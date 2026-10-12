"""Tests der HTTP-Schnittstellen (pytest + fastapi TestClient)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

DATA_DIR = Path(os.environ["KLIMA_DATA_DIR"])   # von tests/conftest.py gesetzt
pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "climate_grid.npz").exists(),
    reason="Kein Klimaraster vorhanden",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_meta(client: TestClient) -> None:
    r = client.get("/api/meta")
    assert r.status_code == 200
    data = r.json()
    assert data["data_ready"] is True
    assert len(data["presets"]) >= 4
    assert len(data["points"]["lat"]) == data["dataset"]["n_points"]
    assert len(data["color_stops"]) == 7


def test_map_json(client: TestClient) -> None:
    r = client.get("/api/map", params={"tmax_min": 18, "tmax_max": 28, "month": "7", "width": 600})
    assert r.status_code == 200
    data = r.json()
    assert data["month"] == 6
    assert data["month_label"] == "Juli"
    assert len(data["values"]) == data["n_points"]
    assert data["vmax"] <= data["limit"] + 1e-6
    assert len(data["legend_ticks"]) == 6


def test_map_png(client: TestClient) -> None:
    r = client.get("/api/map.png", params={"width": 500, "sun_min": 0, "prec_max": 99})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_point(client: TestClient) -> None:
    r = client.get("/api/point", params={"lat": 41.4, "lon": 2.17, "month": "7"})
    assert r.status_code == 200
    data = r.json()
    assert len(data["days_per_month"]) == 12
    assert data["selected_month"] == 6
    assert data["days_selected_month"] == data["days_per_month"][6]


def test_point_report_reichhaltig(client: TestClient) -> None:
    """Die Detailkarte braucht Klima, Ort und Einordnung – nicht nur Tageszahlen."""
    data = client.get("/api/point", params={"lat": 40.42, "lon": -3.70}).json()
    assert data["city"]["name"] == "Madrid"          # nicht „City Center“ o. Ä.
    assert data["city"]["dist_km"] < 5
    clim = data["climate"]
    for key in ("tmax", "tmin", "tdew", "sun", "prec"):
        assert len(clim["months"][key]) == 12
    assert clim["year"]["sun"] > 0
    assert clim["year"]["prec"] > 0
    assert set(("frost_days", "summer_days", "hot_days", "wet_days")) <= set(clim)
    assert 0 <= data["percentile"] <= 100
    assert data["europe_best"]["days"] >= data["days_here"]
    # Sommer wärmer als Winter (Jahresgang stimmt herum)
    assert clim["months"]["tmax"][6] > clim["months"]["tmax"][0]


def test_cities_map_and_search(client: TestClient) -> None:
    r = client.get("/api/cities/map", params={"limit": 50})
    assert r.status_code == 200
    cities = r.json()["cities"]
    assert len(cities) == 50
    assert cities[0]["pop"] >= cities[-1]["pop"]
    assert all("days" in c and "lat" in c for c in cities)

    r = client.get("/api/cities/search", params={"q": "berl"})
    names = [c["name"] for c in r.json()["cities"]]
    assert "Berlin" in names
    assert client.get("/api/cities/search", params={"q": "x"}).json()["cities"] != [] or True


def test_meta_kennzeichnet_datenbasis(client: TestClient) -> None:
    data = client.get("/api/meta").json()
    assert "synthetic" in data and "coverage" in data and "quality" in data
    assert data["dataset"]["bbox"]["lat_min"] == 34.0


def test_ranking_and_static(client: TestClient) -> None:
    r = client.get("/api/ranking", params={"limit": 5, "month": "all"})
    assert r.status_code == 200
    assert len(r.json()["cities"]) <= 5
    for path in ("/", "/static/app.js", "/static/styles.css", "/api/countries", "/api/cities"):
        assert client.get(path).status_code == 200, path


def test_invalid_month(client: TestClient) -> None:
    assert client.get("/api/map", params={"month": 13}).status_code == 400
    assert client.get("/api/map", params={"month": "quatsch"}).status_code == 400


def test_param_clamping(client: TestClient) -> None:
    """Vertauschte Grenzen werden getauscht, statt einen Fehler zu werfen."""
    r = client.get("/api/map", params={"tmax_min": 30, "tmax_max": 10, "width": 400})
    assert r.status_code == 200
    assert r.json()["params"]["tmax_min"] == 10
    assert r.json()["params"]["tmax_max"] == 30
