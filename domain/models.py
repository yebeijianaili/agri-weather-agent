from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


SEASONS: dict[str, tuple[int, ...]] = {
    "春季": (3, 4, 5),
    "夏季": (6, 7, 8),
    "秋季": (9, 10, 11),
    "冬季": (12, 1, 2),
}

METRIC_LABELS = {
    "temperature_c": "气温",
    "precipitation_mm": "降水",
    "solar_radiation_wm2": "太阳辐射",
    "humidity_pct": "相对湿度",
}

METRIC_UNITS = {
    "temperature_c": "°C",
    "precipitation_mm": "mm",
    "solar_radiation_wm2": "W/m²",
    "humidity_pct": "%",
}


@dataclass(slots=True)
class Observation:
    timestamp: str
    plot_id: str
    temperature_c: float | None
    precipitation_mm: float | None
    solar_radiation_wm2: float | None
    humidity_pct: float | None
    quality_flag: str = "valid"
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_timestamp(value: str) -> str:
    """Return an ISO date while accepting ISO datetime and common CSV dates."""
    raw = value.strip()
    if not raw:
        raise ValueError("timestamp 不能为空")
    normalized = raw.replace("/", "-")
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(normalized).isoformat()
        except ValueError as exc:
            raise ValueError(f"日期格式无效: {value}") from exc


def season_for_month(month: int) -> str:
    for name, months in SEASONS.items():
        if month in months:
            return name
    raise ValueError(f"月份无效: {month}")

