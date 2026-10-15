from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from services.ollama_agent import OllamaAgent, OllamaAgentConfig  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path):
    runtime = OllamaAgent(OllamaAgentConfig(provider="rules"))
    api = create_app(tmp_path / "test.db", seed=True, auto_analyze=True, agent_runtime=runtime)
    with TestClient(api) as test_client:
        yield test_client
