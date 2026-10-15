from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

from domain.models import Observation


PLOTS = ("A-01 北区麦田", "B-02 南区水稻田", "C-03 东区试验田")


def generate_sample_observations(year: int = 2024, seed: int = 5102024) -> list[Observation]:
    """Generate deterministic daily microclimate observations for three plots."""
    rng = random.Random(seed)
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    days = (end - start).days + 1
    observations: list[Observation] = []

    for plot_index, plot_id in enumerate(PLOTS):
        plot_temp_offset = (-0.8, 0.6, 0.1)[plot_index]
        plot_rain_factor = (0.92, 1.10, 1.00)[plot_index]
        plot_light_factor = (1.04, 0.96, 1.00)[plot_index]
        for index in range(days):
            current = start + timedelta(days=index)
            doy = current.timetuple().tm_yday
            seasonal = math.sin(2 * math.pi * (doy - 105) / 365.25)
            shoulder = math.sin(4 * math.pi * (doy - 20) / 365.25)
            temperature = 16.2 + 13.8 * seasonal + 1.1 * shoulder + plot_temp_offset + rng.gauss(0, 2.2)

            # South-China style seasonal rainfall, with deterministic rain events.
            rain_probability = 0.16 + 0.18 * max(0.0, seasonal) + 0.08 * max(0.0, shoulder)
            if rng.random() < rain_probability:
                precipitation = min(92.0, rng.expovariate(1 / 12.0) * plot_rain_factor)
                if rng.random() < 0.045:
                    precipitation += rng.uniform(22, 48)
            else:
                precipitation = 0.0

            daylight = 335 + 150 * seasonal
            solar = (daylight + rng.gauss(0, 36) - min(precipitation, 45) * 3.0) * plot_light_factor
            humidity = 71 - 0.55 * (temperature - 18) + min(precipitation, 35) * 0.45 + rng.gauss(0, 4.5)
            flag = "valid"

            temperature_value: float | None = round(temperature, 1)
            precipitation_value: float | None = round(max(0.0, precipitation), 1)
            solar_value: float | None = round(max(45.0, solar), 1)
            humidity_value: float | None = round(min(99.0, max(28.0, humidity)), 1)

            # Built-in, traceable events make every core rule verifiable in the demo.
            if plot_index == 0:
                if date(year, 7, 15) <= current <= date(year, 7, 17):
                    temperature_value = {15: 36.2, 16: 37.8, 17: 36.7}[current.day]
                if current == date(year, 1, 20):
                    temperature_value = -1.2
                if current == date(year, 6, 20):
                    precipitation_value = 68.0
                if date(year, 10, 1) <= current <= date(year, 10, 8):
                    precipitation_value = 0.0
                if date(year, 1, 10) <= current <= date(year, 1, 12):
                    solar_value = {10: 82.0, 11: 76.0, 12: 88.0}[current.day]
                if current == date(year, 3, 10):
                    temperature_value = 9.0
                if current == date(year, 3, 11):
                    temperature_value = 21.5

            # A few deterministic sensor gaps provide a real data-quality path.
            serial = plot_index * days + index
            if serial in {88, 413, 777}:
                solar_value = None
                flag = "suspect"
            if serial in {205, 612}:
                humidity_value = None
                flag = "suspect"

            observations.append(
                Observation(
                    timestamp=current.isoformat(),
                    plot_id=plot_id,
                    temperature_c=temperature_value,
                    precipitation_mm=precipitation_value,
                    solar_radiation_wm2=solar_value,
                    humidity_pct=humidity_value,
                    quality_flag=flag,
                )
            )
    return observations


def write_sample_csv(path: str | Path, observations: list[Observation]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "plot_id",
                "temperature_c",
                "precipitation_mm",
                "solar_radiation_wm2",
                "humidity_pct",
                "quality_flag",
            ],
        )
        writer.writeheader()
        for item in observations:
            row = item.to_dict()
            row.pop("id", None)
            writer.writerow(row)
    return output
