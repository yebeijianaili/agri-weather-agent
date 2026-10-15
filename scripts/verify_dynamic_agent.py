"""Verify that the local model answers different questions from live analysis context."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "evidence" / "dynamic_agent_verification.json"
BASE_URL = "http://127.0.0.1:8003"
QUESTIONS = [
    "别给我背总览，直接说哪两个月最热，各是多少度？",
    "把9月到11月当成一段，降水和光照情况怎么样？",
    "10月比4月更热还是更冷？请给出这两个月各自的平均气温。",
    "你好，我第一次用。你能像同事一样简单告诉我可以问什么吗？",
]


def request_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Agent response is not a JSON object")
    return value


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    responses: list[dict[str, Any]] = []
    for question in QUESTIONS:
        response = request_json("/api/agent/query", {"question": question})
        responses.append(
            {
                "question": question,
                "answer": response.get("answer"),
                "intent": response.get("intent"),
                "evidence": response.get("evidence", []),
                "agent_runtime": response.get("agent_runtime"),
                "analysis_id": response.get("analysis_id"),
                "query_id": response.get("query_id"),
            }
        )

    metric_sets = [
        {str(item.get("metric")) for item in response["evidence"] if isinstance(item, dict)}
        for response in responses
    ]
    answers = [str(response.get("answer") or "") for response in responses]
    runtimes = [response.get("agent_runtime") or {} for response in responses]
    checks = {
        "all_queries_used_local_model": all(runtime.get("mode") == "local_model" for runtime in runtimes),
        "no_query_fallback": all(runtime.get("fallback_used") is False for runtime in runtimes),
        "answers_are_distinct": len(set(answers)) == len(answers),
        "two_month_ranking_is_grounded": {
            "month.2024-07.avg_temperature_c",
            "month.2024-08.avg_temperature_c",
        } <= metric_sets[0] and all(token in answers[0] for token in ("7月", "8月", "29.49", "29.11")),
        "month_range_is_grounded": {
            "period.09-10-11.total_precipitation_mm",
            "period.09-10-11.avg_solar_radiation_wm2",
        } <= metric_sets[1] and all(token in answers[1] for token in ("419.61", "175.58")),
        "cross_month_comparison_is_grounded": {
            "month.2024-04.avg_temperature_c",
            "month.2024-10.avg_temperature_c",
        } <= metric_sets[2] and all(token in answers[2] for token in ("26.03", "25.63"))
        and ("冷" in answers[2] or "低" in answers[2] or "4月比10月更热" in answers[2]),
        "conversation_is_natural": not metric_sets[3] and len(answers[3]) <= 100 and "可以问" in answers[3],
    }
    report = {
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_url": BASE_URL,
        "responses": responses,
        "checks": checks,
        "passed": all(checks.values()),
    }
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
