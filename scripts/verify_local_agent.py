"""Run a real Ollama + platform closed-loop check and write JSON evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "docs" / "evidence" / "local_model_verification.json"


def request_json(url: str, *, payload: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json; charset=utf-8"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object from {url}, got {type(value).__name__}")
    return value


def run_text(command: list[str]) -> str:
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    output = completed.stdout.decode("utf-8", errors="replace").strip()
    if completed.returncode:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{output}")
    return output


def find_ollama() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("ollama.exe was not found")


def contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--platform-url", default="http://127.0.0.1:8003")
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()

    ollama_url = args.ollama_url.rstrip("/")
    platform_url = args.platform_url.rstrip("/")
    ollama = find_ollama()
    started = time.perf_counter()

    version = request_json(f"{ollama_url}/api/version", timeout=10)
    tags = request_json(f"{ollama_url}/api/tags", timeout=10)
    models = tags.get("models") if isinstance(tags.get("models"), list) else []
    installed_names = {
        value
        for item in models
        if isinstance(item, dict)
        for value in (item.get("name"), item.get("model"))
        if isinstance(value, str)
    }

    smoke_payload = {
        "model": args.model,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "seed": 51, "num_predict": 64},
        "messages": [
            {"role": "system", "content": "你是本地运行状态检查器，只用一句简短中文回答。"},
            {"role": "user", "content": "请确认：本地模型已完成中文推理。"},
        ],
    }
    smoke_started = time.perf_counter()
    smoke = request_json(f"{ollama_url}/api/chat", payload=smoke_payload)
    smoke_latency_ms = round((time.perf_counter() - smoke_started) * 1000)
    smoke_answer = str((smoke.get("message") or {}).get("content") or "").strip()

    running = request_json(f"{ollama_url}/api/ps", timeout=10)
    running_models = running.get("models") if isinstance(running.get("models"), list) else []
    active_model = next(
        (
            item
            for item in running_models
            if isinstance(item, dict) and args.model in {item.get("name"), item.get("model")}
        ),
        {},
    )
    size_vram = int(active_model.get("size_vram") or 0) if isinstance(active_model, dict) else 0

    ollama_ps = run_text([str(ollama), "ps"])
    nvidia_smi = run_text(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )

    health = request_json(f"{platform_url}/api/health", timeout=20)
    source_payload = request_json(f"{platform_url}/api/data/source", timeout=20)
    query_started = time.perf_counter()
    query = request_json(
        f"{platform_url}/api/agent/query",
        payload={"question": "请你自己比较12个月，告诉我最热的三个月，并说明正式数据来自哪里。"},
    )
    query_latency_ms = round((time.perf_counter() - query_started) * 1000)
    health_runtime = health.get("agent_runtime") if isinstance(health.get("agent_runtime"), dict) else {}
    query_runtime = query.get("agent_runtime") if isinstance(query.get("agent_runtime"), dict) else {}
    evidence = query.get("evidence") if isinstance(query.get("evidence"), list) else []
    evidence_metrics = {
        str(item.get("metric"))
        for item in evidence
        if isinstance(item, dict) and item.get("metric") is not None
    }
    source = source_payload.get("source") if isinstance(source_payload.get("source"), dict) else {}
    provenance = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
    normalized_file = provenance.get("normalized_file") if isinstance(provenance.get("normalized_file"), dict) else {}
    answer = str(query.get("answer") or "")
    expected_month_metrics = {
        "month.2024-07.avg_temperature_c",
        "month.2024-08.avg_temperature_c",
        "month.2024-09.avg_temperature_c",
    }

    checks = {
        "ollama_version_detected": bool(version.get("version")),
        "model_installed": args.model in installed_names,
        "direct_chat_returned_chinese": bool(smoke_answer) and contains_chinese(smoke_answer),
        "model_loaded_in_vram": size_vram > 0,
        "ollama_ps_reports_gpu": "GPU" in ollama_ps.upper(),
        "platform_version_1_2": str(health.get("version", "")).startswith("1.2"),
        "official_source_registered": (
            str(source.get("provider", "")).startswith("NASA Langley Research Center POWER Project")
            and str(source.get("landing_page", "")).startswith("https://power.larc.nasa.gov/")
            and normalized_file.get("records") == 1098
        ),
        "official_records_loaded": health.get("records") == 1098,
        "health_local_model_ready": health_runtime.get("mode") == "local_model" and health_runtime.get("ready") is True,
        "query_used_local_model": query_runtime.get("mode") == "local_model",
        "query_did_not_fallback": query_runtime.get("fallback_used") is False,
        "dynamic_monthly_answer": all(token in answer for token in ("7月", "8月", "9月", "NASA")),
        "query_has_monthly_and_source_evidence": expected_month_metrics <= evidence_metrics and "data.source" in evidence_metrics,
        "query_persisted": isinstance(query.get("query_id"), int) and query["query_id"] >= 1,
    }

    report = {
        "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "ollama": {
            "executable": str(ollama),
            "base_url": ollama_url,
            "version": version.get("version"),
            "model": args.model,
            "model_installed": args.model in installed_names,
            "installed_models": sorted(installed_names),
            "direct_chat_answer": smoke_answer,
            "direct_chat_latency_ms": smoke_latency_ms,
            "active_model": active_model,
            "size_vram_bytes": size_vram,
            "ollama_ps": ollama_ps,
        },
        "gpu": {"nvidia_smi_csv": nvidia_smi},
        "platform": {
            "base_url": platform_url,
            "health": health,
            "source": source_payload,
            "query": query,
            "query_latency_ms": query_latency_ms,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
