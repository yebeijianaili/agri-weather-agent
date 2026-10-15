from __future__ import annotations

import time
import uuid
from importlib.metadata import version
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from repositories.database import WeatherRepository


class FastMCPGateway:
    """Calls the FastMCP server through FastMCP's real in-memory client transport."""

    def __init__(self, mcp: FastMCP, repository: WeatherRepository | None = None):
        self.mcp = mcp
        self.repository = repository

    async def list_tools(self) -> list[dict[str, Any]]:
        async with Client(self.mcp, name="agri-weather-web-gateway") as client:
            tools = await client.list_tools()
        return [tool.model_dump(mode="json", by_alias=True) for tool in tools]

    async def status(self) -> dict[str, Any]:
        started = time.perf_counter()
        tools = await self.list_tools()
        return {
            "status": "connected",
            "server": "Agri Weather Analysis MCP",
            "fastmcp_version": version("fastmcp"),
            "transport": "in-memory",
            "protocol": "Model Context Protocol",
            "tool_count": len(tools),
            "tool_names": [tool["name"] for tool in tools],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        trace_id = f"mcp-{uuid.uuid4().hex[:12]}"
        async with Client(self.mcp, name="agri-weather-web-gateway") as client:
            discovered = {tool.name for tool in await client.list_tools()}
            if name not in discovered:
                raise ValueError(f"未知 FastMCP 工具：{name}")
            try:
                result = await client.call_tool(name, arguments or {})
            except ToolError as exc:
                raise ValueError(str(exc)) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        payload = result.data if result.data is not None else result.structured_content
        response = {
            "trace_id": trace_id,
            "status": "success" if not result.is_error else "error",
            "server": "Agri Weather Analysis MCP",
            "protocol": "Model Context Protocol",
            "transport": "in-memory",
            "fastmcp_version": version("fastmcp"),
            "tool": name,
            "arguments": arguments or {},
            "elapsed_ms": elapsed_ms,
            "result": payload,
        }
        if self.repository is not None:
            audit = self.repository.save_audit_event(
                "fastmcp_tool_call",
                trace_id,
                {"tool": name, "arguments": arguments or {}, "elapsed_ms": elapsed_ms, "status": response["status"]},
            )
            response["audit_id"] = audit["audit_id"]
        return response
