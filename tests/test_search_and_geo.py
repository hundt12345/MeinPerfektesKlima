"""Ortssuche, Ausweichziele, Grenzlinien und Kartenrand.

Diese Tests sichern die Fehler ab, die auf der Website als „Städtesuche klappt
nicht“ und „komische Striche“ sichtbar waren.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.climate import EDGE_FEATHER_DEG, ClimateData, Params
from app.main import app
from app.names import fold, match_rank, search_key, variants
from scripts.build_geo import _split_line, fix_antimeridian

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ["KLIMA_DATA_DIR"])   # von tests/conftest.py gesetzt
REPO_DATA = ROOT / "app" / "data"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def cd() -> ClimateData:
    return ClimateData(DATA_DIR)


def km(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ Namen
def test_fold_and_german_transliteration() -> None:
    assert fold("Málaga") == "malaga"
    assert fold("Łódź") == "lodz"
    assert fold("Straßburg") == "strassburg"
    assert fold("Mülheim (Ruhr)") == "mulheim ruhr"
    assert variants("München") == ["munchen", "muenchen"]
    assert variants("Duisburg") == ["duisburg"]


def test_match_rank_orders_exact_prefix_words() -> None:
    key = search_key({"name": "Mülheim (Ruhr)", "aliases": []})
    assert match_rank(key, "Mülheim an der Ruhr") == 2      # Füllwörter, Klammern egal
    assert match_rank(key, "muelheim") == 1
    genf = search_key({"name": "Genf", "name_local": "Genève", "aliases": ["Geneva"]})
    assert match_rank(genf, "genf") == 0
    assert match_rank(genf, "Geneve") == 0
    assert match_rank(search_key({"name": "Klagenfurt am Wörthersee"}), "genf") == 3
    assert match_rank(genf, "xyz") is None


@pytest.mark.parametrize("query,expected", [
    ("Duisburg", "Duisburg"), ("duis", "Duisburg"), ("München", "München"), ("muenchen", "München"),
    ("Munich", "München"), ("Koeln", "Köln"), ("Cologne", "Köln"), ("Düsseldorf", "Düsseldorf"),
    ("Duesseldorf", "Düsseldorf"), ("Mailand", "Mailand"), ("Milano", "Mailand"), ("Kopenhagen", "Kopenhagen"),
    ("Brüssel", "Brüssel"), ("Genf", "Genf"), ("Prag", "Prag"), ("Warschau", "Warschau"), ("Krakau", "Krakau"),
    ("Neapel", "Neapel"), ("Nizza", "Nizza"), ("Lissabon", "Lissabon"), ("Mallorca", "Palma"),
    ("Mülheim an der Ruhr", "Mülheim (Ruhr)"), ("St. Petersburg", "Sankt Petersburg"), ("Murmansk", "Murmansk"),
])
def test_city_search_is_tolerant(client: TestClient, query: str, expected: str) -> None:
    data = client.get("/api/cities/search", params={"q": query, "limit": 3, "area": "data"}).json()
    names = [c["name"] for c in data["cities"]]
    assert names and names[0] == expected, (query, names)


def test_city_search_alias_exact_and_area(client: TestClient, cd: ClimateData) -> None:
    hit = client.get("/api/cities/search", params={"q": "Milano", "limit": 1}).json()["cities"][0]
    assert hit["name"] == "Mailand" and hit["alias"] == "Milano" and hit["exact"] is True
    assert client.get("/api/cities/search", params={"q": "Nicht ausgewählt"}).json()["cities"] == []
    box = cd.bbox
    for city in client.get("/api/cities/search", params={"q": "a", "limit": 20, "area": "data"}).json()["cities"]:
        assert box["lat_min"] <= city["lat"] <= box["lat_max"] and box["lon_min"] <= city["lon"] <= box["lon_max"]
    assert client.get("/api/cities/search", params={"q": "x", "area": "welt"}).status_code == 422


# ------------------------------------------------------- Ausweichziele
def test_escape_destinations_are_spread_out(client: TestClient, cd: ClimateData) -> None:
    params = {"lat": 51.4325, "lon": 6.7652, "season": "winter", "limit": 8}
    spread = client.get("/api/winterquarters/escape", params=params).json()
    dense = cd.escape_report(51.4325, 6.7652, "winter", Params(), limit=8, min_distance_km=50)
    destinations = [c["winter_city"] for c in spread["candidates"]]
    assert len(destinations) == 8
    for i, a in enumerate(destinations):
        for b in destinations[i + 1:]:
            assert km(a, b) >= 120 - 1e-6, (a["name"], b["name"])
    # Der beste Ort bleibt der beste – gestreut wird nur der Rest der Liste.
    assert spread["best"]["total_days"] == pytest.approx(dense["best"]["total_days"])
    totals = [c["total_days"] for c in spread["candidates"]]
    assert totals == sorted(totals, reverse=True)
    assert client.get("/api/winterquarters/escape", params={**params, "spread_km": 0}).json()["candidates"] \
        == dense["candidates"]


# ------------------------------------------------------- Klimazeitraum
def test_scenarios_have_plain_language_labels(client: TestClient) -> None:
    periods = {p["id"]: p for p in client.get("/api/meta").json()["periods"]}
    assert periods["all"]["short_label"] == "Heute" and periods["all"]["group"] == "now"
    assert periods["all"]["label"].startswith("Heute (")
    names = [periods[i]["scenario_name"] for i in ("2050-ssp126", "2050", "2050-ssp585")]
    assert names == ["Viel Klimaschutz", "Mittlerer Weg", "Wenig Klimaschutz"]
    assert periods["2050-ssp585"]["label"] == "2050 · Wenig Klimaschutz (SSP5-8.5)"
    warming = [periods[i]["warming_c"] for i in ("2050-ssp126", "2050", "2050-ssp585")]
    assert warming == sorted(warming) and warming[0] > 0
    for pid in ("2030", "2050"):
        assert periods[pid]["group"] == pid and "wärmer als 2016–2025" in periods[pid]["summary"]


# ------------------------------------------------------- Geodaten
def test_antimeridian_rings_are_unwrapped() -> None:
    ring = [[170, 65], [179.9, 65], [-179.9, 65], [-170, 65], [-170, 69], [-179.9, 69], [179.9, 69], [170, 69], [170, 65]]
    fixed = fix_antimeridian({"type": "Polygon", "coordinates": [ring]})
    xs = [x for x, _ in fixed["coordinates"][0][0]]
    assert min(xs) >= 170 and max(xs) <= 190          # nicht mehr quer über die Welt
    started_east = fix_antimeridian({"type": "Polygon", "coordinates": [ring[2:-1] + ring[:3]]})
    xs = [x for x, _ in started_east["coordinates"][0][0]]
    assert min(xs) >= 170 and max(xs) <= 190
    assert _split_line([[179, 1], [-179, 1], [-178, 2]]) == [[[-179, 1], [-178, 2]]]


def test_country_borders_have_no_artificial_straight_lines() -> None:
    geo = json.loads((REPO_DATA / "countries.geojson").read_text())
    lines = [line for feature in geo["features"] for line in feature["geometry"]["coordinates"]]
    assert all(f["geometry"]["type"] == "MultiLineString" for f in geo["features"])
    assert len(lines) > 50
    for line in lines:
        for (x1, y1), (x2, y2) in zip(line, line[1:]):
            # früher: Schnittkanten des Ausschnitts und Sprünge über die Datumsgrenze
            assert math.hypot(x2 - x1, y2 - y1) < 4, ((x1, y1), (x2, y2))
            for edge_x in (-30.0, 50.0):
                assert not (x1 == x2 == edge_x)
            for edge_y in (29.0, 74.0):
                assert not (y1 == y2 == edge_y)
            # keine Linien bei 65°/69°/71° N quer durch Russland/Barentssee
            assert not (abs(y1 - y2) < 0.02 and abs(x2 - x1) > 3 and y1 > 64)


def test_city_list_contains_former_gaps() -> None:
    cities = json.loads((REPO_DATA / "cities.json").read_text())
    names = {c["name"] for c in cities}
    for name in ("Kopenhagen", "Sarajevo", "Skopje", "Split", "Murmansk", "Nikosia", "Birkirkara"):
        assert name in names, name
    assert "City of London" not in names
    assert not any(c["lat"] < 33.5 for c in cities)     # Madeira liegt außerhalb des Rasters
    munich = next(c for c in cities if c["name"] == "München")
    assert munich["name_local"] == "Munich" and "muenchen" in munich["search"]


# ------------------------------------------------------- Kartenrand
def test_overlay_fades_out_at_the_data_edge(cd: ClimateData) -> None:
    width = 400
    ll, height, width = cd._pixel_grid(width)
    alpha = cd._alpha_calc(ll).astype(float)
    lon = ll[0, :, 1]
    east = lon > cd.bbox["lon_max"] - EDGE_FEATHER_DEG / 4
    inner = (lon > cd.bbox["lon_max"] - 3 * EDGE_FEATHER_DEG) & (lon < cd.bbox["lon_max"] - 1.5 * EDGE_FEATHER_DEG)
    # Die harte gerade Kante am Ostrand ist weg: außen kaum noch Deckkraft, innen unverändert.
    assert alpha[:, east].max() <= 0.2 * 255
    land = alpha[:, inner] > 0
    if land.any():
        assert alpha[:, inner][land].max() == 255
    assert np.all(alpha[0, :] == 0) and np.all(alpha[-1, :] == 0)
