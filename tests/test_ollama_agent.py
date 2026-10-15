from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app import create_app
from services.ollama_agent import OllamaAgent, OllamaAgentConfig, _claim_number_tokens, _match_evidence_ids


def draft_payload() -> dict:
    return {
        "answer": "基于分析 #3（全部地块，2024-01-01 至 2024-12-31，1093 条记录）：夏季降水最集中：累计 448.97 mm、雨日 66 天。全期最长连续少雨 8 天。",
        "analysis_id": 3,
        "intent": "precipitation",
        "context": {
            "analysis_id": 3,
            "plot_scope": "全部地块",
            "date_start": "2024-01-01",
            "date_end": "2024-12-31",
            "record_count": 1093,
        },
        "evidence": [
            {"metric": "seasonal_precipitation_mm", "value": 448.97, "unit": "mm", "analysis_id": 3},
            {"metric": "rainy_days", "value": 66, "unit": "day", "analysis_id": 3},
            {"metric": "longest_dry_spell_days", "value": 8, "unit": "day", "analysis_id": 3},
        ],
    }


def config() -> OllamaAgentConfig:
    return OllamaAgentConfig(
        provider="ollama",
        base_url="http://127.0.0.1:11434",
        model="qwen2.5:3b",
        timeout_seconds=0.5,
        fallback="rules",
    )


def test_config_reads_the_exact_agent_environment_variables(monkeypatch):
    for name in (
        "AGRI_AGENT_PROVIDER",
        "AGRI_OLLAMA_BASE_URL",
        "AGRI_OLLAMA_MODEL",
        "AGRI_OLLAMA_TIMEOUT_SECONDS",
        "AGRI_AGENT_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    defaults = OllamaAgentConfig.from_env()
    assert defaults.provider == "ollama"
    assert defaults.model == "qwen2.5:3b"
    assert defaults.timeout_seconds == 60.0

    monkeypatch.setenv("AGRI_AGENT_PROVIDER", "ollama")
    monkeypatch.setenv("AGRI_OLLAMA_BASE_URL", "http://127.0.0.1:22468/")
    monkeypatch.setenv("AGRI_OLLAMA_MODEL", "qwen2.5:3b-test")
    monkeypatch.setenv("AGRI_OLLAMA_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("AGRI_AGENT_FALLBACK", "rules")

    loaded = OllamaAgentConfig.from_env()
    assert loaded.provider == "ollama"
    assert loaded.base_url == "http://127.0.0.1:22468"
    assert loaded.model == "qwen2.5:3b-test"
    assert loaded.timeout_seconds == 7.5
    assert loaded.fallback == "rules"


def test_ollama_success_only_replaces_answer_and_keeps_evidence_immutable():
    polished = "夏季的降水最为集中，累计 448.97 mm、雨日 66 天；全期最长连续少雨为 8 天。"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "qwen2.5:3b"
        assert body["stream"] is False
        assert body["format"]["type"] == "object"
        assert body["format"]["required"] == ["answer", "evidence_ids"]
        return httpx.Response(200, json={"message": {"content": json.dumps({"answer": polished}, ensure_ascii=False)}})

    runtime = OllamaAgent(config(), transport=httpx.MockTransport(handler))
    draft = draft_payload()
    before = copy.deepcopy(draft)
    answer, status = runtime.enhance("哪个季节降水最多？", draft)

    assert answer == "基于分析 #3（全部地块，2024-01-01 至 2024-12-31，1093 条记录）：" + polished
    assert status["mode"] == "local_model"
    assert status["ready"] is True
    assert status["fallback_used"] is False
    assert status["fallback_reason"] is None
    assert draft == before


def test_ollama_humanizes_greeting_without_triggering_number_guard():
    polished = "你好呀，我在呢。想先聊聊降水、温度，还是田间风险？"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][1]["content"])
        assert user_payload["intent"] == "greeting"
        assert user_payload["fallback_evidence"] == []
        return httpx.Response(200, json={"message": {"content": json.dumps({"answer": polished}, ensure_ascii=False)}})

    draft = {
        "answer": "你好呀，我在。你可以把我当作农田气象分析搭档。",
        "intent": "greeting",
        "evidence": [],
    }
    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(handler)).enhance("你好", draft)

    assert answer == polished
    assert status["mode"] == "local_model"
    assert status["fallback_used"] is False


def test_conversation_answer_retries_when_it_is_too_verbose():
    replies = iter(
        [
            "你好，我可以为你详细介绍平台中的温度、降水、太阳辐射、湿度、数据质量、季节变化、风险事件、地块范围和历史分析任务，并且还可以继续说明所有使用方法和操作步骤，让你从头到尾了解全部功能。另外我还能逐项解释每个指标、每张图表、每条风险规则和每一次导出的具体含义。",
            "你好呀。你可以直接问降水、温度、光照或风险，我会结合当前数据回答。",
        ]
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        answer = next(replies)
        return httpx.Response(200, json={"message": {"content": json.dumps({"answer": answer, "evidence_ids": []}, ensure_ascii=False)}})

    draft = {"answer": "你好呀。", "intent": "greeting", "evidence": []}
    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(handler)).enhance("你好", draft)

    assert calls == 2
    assert len(answer) <= 100
    assert status["mode"] == "local_model"
    assert status["fallback_used"] is False


def test_contextual_respond_generates_answer_and_maps_trusted_citations():
    generated = {
        "answer": "当前平均气温是 16.11°C；如果你关心季节差异，我还能继续比较四季。",
        "evidence_ids": ["overview.avg_temperature_c", "not-a-real-id"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][1]["content"])
        assert user_payload["question"] == "现在平均温度怎么样？"
        assert user_payload["analysis_context"]["overview"]["avg_temperature_c"] == 16.11
        assert "overview.avg_temperature_c" in user_payload["evidence_catalog"]
        return httpx.Response(200, json={"message": {"content": json.dumps(generated, ensure_ascii=False)}})

    evidence = {
        "metric": "overview.avg_temperature_c",
        "label": "平均气温",
        "value": 16.11,
        "unit": "°C",
        "source": "当前分析概览",
    }
    draft = {
        "answer": "规则回退摘要。",
        "intent": "temperature",
        "evidence": [],
        "_agent_context": {"overview": {"avg_temperature_c": 16.11}},
        "_evidence_catalog": {"overview.avg_temperature_c": evidence},
    }
    answer, selected, status = OllamaAgent(config(), transport=httpx.MockTransport(handler)).respond(
        "现在平均温度怎么样？",
        draft,
    )

    assert answer == generated["answer"]
    assert selected == [evidence]
    assert status["mode"] == "local_model"
    assert status["fallback_used"] is False


def test_generated_monthly_claims_are_matched_to_server_evidence():
    catalog = {
        "month.2024-07.avg_temperature_c": {"value": 29.49, "unit": "°C"},
        "month.2024-08.avg_temperature_c": {"value": 29.11, "unit": "°C"},
        "month.2024-09.avg_temperature_c": {"value": 28.8, "unit": "°C"},
        "data.source": {"value": "NASA/POWER Source Native Resolution Daily Data"},
        "season.夏季.avg_temperature_c": {"value": 28.94, "unit": "°C"},
    }
    answer = "最热的是7月、8月和9月，平均气温分别为29.49°C、29.11°C和28.80°C；正式数据来自NASA POWER。"

    selected = _match_evidence_ids(
        answer,
        "比较12个月，告诉我最热的三个月，并说明正式数据来源。",
        catalog,
        ["season.夏季.avg_temperature_c"],
    )

    assert selected == [
        "month.2024-07.avg_temperature_c",
        "month.2024-08.avg_temperature_c",
        "month.2024-09.avg_temperature_c",
        "data.source",
    ]


def test_contextual_number_guard_does_not_treat_month_id_as_day_evidence():
    replies = iter(
        [
            {
                "answer": "夏季降水最多，累计1034.96毫米；夏季连续少雨2天，因此触发干旱风险。",
                "evidence_ids": [
                    "season.夏季.total_precipitation_mm",
                    "month.2024-02.total_precipitation_mm",
                    "risk.DRY_SPELL",
                ],
            },
            {
                "answer": "夏季降水最多，累计1034.96毫米；全期检测到28个连续干日事件，最长连续少雨35天，因此存在干旱风险。",
                "evidence_ids": [
                    "season.夏季.total_precipitation_mm",
                    "risk.DRY_SPELL",
                    "overview.longest_dry_spell_days",
                ],
            },
        ]
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {"content": json.dumps(next(replies), ensure_ascii=False)}})

    catalog = {
        "season.夏季.total_precipitation_mm": {"metric": "season.夏季.total_precipitation_mm", "value": 1034.96, "unit": "mm"},
        "month.2024-02.total_precipitation_mm": {"metric": "month.2024-02.total_precipitation_mm", "value": 13.92, "unit": "mm"},
        "season.夏季.longest_dry_spell_days": {"metric": "season.夏季.longest_dry_spell_days", "value": 2, "unit": "day"},
        "overview.longest_dry_spell_days": {"metric": "overview.longest_dry_spell_days", "value": 35, "unit": "day"},
        "risk.DRY_SPELL": {"metric": "risk.DRY_SPELL", "value": 28, "unit": "event", "source": "连续7日 precipitation_mm < 1 mm/day；状态=触发"},
    }
    draft = {
        "answer": "规则回退摘要。",
        "intent": "precipitation",
        "evidence": [],
        "_agent_context": {"seasonal": [{"season": "夏季", "total_precipitation_mm": 1034.96}]},
        "_evidence_catalog": catalog,
    }

    answer, selected, status = OllamaAgent(config(), transport=httpx.MockTransport(handler)).respond(
        "哪个季节降水最多，有干旱风险吗？",
        draft,
    )

    assert calls == 2
    assert "28个" in answer and "35天" in answer
    assert {item["metric"] for item in selected} == {
        "season.夏季.total_precipitation_mm",
        "risk.DRY_SPELL",
        "overview.longest_dry_spell_days",
    }
    assert status["mode"] == "local_model"
    assert status["fallback_used"] is False


def test_chinese_calendar_dates_are_not_treated_as_metric_claims():
    answer = "检测到28个事件，最近发生在2024年11月27日至12月31日，涉及3个地块。"
    assert _claim_number_tokens(answer) == {"28", "3"}


def test_ollama_disconnect_and_timeout_return_rule_answer_with_http_safe_runtime():
    original = draft_payload()

    def disconnected(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(disconnected)).enhance("降水？", original)
    assert answer == original["answer"]
    assert status["mode"] == "rule_fallback"
    assert status["ready"] is False
    assert status["fallback_used"] is True
    assert status["fallback_reason"] == "unreachable"

    def timed_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow model", request=request)

    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(timed_out)).enhance("降水？", original)
    assert answer == original["answer"]
    assert status["mode"] == "rule_fallback"
    assert status["fallback_reason"] == "timeout"


def test_ollama_bad_json_and_new_numbers_force_rule_fallback():
    original = draft_payload()

    def bad_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(bad_json)).enhance("降水？", original)
    assert answer == original["answer"]
    assert status["fallback_reason"] == "invalid_json"

    def invented_number(request: httpx.Request) -> httpx.Response:
        content = json.dumps({"answer": "夏季累计 448.97 mm，并预计额外增加 999 mm。"}, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"content": content}})

    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(invented_number)).enhance("降水？", original)
    assert answer == original["answer"]
    assert status["fallback_reason"] == "unsupported_numbers"

    def omitted_number(request: httpx.Request) -> httpx.Response:
        content = json.dumps({"answer": "夏季降水最集中，累计 448.97 mm、雨日 66 天。"}, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"content": content}})

    answer, status = OllamaAgent(config(), transport=httpx.MockTransport(omitted_number)).enhance("降水？", original)
    assert answer == original["answer"]
    assert status["fallback_reason"] == "unsupported_numbers"


def test_health_reports_model_readiness_and_model_absence():
    def ready(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b"}]})

    status = OllamaAgent(config(), transport=httpx.MockTransport(ready)).health_status()
    assert status["mode"] == "local_model"
    assert status["ready"] is True
    assert status["fallback_used"] is False

    def absent(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:1.5b"}]})

    status = OllamaAgent(config(), transport=httpx.MockTransport(absent)).health_status()
    assert status["mode"] == "rule_fallback"
    assert status["fallback_reason"] == "model_unavailable"


class FakeLocalRuntime:
    runtime = {
        "mode": "local_model",
        "provider": "ollama",
        "model": "qwen2.5:3b",
        "ready": True,
        "fallback_used": False,
        "fallback_reason": None,
        "latency_ms": 4,
    }

    def health_status(self) -> dict:
        return dict(self.runtime)

    def enhance(self, question: str, draft: dict) -> tuple[str, dict]:
        assert draft["evidence"]
        return draft["answer"] + " 表述已由本地模型润色。", dict(self.runtime)


def test_create_app_accepts_runtime_injection_and_persists_runtime_context(tmp_path: Path):
    api = create_app(tmp_path / "injected.db", agent_runtime=FakeLocalRuntime())
    with TestClient(api) as client:
        health = client.get("/api/health").json()
        assert health["agent_runtime"]["mode"] == "local_model"
        response = client.post("/api/agent/query", json={"question": "哪个季节降水最多？", "year": 2024})
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"].endswith("表述已由本地模型润色。")
        assert payload["agent_runtime"] == FakeLocalRuntime.runtime
        assert payload["context"]["agent_runtime"] == FakeLocalRuntime.runtime
        history = client.get("/api/agent/history").json()["items"]
        assert history[0]["context"]["agent_runtime"] == FakeLocalRuntime.runtime


def test_query_returns_http_200_rule_fallback_when_ollama_is_offline(tmp_path: Path):
    def disconnected(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    runtime = OllamaAgent(config(), transport=httpx.MockTransport(disconnected))
    api = create_app(tmp_path / "offline.db", agent_runtime=runtime)
    with TestClient(api) as client:
        response = client.post("/api/agent/query", json={"question": "哪个季节降水最多？", "year": 2024})
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"].startswith("基于分析 #")
        assert len(payload["evidence"]) >= 3
        assert payload["agent_runtime"]["mode"] == "rule_fallback"
        assert payload["agent_runtime"]["fallback_reason"] == "unreachable"
        assert payload["context"]["agent_runtime"] == payload["agent_runtime"]
