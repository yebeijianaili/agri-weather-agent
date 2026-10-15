from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from domain.models import Observation, parse_timestamp
from repositories.database import WeatherRepository


ALIASES = {
    "timestamp": ("timestamp", "date", "日期", "时间"),
    "plot_id": ("plot_id", "plot", "地块", "地块编号"),
    "temperature_c": ("temperature_c", "temperature", "温度", "气温"),
    "precipitation_mm": ("precipitation_mm", "precipitation", "rain", "降水", "降水量"),
    "solar_radiation_wm2": ("solar_radiation_wm2", "solar", "radiation", "光照", "太阳辐射"),
    "humidity_pct": ("humidity_pct", "humidity", "湿度", "相对湿度"),
    "quality_flag": ("quality_flag", "quality", "质量标记"),
}
REQUIRED_FIELDS = ("timestamp", "plot_id", "temperature_c", "precipitation_mm", "solar_radiation_wm2")
BOUNDS = {
    "temperature_c": (-50.0, 60.0),
    "precipitation_mm": (0.0, 500.0),
    "solar_radiation_wm2": (0.0, 1400.0),
    "humidity_pct": (0.0, 100.0),
}


class ImportValidationError(ValueError):
    def __init__(self, message: str, batch: dict[str, Any] | None = None):
        super().__init__(message)
        self.batch = batch


def _decode(content: bytes) -> str:
    if not content:
        raise ValueError("CSV 文件为空")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return content.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV 编码需为 UTF-8/UTF-8-BOM/GB18030") from exc


def _find(row: dict[str, str], field: str) -> str:
    for alias in ALIASES[field]:
        if alias in row:
            return (row.get(alias) or "").strip()
    return ""


def _number(value: str, field: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field} 不是有效数字") from exc


def _same(left: Observation | dict[str, Any], right: Observation | dict[str, Any]) -> bool:
    fields = ("temperature_c", "precipitation_mm", "solar_radiation_wm2", "humidity_pct")
    for field in fields:
        left_value = getattr(left, field) if isinstance(left, Observation) else left[field]
        right_value = getattr(right, field) if isinstance(right, Observation) else right[field]
        if left_value != right_value:
            return False
    return True


def parse_csv(content: bytes) -> tuple[list[tuple[int, Observation]], list[dict[str, Any]], list[dict[str, Any]], int]:
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 缺少表头")
    headers = [(value or "").strip() for value in reader.fieldnames]
    duplicate_headers = sorted({value for value in headers if value and headers.count(value) > 1})
    if duplicate_headers:
        raise ValueError("CSV 存在重复列名: " + ", ".join(duplicate_headers))
    missing = [field for field in REQUIRED_FIELDS if not any(alias in headers for alias in ALIASES[field])]
    if missing:
        raise ValueError("CSV 缺少必填列: " + ", ".join(missing))

    known = {alias for aliases in ALIASES.values() for alias in aliases}
    unknown = [header for header in headers if header and header not in known]
    warnings: list[dict[str, Any]] = []
    if unknown:
        warnings.append({"line": None, "code": "unknown_columns", "message": "未知列未参与计算: " + ", ".join(unknown)})

    parsed: list[tuple[int, Observation]] = []
    errors: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], tuple[int, Observation]] = {}
    total_rows = 0
    for line_number, raw in enumerate(reader, start=2):
        total_rows += 1
        row = {
            str(key).strip(): (";".join(str(item) for item in value) if isinstance(value, list) else str(value or "")).strip()
            for key, value in raw.items()
            if key is not None
        }
        try:
            timestamp = parse_timestamp(_find(row, "timestamp"))
            plot_id = _find(row, "plot_id")
            if not plot_id:
                raise ValueError("plot_id 不能为空")
            values = {
                "temperature_c": _number(_find(row, "temperature_c"), "temperature_c"),
                "precipitation_mm": _number(_find(row, "precipitation_mm"), "precipitation_mm"),
                "solar_radiation_wm2": _number(_find(row, "solar_radiation_wm2"), "solar_radiation_wm2"),
                "humidity_pct": _number(_find(row, "humidity_pct"), "humidity_pct"),
            }
            for required_value in ("temperature_c", "precipitation_mm", "solar_radiation_wm2"):
                if values[required_value] is None:
                    raise ValueError(f"{required_value} 不能为空")
            for field, value in values.items():
                if value is not None and not (BOUNDS[field][0] <= value <= BOUNDS[field][1]):
                    raise ValueError(f"{field} 超出范围 {BOUNDS[field][0]:g}..{BOUNDS[field][1]:g}")
            supplied_flag = _find(row, "quality_flag").lower()
            quality = supplied_flag if supplied_flag in {"valid", "observed", "corrected", "imputed", "suspect"} else "observed"
            missing_fields = [field for field, value in values.items() if value is None]
            if missing_fields:
                warnings.append({"line": line_number, "code": "missing_value", "message": "缺测字段: " + ", ".join(missing_fields)})
            observation = Observation(timestamp=timestamp, plot_id=plot_id, quality_flag=quality, **values)
            key = (timestamp, plot_id)
            if key in seen:
                first_line, first = seen[key]
                if _same(first, observation):
                    warnings.append({"line": line_number, "code": "duplicate_exact", "message": f"与第 {first_line} 行完全重复，保留首行"})
                else:
                    errors.append({"line": line_number, "code": "duplicate_conflict", "message": f"与第 {first_line} 行唯一键相同但指标冲突，未覆盖"})
                continue
            seen[key] = (line_number, observation)
            parsed.append((line_number, observation))
        except ValueError as exc:
            message = str(exc)
            code = "invalid_date" if "日期" in message or "timestamp" in message else "out_of_range" if "超出范围" in message else "missing_required" if "不能为空" in message else "invalid_number"
            errors.append({"line": line_number, "code": code, "message": message})
    return parsed, errors, warnings, total_rows


def import_csv(repository: WeatherRepository, content: bytes, *, filename: str = "upload.csv", replace: bool = False) -> dict[str, Any]:
    safe_filename = Path(filename or "upload.csv").name
    digest = hashlib.sha256(content).hexdigest()
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        parsed, errors, warnings, total_rows = parse_csv(content)
    except ValueError as exc:
        error = {"line": None, "code": "file_structure", "message": str(exc)}
        payload = {
            "created_at": created_at, "filename": safe_filename, "sha256": digest,
            "replace": replace, "total_rows": 0, "valid_rows": 0, "imported": 0,
            "updated": 0, "rejected": 0, "warning_rows": 0,
            "exact_duplicates": 0, "conflict_duplicates": 0, "duplicates": 0,
            "reason_counts": {"file_structure": 1}, "warning_counts": {},
            "errors": [error], "warnings": [],
        }
        batch = repository.save_import_batch(payload)
        raise ImportValidationError(str(exc), batch) from exc

    batch_exact = sum(item["code"] == "duplicate_exact" for item in warnings)
    batch_conflicts = sum(item["code"] == "duplicate_conflict" for item in errors)
    insertable: list[Observation] = []
    if replace:
        insertable = [observation for _, observation in parsed]
    else:
        existing = {(row["timestamp"], row["plot_id"]): row for row in repository.fetch_observations({})}
        for line, observation in parsed:
            stored = existing.get((observation.timestamp, observation.plot_id))
            if stored is None:
                insertable.append(observation)
            elif _same(stored, observation):
                warnings.append({"line": line, "code": "duplicate_exact", "message": "与数据库现有记录完全重复，保留原记录"})
            else:
                errors.append({"line": line, "code": "duplicate_conflict", "message": "与数据库现有记录唯一键相同但指标冲突，未覆盖"})

    if replace and insertable:
        inserted, _ = repository.replace_observations(insertable)
    else:
        inserted, _ = repository.insert_observations(insertable, update=False)
    exact_duplicates = sum(item["code"] == "duplicate_exact" for item in warnings)
    conflict_duplicates = sum(item["code"] == "duplicate_conflict" for item in errors)
    warning_lines = {item["line"] for item in warnings if item["line"] is not None}
    payload = {
        "created_at": created_at,
        "filename": safe_filename,
        "sha256": digest,
        "replace": replace,
        "total_rows": total_rows,
        "valid_rows": max(0, total_rows - len(errors)),
        "imported": inserted,
        "updated": 0,
        "rejected": len(errors),
        "warning_rows": len(warning_lines),
        "exact_duplicates": exact_duplicates,
        "conflict_duplicates": conflict_duplicates,
        "duplicates": exact_duplicates + conflict_duplicates,
        "reason_counts": dict(Counter(item["code"] for item in errors)),
        "warning_counts": dict(Counter(item["code"] for item in warnings)),
        "errors": errors[:20],
        "warnings": warnings[:20],
        "truncated_errors": max(0, len(errors) - 20),
        "truncated_warnings": max(0, len(warnings) - 20),
        "batch_duplicate_exact": batch_exact,
        "batch_duplicate_conflicts": batch_conflicts,
    }
    return repository.save_import_batch(payload)
