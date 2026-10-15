from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domain.models import Observation
from repositories.database import WeatherRepository
from services.importer import import_csv


def test_replace_import_rolls_back_original_snapshot_when_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repository = WeatherRepository(tmp_path / "atomic-replace.db")
    repository.initialize()
    original = Observation(
        timestamp="2026-01-01",
        plot_id="ORIGINAL",
        temperature_c=18,
        precipitation_mm=1,
        solar_radiation_wm2=240,
        humidity_pct=70,
    )
    repository.insert_observations([original])
    before = repository.fetch_observations({})

    real_insert = repository._insert_observations

    def fail_after_first_insert(conn, observations, *, update=False):
        first = list(observations)[0]
        real_insert(conn, [first], update=update)
        raise sqlite3.OperationalError("simulated write failure")

    monkeypatch.setattr(repository, "_insert_observations", fail_after_first_insert)
    replacement_csv = (
        "timestamp,plot_id,temperature_c,precipitation_mm,solar_radiation_wm2,humidity_pct\n"
        "2026-02-01,REPLACEMENT,22,0,320,65\n"
    ).encode("utf-8")

    with pytest.raises(sqlite3.OperationalError, match="simulated write failure"):
        import_csv(repository, replacement_csv, filename="replacement.csv", replace=True)

    assert repository.fetch_observations({}) == before
