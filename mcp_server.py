from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from repositories.database import WeatherRepository
from services.analysis import AnalysisService


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "agri_weather.db"
VALID_SEASONS = {"春季", "夏季", "秋季", "冬季"}
SEASON_ALIASES = {
    "spring": "春季", "summer": "夏季", "autumn": "秋季", "fall": "秋季", "winter": "冬季",
    "春": "春季", "夏": "夏季", "秋": "秋季", "冬": "冬季",
}


def _normalise_filters(
    *,
    plot_id: str | None = None,
    year: int | None = None,
    season: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if plot_id and plot_id.strip():
        filters["plot_id"] = plot_id.strip()
    if year is not None:
        if not 1900 <= year <= 2100:
            raise ValueError("year 必须在 1900 到 2100 之间")
        filters["year"] = year
    if season and season.strip():
        raw = season.strip()
        normalised = SEASON_ALIASES.get(raw.casefold(), raw)
        if normalised not in VALID_SEASONS:
            raise ValueError("season 仅支持春季、夏季、秋季、冬季或对应英文")
        filters["season"] = normalised
    parsed_dates: dict[str, str] = {}
    for key, value in (("start_date", start_date), ("end_date", end_date)):
        if value and value.strip():
            try:
                parsed_dates[key] = date.fromisoformat(value.strip()).isoformat()
            except ValueError as exc:
                raise ValueError(f"{key} 必须使用 YYYY-MM-DD 格式") from exc
    if parsed_dates.get("start_date") and parsed_dates.get("end_date"):
        if parsed_dates["start_date"] > parsed_dates["end_date"]:
            raise ValueError("start_date 不能晚于 end_date")
    filters.update(parsed_dates)
    return filters


def build_weather_mcp(
    repository: WeatherRepository,
    service: AnalysisService | None = None,
) -> FastMCP:
    """Build the real MCP tool layer over the existing repository and analysis services."""
    analysis_service = service or AnalysisService(repository)
    mcp = FastMCP(
        name="Agri Weather Analysis MCP",
        instructions=(
            "面向农田气象数据的可追溯工具服务。数据来自当前 SQLite 数据库；"
            "统计口径与 Web 平台 AnalysisService 保持一致。"
        ),
    )

    @mcp.tool(
        name="get_weather_overview",
        title="获取农业气象概览",
        tags={"weather", "overview", "agriculture"},
    )
    def get_weather_overview(
        plot_id: str | None = None,
        year: int | None = None,
        season: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """按地块、年份、季节或日期范围返回气象统计与入库质量摘要。"""
        filters = _normalise_filters(
            plot_id=plot_id, year=year, season=season,
            start_date=start_date, end_date=end_date,
        )
        rows = repository.fetch_observations(filters)
        if not rows:
            raise ValueError("当前筛选范围没有气象记录")
        result = analysis_service.build_result(rows, filters)
        source = repository.active_dataset_source()
        provenance = (source or {}).get("provenance", {})
        return {
            "tool": "get_weather_overview",
            "filters": filters,
            "overview": result["overview"],
            "quality": result["quality"],
            "source": {
                "provider": (source or {}).get("provider"),
                "dataset_name": (source or {}).get("dataset_name"),
                "retrieved_at": provenance.get("retrieved_at"),
                "spatial_note": provenance.get("spatial_note"),
            },
        }

    @mcp.tool(
        name="analyze_agricultural_risks",
        title="分析农业气象风险",
        tags={"weather", "risk", "agriculture"},
    )
    def analyze_agricultural_risks(
        plot_id: str | None = None,
        year: int | None = None,
        season: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        event_limit: int = 12,
    ) -> dict[str, Any]:
        """运行项目统一风险规则，返回风险汇总及可追溯事件证据。"""
        if not 1 <= event_limit <= 50:
            raise ValueError("event_limit 必须在 1 到 50 之间")
        filters = _normalise_filters(
            plot_id=plot_id, year=year, season=season,
            start_date=start_date, end_date=end_date,
        )
        rows = repository.fetch_observations(filters)
        if not rows:
            raise ValueError("当前筛选范围没有气象记录")
        result = analysis_service.build_result(rows, filters)
        triggered = [item for item in result["risks"] if item.get("status") == "触发"]
        events = result["risk_events"]
        return {
            "tool": "analyze_agricultural_risks",
            "filters": filters,
            "summary": {
                "records_analyzed": result["overview"]["record_count"],
                "triggered_risk_types": len(triggered),
                "risk_event_count": len(events),
                "high_severity_types": sum(1 for item in triggered if item.get("severity") == "高"),
            },
            "triggered_risks": [
                {key: item.get(key) for key in (
                    "code", "name", "severity", "event_count", "threshold", "evidence", "recommendation"
                )}
                for item in triggered
            ],
            "events": events[:event_limit],
            "events_returned": min(len(events), event_limit),
            "methodology": {
                "threshold_version": result["methodology"]["risk_threshold_version"],
                "note": result["methodology"]["risk_note"],
            },
        }

    @mcp.tool(
        name="query_weather_observations",
        title="查询气象观测记录",
        tags={"weather", "query", "data"},
    )
    def query_weather_observations(
        plot_id: str | None = None,
        year: int | None = None,
        season: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页查询当前 SQLite 数据库中的标准化日值记录。"""
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if offset < 0:
            raise ValueError("offset 不能小于 0")
        filters = _normalise_filters(
            plot_id=plot_id, year=year, season=season,
            start_date=start_date, end_date=end_date,
        )
        return {
            "tool": "query_weather_observations",
            "filters": filters,
            "total": repository.count_filtered(filters),
            "limit": limit,
            "offset": offset,
            "items": repository.fetch_observations(filters, limit=limit, offset=offset),
            "schema": {
                "temperature_c": "°C",
                "precipitation_mm": "mm/day",
                "solar_radiation_wm2": "W/m² daily mean",
                "humidity_pct": "%",
            },
        }

    return mcp


def create_default_mcp() -> FastMCP:
    database_path = Path(os.getenv("AGRI_DB_PATH", DEFAULT_DB))
    repository = WeatherRepository(database_path)
    repository.initialize()
    return build_weather_mcp(repository, AnalysisService(repository))


mcp = create_default_mcp()


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host=os.getenv("AGRI_MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("AGRI_MCP_PORT", "8013")),
        path=os.getenv("AGRI_MCP_PATH", "/mcp"),
    )
