from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from domain.models import Observation


class WeatherRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    plot_id TEXT NOT NULL,
                    temperature_c REAL,
                    precipitation_mm REAL,
                    solar_radiation_wm2 REAL,
                    humidity_pct REAL,
                    quality_flag TEXT NOT NULL DEFAULT 'valid',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(timestamp, plot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_observations_timestamp
                    ON observations(timestamp);
                CREATE INDEX IF NOT EXISTS idx_observations_plot_timestamp
                    ON observations(plot_id, timestamp);

                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_created_at
                    ON analysis_runs(created_at DESC);

                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    replace_mode INTEGER NOT NULL DEFAULT 0,
                    total_rows INTEGER NOT NULL,
                    valid_rows INTEGER NOT NULL,
                    inserted_rows INTEGER NOT NULL,
                    rejected_rows INTEGER NOT NULL,
                    warning_rows INTEGER NOT NULL,
                    exact_duplicates INTEGER NOT NULL DEFAULT 0,
                    conflict_duplicates INTEGER NOT NULL DEFAULT 0,
                    reason_counts_json TEXT NOT NULL,
                    errors_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_import_batches_created_at
                    ON import_batches(created_at DESC);

                CREATE TABLE IF NOT EXISTS dataset_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source_key TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    landing_page TEXT NOT NULL,
                    query_urls_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_sources_active
                    ON dataset_sources(active, id DESC);

                CREATE TABLE IF NOT EXISTS agent_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    analysis_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_queries_created_at
                    ON agent_queries(created_at DESC);

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    reference_id TEXT,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
                    ON audit_events(created_at DESC);
                """
            )

    def observation_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    def clear_observations(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM observations")

    @staticmethod
    def _insert_observations(
        conn: sqlite3.Connection,
        observations: Iterable[Observation],
        *,
        update: bool = False,
    ) -> tuple[int, int]:
        inserted = 0
        updated = 0
        sql_insert = """
            INSERT OR IGNORE INTO observations
            (timestamp, plot_id, temperature_c, precipitation_mm,
             solar_radiation_wm2, humidity_pct, quality_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        sql_update = """
            INSERT INTO observations
            (timestamp, plot_id, temperature_c, precipitation_mm,
             solar_radiation_wm2, humidity_pct, quality_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(timestamp, plot_id) DO UPDATE SET
                temperature_c=excluded.temperature_c,
                precipitation_mm=excluded.precipitation_mm,
                solar_radiation_wm2=excluded.solar_radiation_wm2,
                humidity_pct=excluded.humidity_pct,
                quality_flag=excluded.quality_flag,
                updated_at=CURRENT_TIMESTAMP
        """
        for item in observations:
            values = (
                item.timestamp,
                item.plot_id,
                item.temperature_c,
                item.precipitation_mm,
                item.solar_radiation_wm2,
                item.humidity_pct,
                item.quality_flag,
            )
            existed = conn.execute(
                "SELECT 1 FROM observations WHERE timestamp=? AND plot_id=?",
                (item.timestamp, item.plot_id),
            ).fetchone() is not None
            if update:
                conn.execute(sql_update, values)
                updated += int(existed)
                inserted += int(not existed)
            else:
                cursor = conn.execute(sql_insert, values)
                inserted += int(cursor.rowcount > 0)
                updated += int(existed)
        return inserted, updated

    def insert_observations(self, observations: Iterable[Observation], *, update: bool = False) -> tuple[int, int]:
        with self.connect() as conn:
            return self._insert_observations(conn, observations, update=update)

    def replace_observations(self, observations: Iterable[Observation]) -> tuple[int, int]:
        """Replace the observation snapshot atomically.

        The delete and every insert share one SQLite transaction.  Any write
        failure therefore rolls the delete back and leaves the previous
        snapshot readable.
        """
        with self.connect() as conn:
            conn.execute("DELETE FROM observations")
            return self._insert_observations(conn, observations, update=False)

    @staticmethod
    def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if filters.get("plot_id"):
            conditions.append("plot_id = ?")
            params.append(filters["plot_id"])
        if filters.get("year"):
            conditions.append("substr(timestamp, 1, 4) = ?")
            params.append(str(filters["year"]))
        if filters.get("season_year"):
            conditions.append("(CAST(substr(timestamp, 1, 4) AS INTEGER) + CASE WHEN substr(timestamp, 6, 2) = '12' THEN 1 ELSE 0 END) = ?")
            params.append(int(filters["season_year"]))
        if filters.get("season"):
            months = {
                "春季": ("03", "04", "05"),
                "夏季": ("06", "07", "08"),
                "秋季": ("09", "10", "11"),
                "冬季": ("12", "01", "02"),
            }[filters["season"]]
            conditions.append("substr(timestamp, 6, 2) IN (?, ?, ?)")
            params.extend(months)
        if filters.get("start_date"):
            conditions.append("timestamp >= ?")
            params.append(str(filters["start_date"]))
        if filters.get("end_date"):
            conditions.append("timestamp <= ?")
            params.append(str(filters["end_date"]))
        return (" WHERE " + " AND ".join(conditions)) if conditions else "", params

    def fetch_observations(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = self._where(filters or {})
        sql = "SELECT id, timestamp, plot_id, temperature_c, precipitation_mm, solar_radiation_wm2, humidity_pct, quality_flag FROM observations" + where + " ORDER BY timestamp, plot_id"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def count_filtered(self, filters: dict[str, Any] | None = None) -> int:
        where, params = self._where(filters or {})
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM observations" + where, params).fetchone()[0])

    def dataset_metadata(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        where, params = self._where(filters or {})
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, COUNT(DISTINCT plot_id) AS plot_count, MIN(timestamp) AS date_start, MAX(timestamp) AS date_end FROM observations" + where,
                params,
            ).fetchone()
            plots = [r[0] for r in conn.execute("SELECT DISTINCT plot_id FROM observations" + where + " ORDER BY plot_id", params).fetchall()]
            years = [int(r[0]) for r in conn.execute("SELECT DISTINCT substr(timestamp, 1, 4) AS year FROM observations" + where + " ORDER BY year", params).fetchall() if r[0]]
        latest_import = self.latest_import_batch()
        latest_analysis = self.latest_analysis()
        source = self.active_dataset_source()
        return {
            "total_records": int(row["total"]),
            "plot_count": int(row["plot_count"]),
            "date_start": row["date_start"],
            "date_end": row["date_end"],
            "plots": plots,
            "years": years,
            "last_import": latest_import,
            "last_analysis": {
                "analysis_id": latest_analysis["analysis_id"],
                "created_at": latest_analysis["created_at"],
                "status": latest_analysis["status"],
            } if latest_analysis else None,
            "source": source,
        }

    def save_dataset_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        created_at = str(payload.get("retrieved_at") or payload.get("created_at") or "")
        query_urls = payload.get("query_urls") if isinstance(payload.get("query_urls"), list) else []
        with self.connect() as conn:
            conn.execute("UPDATE dataset_sources SET active=0")
            conn.execute(
                """
                INSERT INTO dataset_sources(
                    created_at, source_key, provider, dataset_name, landing_page,
                    query_urls_json, provenance_json, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(source_key) DO UPDATE SET
                    created_at=excluded.created_at,
                    provider=excluded.provider,
                    dataset_name=excluded.dataset_name,
                    landing_page=excluded.landing_page,
                    query_urls_json=excluded.query_urls_json,
                    provenance_json=excluded.provenance_json,
                    active=1
                """,
                (
                    created_at,
                    str(payload["source_key"]),
                    str(payload["provider"]),
                    str(payload["dataset_name"]),
                    str(payload["landing_page"]),
                    json.dumps(query_urls, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            row = conn.execute("SELECT * FROM dataset_sources WHERE source_key=?", (payload["source_key"],)).fetchone()
        return self._decode_dataset_source(row)

    @staticmethod
    def _decode_dataset_source(row: sqlite3.Row) -> dict[str, Any]:
        provenance = json.loads(row["provenance_json"])
        return {
            "source_id": int(row["id"]),
            "created_at": row["created_at"],
            "source_key": row["source_key"],
            "provider": row["provider"],
            "dataset_name": row["dataset_name"],
            "landing_page": row["landing_page"],
            "query_urls": json.loads(row["query_urls_json"]),
            "active": bool(row["active"]),
            "provenance": provenance,
        }

    def active_dataset_source(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM dataset_sources WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
        return self._decode_dataset_source(row) if row else None

    def deactivate_dataset_sources(self) -> None:
        """Remove an active-source claim without deleting its audit history."""
        with self.connect() as conn:
            conn.execute("UPDATE dataset_sources SET active=0")

    def save_import_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO import_batches(
                    created_at, filename, sha256, replace_mode, total_rows,
                    valid_rows, inserted_rows, rejected_rows, warning_rows,
                    exact_duplicates, conflict_duplicates, reason_counts_json,
                    errors_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["created_at"], payload["filename"], payload["sha256"], int(payload.get("replace", False)),
                    payload["total_rows"], payload["valid_rows"], payload["imported"], payload["rejected"],
                    payload["warning_rows"], payload["exact_duplicates"], payload["conflict_duplicates"],
                    json.dumps(payload["reason_counts"], ensure_ascii=False),
                    json.dumps(payload["errors"], ensure_ascii=False),
                    json.dumps(payload["warnings"], ensure_ascii=False),
                ),
            )
            import_id = int(cursor.lastrowid)
        return {"import_id": import_id, **payload}

    @staticmethod
    def _decode_import(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "import_id": int(row["id"]),
            "created_at": row["created_at"],
            "filename": row["filename"],
            "sha256": row["sha256"],
            "replace": bool(row["replace_mode"]),
            "total_rows": int(row["total_rows"]),
            "valid_rows": int(row["valid_rows"]),
            "imported": int(row["inserted_rows"]),
            "rejected": int(row["rejected_rows"]),
            "warning_rows": int(row["warning_rows"]),
            "exact_duplicates": int(row["exact_duplicates"]),
            "conflict_duplicates": int(row["conflict_duplicates"]),
            "reason_counts": json.loads(row["reason_counts_json"]),
            "errors": json.loads(row["errors_json"]),
            "warnings": json.loads(row["warnings_json"]),
        }

    def latest_import_batch(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM import_batches ORDER BY id DESC LIMIT 1").fetchone()
        return self._decode_import(row) if row else None

    def list_import_batches(self, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0])
            rows = conn.execute("SELECT * FROM import_batches ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._decode_import(row) for row in rows], total

    def save_analysis(self, created_at: str, filters: dict[str, Any], summary: str, result: dict[str, Any]) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO analysis_runs(created_at, status, filters_json, summary, result_json) VALUES (?, 'completed', ?, ?, ?)",
                (created_at, json.dumps(filters, ensure_ascii=False), summary, json.dumps(result, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
        result = json.loads(row["result_json"])
        overview = result.get("overview", {})
        return {
            "analysis_id": int(row["id"]),
            "created_at": row["created_at"],
            "finished_at": row["created_at"],
            "status": row["status"],
            "filters": json.loads(row["filters_json"]),
            "summary": row["summary"],
            "record_count": overview.get("record_count", 0),
            "risk_count": len(result.get("risk_events", [])),
            "result": result,
        }

    def save_failed_analysis(self, created_at: str, filters: dict[str, Any], error: str) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO analysis_runs(created_at, status, filters_json, summary, result_json) VALUES (?, 'failed', ?, ?, '{}')",
                (created_at, json.dumps(filters, ensure_ascii=False), error),
            )
            analysis_id = int(cursor.lastrowid)
        return {
            "analysis_id": analysis_id,
            "created_at": created_at,
            "finished_at": created_at,
            "status": "failed",
            "filters": filters,
            "summary": error,
            "record_count": 0,
            "risk_count": 0,
            "result": {},
        }

    def get_analysis(self, analysis_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM analysis_runs WHERE id=?", (analysis_id,)).fetchone()
        return self._decode_run(row) if row else None

    def latest_analysis(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM analysis_runs WHERE status='completed' ORDER BY id DESC LIMIT 1").fetchone()
        return self._decode_run(row) if row else None

    def list_analyses(self, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0])
            rows = conn.execute("SELECT * FROM analysis_runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [self._decode_run(row) for row in rows], total

    def save_agent_query(self, analysis_id: int, question: str, response: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime

        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO agent_queries(created_at, analysis_id, question, answer, context_json, evidence_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    created_at,
                    analysis_id,
                    question,
                    response["answer"],
                    json.dumps(response.get("context", {}), ensure_ascii=False),
                    json.dumps(response.get("evidence", []), ensure_ascii=False),
                ),
            )
            query_id = int(cursor.lastrowid)
        return {"query_id": query_id, "queried_at": created_at}

    def list_agent_queries(self, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM agent_queries").fetchone()[0])
            rows = conn.execute("SELECT * FROM agent_queries ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        items = [
            {
                "query_id": int(row["id"]),
                "created_at": row["created_at"],
                "analysis_id": int(row["analysis_id"]),
                "question": row["question"],
                "answer": row["answer"],
                "context": json.loads(row["context_json"]),
                "evidence": json.loads(row["evidence_json"]),
            }
            for row in rows
        ]
        return items, total

    def save_audit_event(self, event_type: str, reference_id: str | None, details: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime

        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO audit_events(created_at, event_type, reference_id, details_json) VALUES (?, ?, ?, ?)",
                (created_at, event_type, reference_id, json.dumps(details, ensure_ascii=False)),
            )
            audit_id = int(cursor.lastrowid)
        return {"audit_id": audit_id, "created_at": created_at, "event_type": event_type, "reference_id": reference_id}

    def list_audit_events(self, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
            rows = conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [
            {
                "audit_id": int(row["id"]), "created_at": row["created_at"],
                "event_type": row["event_type"], "reference_id": row["reference_id"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ], total
