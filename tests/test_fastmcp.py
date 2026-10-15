import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from fastmcp import Client

from app import create_app


def test_fastmcp_discovers_three_domain_tools(tmp_path: Path):
    app = create_app(tmp_path / "mcp-tools.db", seed=True, auto_analyze=False)

    async def exercise():
        async with Client(app.state.weather_mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(exercise())
    assert set(tools) == {
        "get_weather_overview",
        "analyze_agricultural_risks",
        "query_weather_observations",
    }
    assert "year" in tools["get_weather_overview"].inputSchema["properties"]
    assert "event_limit" in tools["analyze_agricultural_risks"].inputSchema["properties"]


def test_fastmcp_client_returns_real_analysis_data(tmp_path: Path):
    app = create_app(tmp_path / "mcp-call.db", seed=True, auto_analyze=False)

    async def exercise():
        async with Client(app.state.weather_mcp) as client:
            return await client.call_tool("get_weather_overview", {"year": 2024, "season": "summer"})

    call = asyncio.run(exercise())
    assert call.is_error is False
    assert call.data["tool"] == "get_weather_overview"
    assert call.data["filters"] == {"year": 2024, "season": "夏季"}
    assert call.data["overview"]["record_count"] > 0
    assert call.data["quality"]["total_records"] > 0


def test_fastmcp_http_gateway_exposes_status_tools_call_and_audit(tmp_path: Path):
    app = create_app(tmp_path / "mcp-api.db", seed=True, auto_analyze=False)
    with TestClient(app) as client:
        status = client.get("/api/fastmcp/status")
        assert status.status_code == 200
        assert status.json()["status"] == "connected"
        assert status.json()["tool_count"] == 3
        assert status.json()["fastmcp_version"].startswith("3.")

        tools = client.get("/api/fastmcp/tools")
        assert tools.status_code == 200
        assert len(tools.json()["items"]) == 3

        response = client.post(
            "/api/fastmcp/call",
            json={"tool": "analyze_agricultural_risks", "arguments": {"year": 2024, "season": "summer", "event_limit": 3}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["transport"] == "in-memory"
        assert body["protocol"] == "Model Context Protocol"
        assert body["result"]["events_returned"] <= 3
        assert body["trace_id"].startswith("mcp-")
        assert body["audit_id"] > 0


def test_fastmcp_rejects_unknown_tool(tmp_path: Path):
    app = create_app(tmp_path / "mcp-error.db", seed=True, auto_analyze=False)
    with TestClient(app) as client:
        response = client.post("/api/fastmcp/call", json={"tool": "missing_tool", "arguments": {}})
    assert response.status_code == 422
    assert "未知 FastMCP 工具" in response.json()["detail"]
