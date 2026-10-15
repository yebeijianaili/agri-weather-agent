"""Download official NASA POWER daily agrometeorology data and optionally install it."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repositories.database import WeatherRepository
from services.analysis import AnalysisService
from services.importer import import_csv


API_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
LANDING_PAGE = "https://power.larc.nasa.gov/"
API_DOCUMENTATION = "https://power.larc.nasa.gov/docs/services/api/temporal/daily/"
PARAMETERS = ("T2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "RH2M")
POINTS = (
    {"plot_id": "NASA-GM 光明网格", "label": "深圳光明网格", "latitude": 22.7489, "longitude": 113.9359},
    {"plot_id": "NASA-LG 龙岗网格", "label": "深圳龙岗网格", "latitude": 22.7215, "longitude": 114.2469},
    {"plot_id": "NASA-DP 大鹏网格", "label": "深圳大鹏网格", "latitude": 22.5963, "longitude": 114.4797},
)
CSV_FIELDS = (
    "timestamp",
    "plot_id",
    "temperature_c",
    "precipitation_mm",
    "solar_radiation_wm2",
    "humidity_pct",
    "quality_flag",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def date_keys(start: date, end: date) -> list[str]:
    if end < start:
        raise ValueError("end date must not be earlier than start date")
    days = (end - start).days + 1
    return [(start + timedelta(days=index)).strftime("%Y%m%d") for index in range(days)]


def query_url(point: dict[str, Any], start: date, end: date) -> str:
    query = urllib.parse.urlencode(
        {
            "parameters": ",".join(PARAMETERS),
            "community": "AG",
            "longitude": point["longitude"],
            "latitude": point["latitude"],
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "format": "JSON",
        }
    )
    return f"{API_URL}?{query}"


def download_json(url: str, timeout: int = 120) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Return parsed JSON, the exact HTTP response body bytes, and response metadata.

    The body is kept separately from the parsed object so callers can persist and
    hash what the service actually returned.  Re-serializing ``payload`` would
    preserve values but would not be a byte-for-byte capture of the response.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "agri-weather-agent/1.2"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        headers = response.headers
        charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
        content_type = headers.get("Content-Type") if hasattr(headers, "get") else None
        response_metadata = {
            "status": getattr(response, "status", None),
            "content_type": content_type,
            "content_encoding": headers.get("Content-Encoding") if hasattr(headers, "get") else None,
            "server_date": headers.get("Date") if hasattr(headers, "get") else None,
            "etag": headers.get("ETag") if hasattr(headers, "get") else None,
            "last_modified": headers.get("Last-Modified") if hasattr(headers, "get") else None,
            "response_url": response.geturl() if hasattr(response, "geturl") else url,
        }
    try:
        payload = json.loads(body.decode(charset or "utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("NASA POWER response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("NASA POWER response is not a JSON object")
    return payload, body, response_metadata


def numeric(value: Any, fill_value: float) -> float | None:
    if value is None:
        return None
    number = float(value)
    return None if number == fill_value else number


def solar_to_wm2(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    normalized = unit.casefold().replace(" ", "")
    if normalized in {"mj/m^2/day", "mj/m²/day"}:
        return value * 1_000_000 / 86_400
    if normalized in {"kwh/m^2/day", "kwh/m²/day"}:
        return value * 1_000 / 24
    if normalized in {"w/m^2", "w/m²"}:
        return value
    raise RuntimeError(f"Unsupported ALLSKY_SFC_SW_DWN unit: {unit}")


def normalize_point(
    payload: dict[str, Any],
    point: dict[str, Any],
    expected_dates: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    parameters = properties.get("parameter") if isinstance(properties.get("parameter"), dict) else {}
    metadata = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    fill_value = float(header.get("fill_value", -999.0))
    missing_parameters = [name for name in PARAMETERS if not isinstance(parameters.get(name), dict)]
    if missing_parameters:
        raise RuntimeError("NASA POWER response is missing parameters: " + ", ".join(missing_parameters))
    solar_unit = str((metadata.get("ALLSKY_SFC_SW_DWN") or {}).get("units") or "")

    rows: list[dict[str, Any]] = []
    missing_cells: list[str] = []
    for key in expected_dates:
        temperature = numeric(parameters["T2M"].get(key), fill_value)
        precipitation = numeric(parameters["PRECTOTCORR"].get(key), fill_value)
        solar_native = numeric(parameters["ALLSKY_SFC_SW_DWN"].get(key), fill_value)
        humidity = numeric(parameters["RH2M"].get(key), fill_value)
        values = (temperature, precipitation, solar_native, humidity)
        if any(value is None for value in values):
            missing_cells.append(key)
        rows.append(
            {
                "timestamp": datetime.strptime(key, "%Y%m%d").date().isoformat(),
                "plot_id": point["plot_id"],
                "temperature_c": round(temperature, 2) if temperature is not None else "",
                "precipitation_mm": round(precipitation, 2) if precipitation is not None else "",
                "solar_radiation_wm2": round(solar_to_wm2(solar_native, solar_unit), 2) if solar_native is not None else "",
                "humidity_pct": round(humidity, 2) if humidity is not None else "",
                "quality_flag": "valid",
            }
        )
    if missing_cells:
        raise RuntimeError(f"{point['plot_id']} contains missing required values on {len(missing_cells)} dates")
    return rows, {
        "requested_coordinate": {"latitude": point["latitude"], "longitude": point["longitude"]},
        "response_geometry": payload.get("geometry"),
        "header": header,
        "parameters": metadata,
        "record_count": len(rows),
        "solar_conversion": {
            "from": solar_unit,
            "to": "W/m² daily mean",
            "formula": "MJ/m²/day × 1,000,000 ÷ 86,400" if solar_unit.casefold().startswith("mj") else "source-unit conversion",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 12, 31))
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "nasa_power_shenzhen_2024.csv")
    parser.add_argument("--provenance", type=Path, default=ROOT / "data" / "nasa_power_shenzhen_2024.provenance.json")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw" / "nasa_power_2024")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "agri_weather.db")
    parser.add_argument("--install", action="store_true", help="Replace the target database snapshot and run analysis")
    args = parser.parse_args()

    expected_dates = date_keys(args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    all_rows: list[dict[str, Any]] = []
    point_reports: list[dict[str, Any]] = []
    query_urls: list[str] = []
    raw_files: list[dict[str, Any]] = []

    for point in POINTS:
        url = query_url(point, args.start, args.end)
        query_urls.append(url)
        payload, raw_body, response_metadata = download_json(url)
        raw_path = args.raw_dir / f"{point['plot_id'].split()[0].lower()}_{args.start:%Y%m%d}_{args.end:%Y%m%d}.json"
        raw_path.write_bytes(raw_body)
        rows, report = normalize_point(payload, point, expected_dates)
        all_rows.extend(rows)
        point_reports.append(
            {
                **point,
                **report,
                "query_url": url,
                "raw_file": str(raw_path.relative_to(ROOT)),
                "http_response": response_metadata,
            }
        )
        raw_files.append({"path": str(raw_path.relative_to(ROOT)), "sha256": sha256_file(raw_path), "bytes": raw_path.stat().st_size})

    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    provenance = {
        "source_key": f"nasa-power-shenzhen-daily-{args.start:%Y%m%d}-{args.end:%Y%m%d}",
        "provider": "NASA Langley Research Center POWER Project",
        "dataset_name": point_reports[0]["header"].get("title", "NASA POWER Daily Point Data"),
        "api_name": point_reports[0]["header"].get("api", {}).get("name"),
        "api_version": point_reports[0]["header"].get("api", {}).get("version"),
        "official_sources": point_reports[0]["header"].get("sources", []),
        "landing_page": LANDING_PAGE,
        "api_documentation": API_DOCUMENTATION,
        "query_urls": query_urls,
        "retrieved_at": retrieved_at,
        "time_standard": point_reports[0]["header"].get("time_standard"),
        "community": "AG",
        "period": {"start": args.start.isoformat(), "end": args.end.isoformat(), "days": len(expected_dates)},
        "parameters": point_reports[0]["parameters"],
        "normalized_schema": {
            "T2M": "temperature_c",
            "PRECTOTCORR": "precipitation_mm",
            "ALLSKY_SFC_SW_DWN": "solar_radiation_wm2 (daily energy converted to daily mean power)",
            "RH2M": "humidity_pct",
        },
        "spatial_note": "These are NASA POWER gridded point values at the requested Shenzhen coordinates, not on-site station observations.",
        "spatial_sampling_note": "Each label denotes one representative request coordinate, not an administrative-area average. Different requested coordinates can share identical values at the products' native spatial resolution.",
        "raw_capture_mode": "http_response_body",
        "raw_capture_note": "Raw file hashes cover the exact HTTP response body bytes returned by NASA POWER.",
        "quality_note": "Values are official NASA POWER gridded satellite/reanalysis products. The local quality flag only records ingestion completeness and range checks; it is not an upstream measurement-accuracy rating.",
        "points": point_reports,
        "raw_files": raw_files,
        "normalized_file": {
            "path": str(args.output.resolve().relative_to(ROOT.resolve())),
            "sha256": sha256_file(args.output),
            "bytes": args.output.stat().st_size,
            "records": len(all_rows),
        },
    }
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    result: dict[str, Any] = {
        "downloaded": True,
        "records": len(all_rows),
        "expected_records": len(expected_dates) * len(POINTS),
        "csv": str(args.output.resolve()),
        "csv_sha256": provenance["normalized_file"]["sha256"],
        "provenance": str(args.provenance.resolve()),
        "raw_files": raw_files,
        "installed": False,
    }
    if len(all_rows) != result["expected_records"]:
        raise RuntimeError(f"Record conservation failed: {len(all_rows)} != {result['expected_records']}")

    if args.install:
        repository = WeatherRepository(args.db)
        repository.initialize()
        import_report = import_csv(repository, args.output.read_bytes(), filename=args.output.name, replace=True)
        source = repository.save_dataset_source(provenance)
        analysis = AnalysisService(repository).run_analysis({})
        result.update(
            {
                "installed": True,
                "database": str(args.db.resolve()),
                "import": import_report,
                "source_id": source["source_id"],
                "analysis_id": analysis["analysis_id"],
                "database_records": repository.observation_count(),
            }
        )

    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
