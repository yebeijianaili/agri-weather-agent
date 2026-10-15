from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from repositories.database import WeatherRepository
from services.analysis import AnalysisService
from services.exporter import excel_report, markdown_report, seasonal_csv
from services.fastmcp_gateway import FastMCPGateway
from services.importer import ImportValidationError, import_csv
from services.ollama_agent import AgentRuntime, OllamaAgent
from services.sample_data import generate_sample_observations, write_sample_csv
from mcp_server import build_weather_mcp


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "agri_weather.db"
DEFAULT_OFFICIAL_CSV = BASE_DIR / "data" / "nasa_power_shenzhen_2024.csv"
DEFAULT_OFFICIAL_PROVENANCE = BASE_DIR / "data" / "nasa_power_shenzhen_2024.provenance.json"


class AnalysisRequest(BaseModel):
    plot_id: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    season_year: int | None = Field(default=None, ge=1900, le=2101)
    season: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("season")
    @classmethod
    def validate_season(cls, value: str | None) -> str | None:
        return _normalize_season(value)

    @model_validator(mode="after")
    def validate_range(self) -> "AnalysisRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return self

    def filters(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        return {key: value.isoformat() if isinstance(value, date) else value for key, value in data.items()}


class AgentQuery(AnalysisRequest):
    question: str = Field(min_length=1, max_length=500)
    analysis_id: int | None = Field(default=None, ge=1)

    def filters(self) -> dict[str, Any]:
        data = super().filters()
        data.pop("question", None)
        data.pop("analysis_id", None)
        return data


class FastMCPCallRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _normalize_season(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    aliases = {
        "spring": "春季", "summer": "夏季", "autumn": "秋季", "fall": "秋季", "winter": "冬季",
        "春": "春季", "夏": "夏季", "秋": "秋季", "冬": "冬季",
        "春季": "春季", "夏季": "夏季", "秋季": "秋季", "冬季": "冬季",
    }
    normalized = aliases.get(value.strip().lower())
    if not normalized:
        raise ValueError("season 仅支持 spring/summer/autumn/winter 或 春季/夏季/秋季/冬季")
    return normalized


def _filters(
    plot_id: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    season: str | None = None,
    season_year: int | None = None,
) -> dict[str, Any]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date 不能晚于 end_date")
    try:
        normalized_season = _normalize_season(season)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    values = {
        "plot_id": plot_id, "year": year, "season_year": season_year, "season": normalized_season,
        "start_date": start_date, "end_date": end_date,
    }
    return {key: value.isoformat() if isinstance(value, date) else value for key, value in values.items() if value is not None and value != ""}


def _register_user_import_source(
    repository: WeatherRepository,
    result: dict[str, Any],
    *,
    replace: bool,
    previous_record_count: int,
    previous_source: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Make a dataset-changing CSV import the active, truthful source snapshot.

    A no-op import leaves the existing source untouched.  A replacement is fully
    described by the uploaded file; an append to an existing dataset is marked as
    mixed instead of continuing to advertise the previous provider as if it still
    described every row.
    """
    if int(result.get("imported") or 0) <= 0:
        return None

    is_mixed = not replace and previous_record_count > 0
    source_type = "mixed_dataset" if is_mixed else "user_csv"
    filename = str(result.get("filename") or "upload.csv")
    parent_source = None
    if is_mixed and previous_source:
        parent_source = {
            "source_id": previous_source.get("source_id"),
            "source_key": previous_source.get("source_key"),
            "provider": previous_source.get("provider"),
            "dataset_name": previous_source.get("dataset_name"),
        }
    payload = {
        "source_key": f"csv-import-{result['import_id']}-{str(result['sha256'])[:16]}",
        "provider": "混合来源数据集" if is_mixed else "用户上传 CSV",
        "dataset_name": f"既有数据 + {filename}" if is_mixed else filename,
        "landing_page": "/api/imports/history",
        "query_urls": [],
        "retrieved_at": result["created_at"],
        "source_type": source_type,
        "observation_nature": (
            "既有数据与用户上传 CSV 的混合数据；来源属性不能由单一上游提供方代表。"
            if is_mixed
            else "用户上传的 CSV 数据；平台未对文件内容进行外部独立实测核验。"
        ),
        "quality_scope": "入库质量仅评价字段完整性、类型、工程范围、缺测和重复，不代表测量准确性。",
        "import_batch": {
            "import_id": result["import_id"],
            "filename": filename,
            "sha256": result["sha256"],
            "replace": bool(replace),
            "total_rows": result["total_rows"],
            "imported_rows": result["imported"],
            "rejected_rows": result["rejected"],
        },
        "parent_source": parent_source,
        "active_record_count": repository.observation_count(),
    }
    return repository.save_dataset_source(payload)


def create_app(
    db_path: str | Path | None = None,
    *,
    seed: bool = True,
    auto_analyze: bool = True,
    agent_runtime: AgentRuntime | None = None,
) -> FastAPI:
    database_path = Path(db_path or os.getenv("AGRI_DB_PATH", DEFAULT_DB))
    repository = WeatherRepository(database_path)
    repository.initialize()
    if seed and repository.observation_count() == 0:
        if (
            database_path.resolve() == DEFAULT_DB.resolve()
            and DEFAULT_OFFICIAL_CSV.is_file()
            and DEFAULT_OFFICIAL_PROVENANCE.is_file()
        ):
            import_csv(repository, DEFAULT_OFFICIAL_CSV.read_bytes(), filename=DEFAULT_OFFICIAL_CSV.name, replace=True)
            repository.save_dataset_source(json.loads(DEFAULT_OFFICIAL_PROVENANCE.read_text(encoding="utf-8")))
        else:
            samples = generate_sample_observations()
            repository.insert_observations(samples)
            if database_path.resolve() == DEFAULT_DB.resolve():
                write_sample_csv(BASE_DIR / "data" / "sample_weather_2024.csv", samples)
    service = AnalysisService(repository)
    answer_runtime = agent_runtime or OllamaAgent.from_env()
    weather_mcp = build_weather_mcp(repository, service)
    fastmcp_gateway = FastMCPGateway(weather_mcp, repository)
    if auto_analyze and repository.observation_count() and repository.latest_analysis() is None:
        service.run_analysis({})

    api = FastAPI(
        title="农田气象时序分析 Agent API",
        version="1.2.0",
        description="基于正式公开气象数据、本地模型动态问答与可追溯证据的农田气象时序分析 Agent。",
    )
    api.state.repository = repository
    api.state.analysis_service = service
    api.state.agent_runtime = answer_runtime
    api.state.weather_mcp = weather_mcp
    api.state.fastmcp_gateway = fastmcp_gateway
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path == "/api/analysis/run":
            try:
                raw = await request.json()
                filters = {key: value for key, value in raw.items() if key in {"plot_id", "year", "season_year", "season", "start_date", "end_date"}}
            except Exception:
                filters = {}
            failed = repository.save_failed_analysis(
                datetime.now().astimezone().isoformat(timespec="seconds"), filters, "请求参数校验失败"
            )
            return JSONResponse(
                status_code=422,
                content=jsonable_encoder({
                    "detail": {
                        "message": "请求参数校验失败",
                        "analysis_id": failed["analysis_id"],
                        "status": "failed",
                        "errors": exc.errors(),
                    }
                }),
            )
        return JSONResponse(status_code=422, content=jsonable_encoder({"detail": exc.errors()}))

    @api.get("/api/health")
    def health() -> dict[str, Any]:
        runtime_status = answer_runtime.health_status()
        try:
            records = repository.observation_count()
            source = repository.active_dataset_source()
            return {
                "status": "ok", "service": "农田气象时序分析 Agent",
                "version": api.version, "database": "connected", "records": records,
                "data_source": {
                    "source_id": source["source_id"],
                    "provider": source["provider"],
                    "dataset_name": source["dataset_name"],
                    "retrieved_at": source["provenance"].get("retrieved_at"),
                } if source else None,
                "agent_runtime": runtime_status,
            }
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded", "service": "农田气象时序分析 Agent",
                    "database": "disconnected", "error": type(exc).__name__,
                    "agent_runtime": runtime_status,
                },
            )

    @api.get("/api/overview")
    def overview(
        plot_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        season_year: int | None = Query(default=None, ge=1900, le=2101),
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        return service.overview_payload(_filters(plot_id, year, start_date, end_date, season, season_year))

    @api.get("/api/data/source")
    def data_source() -> dict[str, Any]:
        source = repository.active_dataset_source()
        if source is None:
            return {"source": None, "message": "当前数据库未登记外部数据来源"}
        return {"source": source}

    @api.get("/api/fastmcp/status")
    async def fastmcp_status() -> dict[str, Any]:
        return await fastmcp_gateway.status()

    @api.get("/api/fastmcp/tools")
    async def fastmcp_tools() -> dict[str, Any]:
        tools = await fastmcp_gateway.list_tools()
        return {"server": "Agri Weather Analysis MCP", "tool_count": len(tools), "items": tools}

    @api.post("/api/fastmcp/call")
    async def fastmcp_call(request: FastMCPCallRequest) -> dict[str, Any]:
        try:
            return await fastmcp_gateway.call_tool(request.tool, request.arguments)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.get("/api/observations")
    def observations(
        plot_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        season_year: int | None = Query(default=None, ge=1900, le=2101),
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = Query(default=200, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        values = _filters(plot_id, year, start_date, end_date, season, season_year)
        return {
            "total": repository.count_filtered(values),
            "limit": limit,
            "offset": offset,
            "items": repository.fetch_observations(values, limit=limit, offset=offset),
        }

    @api.post("/api/observations/import")
    async def observations_import(file: UploadFile = File(...), replace: bool = False) -> dict[str, Any]:
        if not (file.filename or "").lower().endswith(".csv"):
            raise HTTPException(status_code=415, detail="仅支持 CSV 文件")
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="CSV 文件不能超过 20 MB")
        previous_record_count = repository.observation_count()
        previous_source = repository.active_dataset_source()
        try:
            result = import_csv(repository, content, filename=file.filename or "upload.csv", replace=replace)
        except ImportValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "import_id": exc.batch.get("import_id") if exc.batch else None},
            ) from exc
        try:
            source = _register_user_import_source(
                repository,
                result,
                replace=replace,
                previous_record_count=previous_record_count,
                previous_source=previous_source,
            )
        except Exception:
            # The observation set has already changed.  Prefer an explicit
            # source-less state over continuing to advertise a stale provider.
            repository.deactivate_dataset_sources()
            raise
        if source is not None:
            result["data_source"] = source
        result["quality"] = service.quality_report(repository.fetch_observations({}))
        result["message"] = f"导入完成：新增 {result['imported']} 条，更新 {result['updated']} 条，拒绝 {result['rejected']} 条。"
        return result

    @api.get("/api/imports/history")
    def import_history(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = repository.list_import_batches(limit=limit, offset=offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @api.post("/api/analysis/run")
    def run_analysis(request: AnalysisRequest) -> dict[str, Any]:
        values = request.filters()
        try:
            return service.run_analysis(values)
        except ValueError as exc:
            failed = repository.save_failed_analysis(
                datetime.now().astimezone().isoformat(timespec="seconds"), values, str(exc)
            )
            raise HTTPException(
                status_code=404,
                detail={"message": str(exc), "analysis_id": failed["analysis_id"], "status": "failed"},
            ) from exc

    @api.get("/api/analysis/latest")
    def latest_analysis() -> dict[str, Any]:
        analysis = repository.latest_analysis()
        if not analysis:
            raise HTTPException(status_code=404, detail="暂无分析记录")
        return analysis

    @api.get("/api/analysis/history")
    def analysis_history(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = repository.list_analyses(limit=limit, offset=offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @api.get("/api/analysis/{analysis_id}")
    def analysis_detail(analysis_id: int) -> dict[str, Any]:
        analysis = repository.get_analysis(analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="分析任务不存在")
        return analysis

    @api.post("/api/agent/query")
    def agent_query(request: AgentQuery) -> dict[str, Any]:
        analysis = repository.get_analysis(request.analysis_id) if request.analysis_id else None
        if request.analysis_id is not None and analysis is None:
            raise HTTPException(status_code=404, detail="分析任务不存在")
        if analysis is not None and analysis["status"] != "completed":
            raise HTTPException(status_code=409, detail="该分析任务未成功完成，请选择 completed 任务后问答")
        if analysis is None and request.filters():
            try:
                analysis = service.run_analysis(request.filters())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        if analysis is None:
            analysis = repository.latest_analysis()
        if analysis is None:
            raise HTTPException(status_code=404, detail="暂无可问答的分析结果")
        response = service.answer_question(request.question, analysis)
        contextual_responder = getattr(answer_runtime, "respond", None)
        if callable(contextual_responder):
            enhanced_answer, selected_evidence, runtime_status = contextual_responder(request.question, response)
        else:
            enhanced_answer, runtime_status = answer_runtime.enhance(request.question, response)
            selected_evidence = response.get("evidence", [])
        response = {
            key: value
            for key, value in response.items()
            if not key.startswith("_agent_") and key != "_evidence_catalog"
        }
        response = {
            **response,
            "answer": enhanced_answer,
            "evidence": selected_evidence,
            "agent_runtime": runtime_status,
        }
        response["context"] = {**response.get("context", {}), "agent_runtime": runtime_status}
        response.update(repository.save_agent_query(analysis["analysis_id"], request.question, response))
        return response

    @api.get("/api/agent/history")
    def agent_history(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = repository.list_agent_queries(limit=limit, offset=offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def resolve_analysis(analysis_id: int | None, values: dict[str, Any]) -> dict[str, Any]:
        if analysis_id:
            analysis = repository.get_analysis(analysis_id)
            if not analysis:
                raise HTTPException(status_code=404, detail="分析任务不存在")
            if analysis["status"] != "completed":
                raise HTTPException(status_code=409, detail="该分析任务未成功完成，暂无可导出结果")
            return analysis
        if values:
            try:
                return service.preview_analysis(values)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        analysis = repository.latest_analysis()
        if not analysis:
            raise HTTPException(status_code=404, detail="暂无分析结果")
        return analysis

    @api.get("/api/export/seasonal.csv")
    def export_seasonal(
        analysis_id: int | None = Query(default=None, ge=1),
        plot_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        season_year: int | None = Query(default=None, ge=1900, le=2101),
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> PlainTextResponse:
        analysis = resolve_analysis(analysis_id, _filters(plot_id, year, start_date, end_date, season, season_year))
        audit = repository.save_audit_event(
            "export_seasonal_csv", str(analysis["analysis_id"]), {"filters": analysis.get("filters", {})}
        )
        return PlainTextResponse(
            seasonal_csv(analysis),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=seasonal-analysis-{analysis['analysis_id']}.csv",
                "X-Audit-Id": str(audit["audit_id"]),
            },
        )

    @api.get("/api/export/report.md")
    def export_report(
        analysis_id: int | None = Query(default=None, ge=1),
        plot_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        season_year: int | None = Query(default=None, ge=1900, le=2101),
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> PlainTextResponse:
        analysis = resolve_analysis(analysis_id, _filters(plot_id, year, start_date, end_date, season, season_year))
        audit = repository.save_audit_event(
            "export_markdown_report", str(analysis["analysis_id"]), {"filters": analysis.get("filters", {})}
        )
        return PlainTextResponse(
            markdown_report(analysis),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=agri-weather-report-{analysis['analysis_id']}.md",
                "X-Audit-Id": str(audit["audit_id"]),
            },
        )

    @api.get("/api/export/report.xlsx")
    def export_excel_report(
        analysis_id: int | None = Query(default=None, ge=1),
        plot_id: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        season_year: int | None = Query(default=None, ge=1900, le=2101),
        season: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Response:
        analysis = resolve_analysis(analysis_id, _filters(plot_id, year, start_date, end_date, season, season_year))
        audit = repository.save_audit_event(
            "export_excel_report", str(analysis["analysis_id"]), {"filters": analysis.get("filters", {})}
        )
        return Response(
            excel_report(analysis),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename=agri-weather-report-{analysis['analysis_id']}.xlsx",
                "X-Audit-Id": str(audit["audit_id"]),
            },
        )

    @api.get("/api/audit/events")
    def audit_events(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        items, total = repository.list_audit_events(limit=limit, offset=offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        api.mount("/static", StaticFiles(directory=static_dir), name="static")

        @api.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")
    else:
        @api.get("/", include_in_schema=False)
        def index_fallback() -> dict[str, str]:
            return {"service": "农田气象时序分析 Agent", "docs": "/docs"}

    return api


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8003, reload=False)
