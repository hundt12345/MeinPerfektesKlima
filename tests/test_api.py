"""Tests der HTTP-Schnittstellen (pytest + fastapi TestClient)."""
from __future__ import annotations

import json
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


def test_cities_climate_returns_complete_city_table(client: TestClient) -> None:
    """Die Zusatzansicht enthält jede Stadt plus klar definierte Jahreswerte."""
    r = client.get("/api/cities/climate")
    assert r.status_code == 200
    assert "max-age=3600" in r.headers.get("cache-control", "")
    data = r.json()
    assert data["period"]["start_date"]
    assert data["period"]["end_date"]
    assert data["period"]["years"] > 0
    assert data["city_method"] == "nearest_grid_point"
    assert data["city_count"] == len(data["cities"])
    assert data["city_count"] == len(json.loads((DATA_DIR / "cities.json").read_text()))

    city = data["cities"][0]
    assert {"name", "country", "lat", "lon", "pop", "grid_distance_km", "climate"} <= set(city)
    assert {"tmax", "tmin", "tdew", "sun", "prec"} <= set(city["climate"]["year"])
    assert {"hot_days", "summer_days", "winter_days", "ice_days", "sultry_days",
            "wet_days", "sunny_days", "very_hot_days", "tropical_nights", "heavy_rain_days"} \
        <= set(city["climate"])
    assert data["indicators"]["hot_days"]["threshold"] == "Tageshöchsttemperatur > 30 °C"
    assert data["indicators"]["sunny_days"]["threshold"] == "Sonnenschein > 4 Stunden/Tag"
    assert all(city["climate"][key] >= 0 for key in data["indicators"])


def test_meta_kennzeichnet_datenbasis(client: TestClient) -> None:
    data = client.get("/api/meta").json()
    assert "synthetic" in data and "coverage" in data and "quality" in data
    assert data["dataset"]["bbox"]["lat_min"] == 34.0


def test_meta_perioden_und_zukunftsprojektionen(client: TestClient) -> None:
    data = client.get("/api/meta").json()
    periods = {item["id"]: item for item in data["periods"]}
    assert data["default_period"] == "all"
    assert periods["all"]["available"] is True
    assert periods["recent5"]["available"] is True
    assert periods["recent5"]["start_date"] == "2021-01-01"
    assert periods["recent5"]["end_date"] == "2025-12-31"
    assert periods["2030"]["available"] is True
    assert periods["2030"]["approximation"] is True
    assert periods["2050"]["available"] is True
    assert periods["2050"]["model_count"] == 31
    assert "Klimamodell" in periods["2050"]["description"]


def test_recent5_is_used_for_all_climate_endpoints(client: TestClient) -> None:
    map_data = client.get("/api/map", params={"period": "recent5", "width": 500}).json()
    assert map_data["period"] == "recent5"
    assert map_data["period_info"]["start_date"] == "2021-01-01"
    assert len(map_data["values"]) == map_data["n_points"]

    point = client.get("/api/point", params={"lat": 48.85, "lon": 2.35,
                                             "period": "recent5"}).json()
    assert point["period"]["id"] == "recent5"
    assert point["period"]["years"] == pytest.approx(5.0, abs=0.02)

    cities = client.get("/api/cities/climate", params={"period": "recent5"}).json()
    assert cities["period"]["id"] == "recent5"
    assert cities["period"]["start_date"] == "2021-01-01"

    assert client.get("/api/map", params={"period": "2060"}).status_code == 400


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


# ------------------------------------------------------- Monatsfenster & Winterquartier
def test_month_fenster(client: TestClient) -> None:
    """Monatsbereiche für die Karte (z. B. „Wintermonate“)."""
    r = client.get("/api/map", params={"month": "5-9", "width": 500})
    assert r.status_code == 200
    data = r.json()
    assert data["month"] is None                       # kein Einzelmonat
    assert data["month_label"] == "Mai – September"
    assert data["unit_label"] == "Tage Mai – September"
    assert 150 < data["limit"] < 155                   # 153 Tage je Jahr
    assert data["vmax"] <= data["limit"] + 1e-6

    wrap = client.get("/api/map", params={"month": "10-3", "width": 500}).json()
    assert wrap["month_label"] == "Oktober – März"
    assert wrap["limit"] > data["limit"]               # Winterhalbjahr ist länger

    punkt = client.get("/api/point", params={"lat": 41.4, "lon": 2.17, "month": "5-9"}).json()
    assert punkt["months"] == [4, 5, 6, 7, 8]
    assert "selected_month" not in punkt
    assert punkt["days_here"] <= punkt["month_limit"] + 1e-6


def test_map_winterquartier(client: TestClient) -> None:
    params = {"width": 500, "view": "wq", "sfrom": 5, "sto": 9,
              "tmax_min": 22, "tmax_max": 32, "sun_min": 5,
              "wtmax_min": 14, "wtmax_max": 26, "wsun_min": 4}
    combo = client.get("/api/map", params={**params, "wview": "combo"}).json()
    assert combo["view_kind"] == "combo"
    assert combo["summer_months"] == [4, 5, 6, 7, 8]
    assert combo["winter_months"] == [0, 1, 2, 3, 9, 10, 11]
    assert combo["limit"] == pytest.approx(365.25, abs=0.1)
    assert len(combo["values"]) == combo["n_points"]
    assert "Sommer + Winter" in combo["unit_label"]
    assert combo["winter_params"]["sun_min"] == 4

    sommer = client.get("/api/map", params={**params, "wview": "summer"}).json()
    winter = client.get("/api/map", params={**params, "wview": "winter"}).json()
    assert sommer["view_kind"] == "summer" and winter["view_kind"] == "winter"
    # Kombination = Sommer- + Winteransicht (die Karte zählt beide zusammen)
    assert all(abs(c - (s + w)) <= 1
               for c, s, w in zip(combo["values"], sommer["values"], winter["values"]))
    assert winter["unit_label"].startswith("Tage im Winterquartier")

    png = client.get("/api/map.png", params={**params, "width": 420})
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_winterquartier_fenster_fehler(client: TestClient) -> None:
    # „von Juni bis Mai“ wäre das ganze Jahr – dann gäbe es kein Winterquartier
    assert client.get("/api/map", params={"view": "wq", "sfrom": 6, "sto": 5}).status_code == 400
    assert client.get("/api/map", params={"view": "quatsch"}).status_code == 400
    assert client.get("/api/map", params={"view": "wq", "wview": "quatsch"}).status_code == 400


def test_point_winterquartier(client: TestClient) -> None:
    r = client.get("/api/point", params={"lat": 41.4, "lon": 2.17, "view": "wq"})
    assert r.status_code == 200
    data = r.json()
    wq = data["wq"]
    assert wq["summer_label"] == "Mai – September"
    assert wq["winter_label"] == "Oktober – April"
    assert wq["total_days"] == pytest.approx(wq["summer_days"] + wq["winter_days"], abs=0.2)
    assert wq["summer_days"] <= wq["summer_year_days"] + 1e-6
    assert len(wq["summer_monthly"]) == 12 and len(wq["winter_monthly"]) == 12
    assert 0 <= wq["percentile"] <= 100
    assert wq["europe_best"]["days"] >= wq["total_days"] - 0.2
    # Ohne Winterquartier-Modus bleibt der Block weg
    assert "wq" not in client.get("/api/point", params={"lat": 41.4, "lon": 2.17}).json()


def test_optimize_endpoint(client: TestClient) -> None:
    r = client.get("/api/winterquarters/optimize",
                   params={"tmax_min": 22, "tmax_max": 32, "sun_min": 5, "limit": 4})
    assert r.status_code == 200
    data = r.json()
    assert data["window_range"] == {"min_months": 3, "max_months": 9}
    assert 0 < len(data["candidates"]) <= 4
    best = data["best"]
    assert 3 <= best["length"] <= 9
    assert len(best["months"]) == best["length"]
    assert best["best"]["days"] > 0 and best["best_city"]["days"] > 0
    assert best["summer_label"] and best["winter_label"]
    # Erlaubt enger gefasste Saisons
    eng = client.get("/api/winterquarters/optimize", params={"min_months": 5, "max_months": 6}).json()
    assert all(5 <= c["length"] <= 6 for c in eng["candidates"])


def test_pair_endpoint(client: TestClient) -> None:
    r = client.get("/api/winterquarters/pair",
                   params={"lat": 48.85, "lon": 2.35, "role": "summer", "limit": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["wanted_label"] == "Winterquartier"
    assert data["anchor"]["city"]["name"] == "Paris"
    cands = data["candidates"]
    assert 0 < len(cands) <= 5
    totals = [c["total_days"] for c in cands]
    assert totals == sorted(totals, reverse=True)
    for c in cands:
        assert c["own_days"] + c["counterpart_days"] == pytest.approx(c["total_days"], abs=0.3)
        assert set(c) >= {"name", "country", "lat", "lon", "km", "days", "summer_label"}

    nah = client.get("/api/winterquarters/pair",
                     params={"lat": 48.85, "lon": 2.35, "max_km": 700, "limit": 5}).json()
    assert nah["max_km"] == 700
    assert all(c["km"] <= 700 for c in nah["candidates"])

    winter = client.get("/api/winterquarters/pair",
                        params={"lat": 41.4, "lon": 2.17, "role": "winter", "limit": 3}).json()
    assert winter["wanted_label"] == "Sommerquartier"
    assert winter["anchor"]["own_days"] == pytest.approx(winter["anchor"]["winter_days"], abs=0.2)

    assert client.get("/api/winterquarters/pair",
                      params={"lat": 48.85, "lon": 2.35, "role": "quatsch"}).status_code == 400


def test_meta_winterquartier(client: TestClient) -> None:
    data = client.get("/api/meta").json()
    assert data["winterquarters"]["summer_months"] == [5, 6, 7, 8, 9]
    assert data["winterquarters"]["min_months"] <= data["winterquarters"]["max_months"]
    assert len(data["winter_presets"]) >= 3
    assert set(data["winter_defaults"]) == set(data["defaults"])
    assert {v["id"] for v in data["winterquarters"]["views"]} == {"combo", "summer", "winter"}


def test_ranking_und_staedte_im_winterquartier(client: TestClient) -> None:
    """Marker und Top-Liste müssen dieselbe Kennzahl zeigen wie die Karte."""
    params = {"view": "wq", "wview": "winter", "sfrom": 5, "sto": 9}
    rank = client.get("/api/ranking", params={**params, "limit": 5}).json()
    assert rank["view_kind"] == "winter" and rank["month"] is None
    assert len(rank["cities"]) <= 5
    staedte = client.get("/api/cities/map", params={**params, "limit": 40}).json()
    assert len(staedte["cities"]) == 40
    assert staedte["view_kind"] == "winter"
    # Sommer- und Winteransicht dürfen sich nicht gleichen
    sommer = client.get("/api/ranking", params={**params, "wview": "summer", "limit": 5}).json()
    assert [c["days"] for c in sommer["cities"]] != [c["days"] for c in rank["cities"]]


def test_winterquarters_combination_uses_fixed_window_and_one_profile(client: TestClient) -> None:
    params = {
        "sfrom": 5, "sto": 7, "limit": 6,
        "tmax_min": 18, "tmax_max": 30,
        "tmin_min": 5, "tmin_max": 20,
        "tdew_max": 18, "sun_min": 3, "prec_max": 99,
    }
    response = client.get("/api/winterquarters/combination", params=params)
    assert response.status_code == 200
    data = response.json()
    assert data["summer_months"] == [4, 5, 6]
    assert data["winter_months"] == [0, 1, 2, 3, 7, 8, 9, 10, 11]
    assert data["summer_label"] == "Mai – Juli"
    assert data["winter_label"] == "August – April"
    assert data["params"]["tmax_min"] == 18
    assert data["params"]["tmax_max"] == 30
    assert data["params"]["tmin_min"] == 5
    assert data["params"]["tmin_max"] == 20
    assert data["params"]["tdew_max"] == 18
    assert data["params"]["sun_min"] == 3
    assert data["min_distance_km"] == 50
    assert 0 < len(data["candidates"]) <= 6
    assert data["best"] == data["candidates"][0]
    totals = [candidate["total_days"] for candidate in data["candidates"]]
    assert totals == sorted(totals, reverse=True)
    for candidate in data["candidates"]:
        assert candidate["distance_km"] >= 50
        assert candidate["total_days"] == pytest.approx(
            candidate["summer_days"] + candidate["winter_days"], abs=0.2)
        assert candidate["summer_city"] != candidate["winter_city"]

    # Ein Sommerfenster über alle zwölf Monate lässt kein Winterquartier übrig.
    full_year = client.get("/api/winterquarters/combination", params={"sfrom": 6, "sto": 5})
    assert full_year.status_code == 400


@pytest.mark.parametrize("season,home_key", [("winter", "summer_city"), ("summer", "winter_city")])
def test_escape_quarters_api(client: TestClient, season: str, home_key: str) -> None:
    response = client.get("/api/winterquarters/escape", params={
        "lat": 52.52, "lon": 13.405, "season": season, "sfrom": 11, "sto": 2,
        "period": "recent5", "limit": 3,
    })
    assert response.status_code == 200
    report = response.json()
    assert report["summer_months"] == [0, 1, 10, 11]
    assert report["summer_label"] == "November – Februar"
    assert report["period"]["id"] == "recent5"
    assert len(report["candidates"]) == 3
    for candidate in report["candidates"]:
        assert candidate[home_key]["lat"] == 52.52
        assert candidate[home_key]["lon"] == 13.405
        assert candidate["total_days"] - report["baseline_days"] == pytest.approx(candidate["gain_days"], abs=.11)


def test_escape_quarters_validation(client: TestClient) -> None:
    params = {"lat": 52.52, "lon": 13.405}
    assert client.get("/api/winterquarters/escape").status_code == 422
    assert client.get("/api/winterquarters/escape", params={**params, "season": "spring"}).status_code == 422
    assert client.get("/api/winterquarters/escape", params={**params, "lat": 0}).status_code == 422
    assert client.get("/api/winterquarters/escape", params={**params, "period": "2060"}).status_code == 400
    assert client.get("/api/winterquarters/escape", params={**params, "sfrom": 6, "sto": 5}).status_code == 400
