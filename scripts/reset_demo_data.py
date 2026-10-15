from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repositories.database import WeatherRepository  # noqa: E402
from services.analysis import AnalysisService  # noqa: E402
from services.sample_data import generate_sample_observations, write_sample_csv  # noqa: E402


def main() -> int:
    database = (ROOT / "data" / "agri_weather.db").resolve()
    expected_parent = (ROOT / "data").resolve()
    if database.parent != expected_parent:
        raise RuntimeError(f"Unexpected database path: {database}")

    repository = WeatherRepository(database)
    repository.initialize()
    with repository.connect() as connection:
        for table in ("agent_queries", "audit_events", "import_batches", "analysis_runs", "observations"):
            connection.execute(f"DELETE FROM {table}")
        connection.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('agent_queries','audit_events','import_batches','analysis_runs','observations')"
        )

    observations = generate_sample_observations()
    inserted, _ = repository.insert_observations(observations)
    write_sample_csv(ROOT / "data" / "sample_weather_2024.csv", observations)
    analysis = AnalysisService(repository).run_analysis({"year": 2024})
    print(f"RESET_RECORDS={inserted}")
    print(f"ANALYSIS_ID={analysis['analysis_id']}")
    print(f"ANALYSIS_RECORDS={analysis['result']['overview']['record_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

