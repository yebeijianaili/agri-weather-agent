from __future__ import annotations

from datetime import date, timedelta

from services.analysis import AnalysisService


def row(day: date, *, plot: str = "P-1", temp: float = 20, rain: float = 2, solar: float = 300):
    return {
        "id": 1,
        "timestamp": day.isoformat(),
        "plot_id": plot,
        "temperature_c": temp,
        "precipitation_mm": rain,
        "solar_radiation_wm2": solar,
        "humidity_pct": 70,
        "quality_flag": "valid",
    }


def test_winter_december_is_assigned_to_next_season_year():
    service = AnalysisService(repository=None)  # type: ignore[arg-type]
    rows = [row(date(2025, 12, 20)), row(date(2026, 1, 20)), row(date(2026, 2, 20))]
    seasonal = service._seasonal_by_year(rows)
    assert len(seasonal) == 1
    assert seasonal[0]["season"] == "冬季"
    assert seasonal[0]["season_year"] == 2026
    assert seasonal[0]["record_count"] == 3


def test_dry_spell_threshold_is_seven_consecutive_days_below_one_mm():
    service = AnalysisService(repository=None)  # type: ignore[arg-type]
    start = date(2026, 4, 1)
    rows = [row(start + timedelta(days=index), rain=0.9) for index in range(7)]
    events = service._risk_events(rows)
    dry = [event for event in events if event["code"] == "DRY_SPELL"]
    assert len(dry) == 1
    assert dry[0]["duration_days"] == 7
    assert dry[0]["start_date"] == "2026-04-01"
    assert dry[0]["end_date"] == "2026-04-07"

    interrupted = rows[:3] + [row(start + timedelta(days=3), rain=1.0)] + rows[4:]
    assert not [event for event in service._risk_events(interrupted) if event["code"] == "DRY_SPELL"]


def test_risk_rule_boundaries_are_inclusive_and_events_are_traceable():
    service = AnalysisService(repository=None)  # type: ignore[arg-type]
    start = date(2026, 6, 1)
    rows = [
        row(start, temp=35, rain=50, solar=99),
        row(start + timedelta(days=1), temp=35, solar=99),
        row(start + timedelta(days=2), temp=35, solar=99),
        row(start + timedelta(days=3), temp=0),
    ]
    events = service._risk_events(rows)
    codes = {event["code"] for event in events}
    assert {"HEAT_DAY", "HEAT_STREAK", "COLD_DAY", "HEAVY_RAIN", "LOW_LIGHT_STREAK"} <= codes
    assert all(event["plot_id"] == "P-1" and event["evidence"] for event in events)


def test_adjacent_daily_threshold_hits_are_merged_with_peak_evidence():
    service = AnalysisService(repository=None)  # type: ignore[arg-type]
    start = date(2026, 7, 1)
    rows = [
        row(start, temp=35, rain=50),
        row(start + timedelta(days=1), temp=38, rain=72),
        row(start + timedelta(days=2), temp=20, rain=0),
        row(start + timedelta(days=3), temp=-1, rain=0),
        row(start + timedelta(days=4), temp=-4, rain=0),
    ]

    events = service._risk_events(rows)
    heat = [event for event in events if event["code"] == "HEAT_DAY"]
    cold = [event for event in events if event["code"] == "COLD_DAY"]
    rain = [event for event in events if event["code"] == "HEAVY_RAIN"]

    assert len(heat) == len(cold) == len(rain) == 1
    assert heat[0]["start_date"] == "2026-07-01"
    assert heat[0]["end_date"] == "2026-07-02"
    assert heat[0]["duration_days"] == 2
    assert heat[0]["metric_value"] == 38
    assert "峰值 38" in heat[0]["evidence"]
    assert cold[0]["duration_days"] == 2
    assert cold[0]["metric_value"] == -4
    assert "最低值 -4" in cold[0]["evidence"]
    assert rain[0]["duration_days"] == 2
    assert rain[0]["metric_value"] == 72
    assert "峰值 72" in rain[0]["evidence"]
