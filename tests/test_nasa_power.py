from __future__ import annotations

import json

from scripts.fetch_nasa_power import download_json, normalize_point


def test_nasa_power_normalization_converts_daily_energy_to_mean_wm2():
    payload = {
        "header": {
            "title": "NASA/POWER Source Native Resolution Daily Data",
            "fill_value": -999.0,
            "api": {"name": "POWER Daily API", "version": "v2.9.7"},
        },
        "parameters": {
            "T2M": {"units": "C"},
            "PRECTOTCORR": {"units": "mm/day"},
            "ALLSKY_SFC_SW_DWN": {"units": "MJ/m^2/day"},
            "RH2M": {"units": "%"},
        },
        "properties": {
            "parameter": {
                "T2M": {"20240101": 20.5},
                "PRECTOTCORR": {"20240101": 2.4},
                "ALLSKY_SFC_SW_DWN": {"20240101": 8.64},
                "RH2M": {"20240101": 71.2},
            }
        },
        "geometry": {"coordinates": [114.0, 22.5, 10.0]},
    }
    point = {"plot_id": "NASA-TEST", "label": "测试网格", "latitude": 22.5, "longitude": 114.0}

    rows, report = normalize_point(payload, point, ["20240101"])

    assert rows == [
        {
            "timestamp": "2024-01-01",
            "plot_id": "NASA-TEST",
            "temperature_c": 20.5,
            "precipitation_mm": 2.4,
            "solar_radiation_wm2": 100.0,
            "humidity_pct": 71.2,
            "quality_flag": "valid",
        }
    ]
    assert report["record_count"] == 1
    assert report["solar_conversion"]["from"] == "MJ/m^2/day"


def test_nasa_download_keeps_exact_http_response_body(monkeypatch):
    body = b'{"header":{"api":{"version":"v2.9.7"}},"properties":{"parameter":{}}}\n'

    class Headers(dict):
        def get_content_charset(self):
            return "utf-8"

    class Response:
        status = 200
        headers = Headers({"Content-Type": "application/json; charset=utf-8", "Date": "Sun, 23 Aug 2026 01:14:30 GMT"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return body

        def geturl(self):
            return "https://power.larc.nasa.gov/test"

    monkeypatch.setattr("scripts.fetch_nasa_power.urllib.request.urlopen", lambda *_args, **_kwargs: Response())

    payload, captured_body, metadata = download_json("https://power.larc.nasa.gov/test")

    assert payload == json.loads(body)
    assert captured_body == body
    assert metadata == {
        "status": 200,
        "content_type": "application/json; charset=utf-8",
        "content_encoding": None,
        "server_date": "Sun, 23 Aug 2026 01:14:30 GMT",
        "etag": None,
        "last_modified": None,
        "response_url": "https://power.larc.nasa.gov/test",
    }
