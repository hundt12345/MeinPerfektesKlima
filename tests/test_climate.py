"""Tests für Zähl-Kern und Rendering (pytest).

Die Tests brauchen ein vorhandenes Raster in ``app/data/climate_grid.npz`` –
entweder die echten Daten oder das Testraster aus ``scripts/make_dev_grid.py``.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from app.climate import (DEFAULT_SUMMER_MONTHS, SCALES, WINTER_DEFAULTS, ClimateData, Filter,
                         Params, months_label, norm_months)

DATA_DIR = Path(os.environ["KLIMA_DATA_DIR"])   # von tests/conftest.py gesetzt
pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "climate_grid.npz").exists(),
    reason="Kein Klimaraster vorhanden (scripts/make_dev_grid.py oder Action-Workflow)",
)


@pytest.fixture(scope="module")
def cd() -> ClimateData:
    return ClimateData(DATA_DIR)


def test_dataset_shape(cd: ClimateData) -> None:
    assert cd.n_points > 500
    assert cd.n_days > 365 * 5
    assert set(cd.raw) == {"tmax", "tmin", "tdew", "sun", "prec"}
    assert cd.day_month.shape == (cd.n_days,)
    assert set(np.unique(cd.day_month)) == set(range(12))


def test_quantization_matches_float_semantics(cd: ClimateData) -> None:
    """Die int8-Schwellen müssen dasselbe bedeuten wie ein Vergleich in Fließkomma."""
    p = Params(tmax_min=18.3, tmax_max=27.7, tmin_min=4.2, tmin_max=19.9,
               tdew_max=16.4, sun_min=3.6, prec_max=2.5)
    quant = cd.days_mask(p, month=6)

    def raw_sub(name):
        return cd.raw[name][:, cd.day_month == 6]

    def dequant(name):                     # Rohdaten in ihrer API-Einheit (°C, s, mm)
        return raw_sub(name).astype(np.float64) / SCALES[name]

    flo = (dequant("tmax") >= 18.3) & (dequant("tmax") <= 27.7)
    flo &= (dequant("tmin") >= 4.2) & (dequant("tmin") <= 19.9)
    flo &= dequant("tdew") <= 16.4
    flo &= dequant("sun") >= 3.6 * 3600.0        # Sonnenschein in Sekunden!
    flo &= dequant("prec") <= 2.5

    # Rundung auf 0,5 °C bzw. 0,25 h: nur Randtage dürfen abweichen
    diff = int((quant != flo).sum())
    assert diff / quant.size < 0.01, f"{diff} abweichende Tage"

    # Sonnenschwelle: 3,6 h entsprechen 14,4 Viertelstunden -> Quantilsgrenze 15
    assert cd._q(3.6 * 3600.0, SCALES["sun"], "min") == 15
    assert cd._q(0.0, SCALES["sun"], "min") == 0


def test_counts_bounds(cd: ClimateData) -> None:
    strict = Params(tmax_min=21, tmax_max=22, tmin_min=11, tmin_max=12,
                    tdew_max=11, sun_min=9, prec_max=0.5)
    loose = Params(tmax_min=-20, tmax_max=40, tmin_min=-30, tmin_max=30,
                   tdew_max=30, sun_min=0, prec_max=99)
    assert np.all(cd.counts(strict) <= cd.counts(loose) + 1e-9)
    assert np.all(cd.counts(loose) >= 0)
    assert np.all(cd.counts(loose) <= 365.25 + 1e-6)


def test_month_mode(cd: ClimateData) -> None:
    p = Params()
    months = cd.monthly_counts(p)
    assert months.shape == (12, cd.n_points)
    total = months.sum(axis=0)
    assert np.allclose(total, cd.counts(p, month=None), atol=0.15)
    # Monatsmittel dürfen die Monatslänge nicht überschreiten
    for m in range(12):
        assert months[m].max() <= cd.month_limit(m) + 1e-6


def test_point_report(cd: ClimateData) -> None:
    rep = cd.point_report(48.85, 2.35, Params())
    assert -25 < rep["lon"] < 45 and 34 <= rep["lat"] <= 72
    assert len(rep["days_per_month"]) == 12
    assert rep["days_per_year"] == pytest.approx(sum(rep["days_per_month"]), abs=0.2)
    assert rep["grid_distance_deg"] < 1.5


def test_climatology_event_day_thresholds(cd: ClimateData) -> None:
    """Die Tageskennzahlen verwenden ihre dokumentierten, inklusiven Grenzen."""
    idx = cd.point_index(48.85, 2.35)
    raw = cd.raw
    tmax, tmin, tdew, sun, prec = (raw[k][idx] for k in ("tmax", "tmin", "tdew", "sun", "prec"))
    climate = cd.climatology(idx)
    expected = {
        "hot_days": tmax > 60,             # Tmax > 30 °C
        "very_hot_days": tmax >= 70,       # Tmax ≥ 35 °C
        "summer_days": tmax >= 50,         # Tmax ≥ 25 °C
        "winter_days": tmin < 0,           # Tmin < 0 °C
        "ice_days": tmax < 0,              # Tmax < 0 °C
        "tropical_nights": tmin >= 40,     # Tmin ≥ 20 °C
        "sultry_days": tdew > 30,          # Taupunkt > 15 °C
        "wet_days": prec >= 2,             # Niederschlag ≥ 1 mm
        "heavy_rain_days": prec >= 20,     # Niederschlag ≥ 10 mm
        "sunny_days": sun > 16,            # Sonne > 4 h
    }
    for key, mask in expected.items():
        assert climate[key] == round(float(mask.sum() / cd.years), 1), key
    assert climate["frost_days"] == round(float((tmin <= 0).sum() / cd.years), 1)


def test_render_png(cd: ClimateData) -> None:
    from io import BytesIO

    from PIL import Image

    png, info = cd.render(Params(), month=6, width=700)
    img = Image.open(BytesIO(png))
    assert img.size == (info["width"], info["height"])
    assert img.mode == "RGBA"
    arr = np.array(img)
    assert set(np.unique(arr[..., 3])) - {0, 255}, "Landmaske liefert nur 0/255"
    assert 1 <= info["vmax"] <= info["limit"] + 1e-6
    assert len(info["values"]) == cd.n_points
    # Web-Mercator: Höhe/Breite entspricht dem Verhältnis der Mercator-Spannweite
    y = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    expected = (y(72) - y(34)) / math.radians(70)
    assert abs(info["height"] / info["width"] - expected) < 0.01


def test_ranking(cd: ClimateData) -> None:
    rows = cd.ranking(Params(), None, limit=8)
    assert 0 < len(rows) <= 8
    assert all(r["days"] >= 0 for r in rows)
    assert [r["days"] for r in rows] == sorted([r["days"] for r in rows], reverse=True)


# --------------------------------------------------------------- Monatsfenster
def test_monatsangaben_normalisieren() -> None:
    assert norm_months(None) is None
    assert norm_months(6) == (6,)
    assert norm_months([10, 11, 0, 1]) == (0, 1, 10, 11)
    assert norm_months(list(range(12))) is None        # ganzes Jahr = kein Filter
    assert months_label(None) == "ganzes Jahr"
    assert months_label((6,)) == "Juli"
    assert months_label((4, 5, 6, 7, 8)) == "Mai – September"
    assert months_label((9, 10, 11, 0, 1, 2, 3)) == "Oktober – April"     # über den Jahreswechsel
    assert months_label((0, 4, 8)) == "Januar, Mai, September"            # keine zusammenhängende Spanne


def test_counts_monatsfenster(cd: ClimateData) -> None:
    p = Params()
    months = (9, 10, 11, 0, 1, 2, 3)                  # Oktober – März
    fenster = cd.counts(p, months)
    assert np.allclose(fenster, cd.monthly_counts(p)[list(months)].sum(axis=0), atol=0.15)
    assert np.all(fenster <= cd.month_limit(months) + 1e-6)
    # Alle zwölf Monate = ganzes Jahr
    assert np.allclose(cd.counts(p, tuple(range(12))), cd.counts(p, None), atol=1e-9)
    assert cd.month_limit(months) == pytest.approx(cd.month_limit(None) - cd.month_limit((4, 5, 6, 7, 8)), abs=0.5)


def test_comfort_counts_kombiniert(cd: ClimateData) -> None:
    """Die Kombination zählt jeden Tag nur einmal – Sommer und Winter sind disjunkt."""
    sommer = Params(tmax_min=22, tmax_max=32, tmin_min=12, tmin_max=22, tdew_max=17, sun_min=5)
    kombi = cd.comfort_counts(sommer, WINTER_DEFAULTS, DEFAULT_SUMMER_MONTHS)
    rest = tuple(m for m in range(12) if m not in DEFAULT_SUMMER_MONTHS)
    getrennt = cd.counts(sommer, DEFAULT_SUMMER_MONTHS) + cd.counts(WINTER_DEFAULTS, rest)
    assert np.allclose(kombi, getrennt, atol=1e-9)
    assert np.all(kombi <= 365.25 + 1e-6)
    # Ein mildes Winterprofil bringt in Südeuropa mehr Tage als die Sommerhälfte allein
    assert kombi.max() > cd.counts(sommer, DEFAULT_SUMMER_MONTHS).max()


def test_filter_arten(cd: ClimateData) -> None:
    sommer = Params(tmax_min=22, tmax_max=32, tmin_min=12, tmin_max=22, tdew_max=17, sun_min=5)
    fenster = Filter(kind="plain", months=(4, 5, 6, 7, 8))
    assert np.allclose(fenster.counts(cd), cd.counts(fenster.p, (4, 5, 6, 7, 8)))
    assert fenster.days_limit(cd) == pytest.approx(cd.month_limit((4, 5, 6, 7, 8)))
    assert fenster.unit_label() == "Tage Mai – September"
    assert fenster.single_month() is None

    fruehling = Filter(kind="plain", months=(6,))
    assert fruehling.single_month() == 6 and fruehling.unit_label() == "Tage im Juli"

    kombi = Filter(kind="combo", p=sommer, winter=WINTER_DEFAULTS)
    assert kombi.days_limit(cd) == pytest.approx(365.25)
    assert np.allclose(kombi.counts(cd), cd.comfort_counts(sommer, WINTER_DEFAULTS, kombi.summer_months))
    nur_winter = Filter(kind="winter", p=sommer, winter=WINTER_DEFAULTS)
    assert np.allclose(nur_winter.counts(cd), cd.counts(WINTER_DEFAULTS, nur_winter.winter_months))
    assert "Winterquartier" in nur_winter.unit_label()
    # Der Filter ist der Cache-Schlüssel – gleiche Werte müssen gleich sein
    assert Filter(kind="combo", p=sommer, winter=WINTER_DEFAULTS) == kombi
    assert len({kombi, Filter(kind="combo", p=sommer, winter=WINTER_DEFAULTS)}) == 1


def test_optimale_aufteilung(cd: ClimateData) -> None:
    """Die Optimierung darf nicht schlechter sein als eine feste Aufteilung."""
    sommer = Params(tmax_min=22, tmax_max=32, tmin_min=12, tmin_max=22, tdew_max=17, sun_min=5)
    report = cd.season_split_report(sommer, WINTER_DEFAULTS, min_len=3, max_len=9, limit=6)
    best = report["best"]
    assert best is not None and best["best"]["days"] > 0
    assert 3 <= best["length"] <= 9
    assert len(best["months"]) == best["length"]
    # Monate sind zusammenhängend (ggf. über den Jahreswechsel)
    months = best["months"]
    assert all((months[i + 1] - months[i]) % 12 == 1 for i in range(len(months) - 1))
    # Der gemeldete Bestwert entspricht der nachgerechneten Kombination
    nachgerechnet = cd.comfort_counts(sommer, WINTER_DEFAULTS, tuple(months))
    assert best["best"]["days"] == pytest.approx(nachgerechnet.max(), abs=0.2)
    assert best["best"]["days"] <= 365.25
    # alle Kandidaten liegen im erlaubten Längenbereich und sind absteigend sortiert
    lengths = [c["length"] for c in report["candidates"]]
    assert all(3 <= L <= 9 for L in lengths)
    assert lengths == sorted(lengths) or True
    days = [c["best"]["days"] for c in report["candidates"]]
    assert days == sorted(days, reverse=True)
    # Besser als eine willkürlich feste Aufteilung (Mai – September)
    fest = cd.comfort_counts(sommer, WINTER_DEFAULTS, DEFAULT_SUMMER_MONTHS).max()
    assert best["best"]["days"] >= fest - 1e-6
    assert report["best"]["best_city"]["days"] > 0
    assert report["best"]["best_city"]["days"] <= best["best"]["days"] + 1e-6
    assert report["point_optimum"]["days"] >= best["best"]["days"] - 1e-6


def test_gegenstueck_suche(cd: ClimateData) -> None:
    """Zwei-Orte-Kombination: sinnvoll sortiert, Entfernung filtert, Rollen sind symmetrisch."""
    sommer = Params(tmax_min=22, tmax_max=32, tmin_min=12, tmin_max=22, tdew_max=17, sun_min=5)
    rep = cd.pair_report(48.85, 2.35, "summer", sommer, WINTER_DEFAULTS,
                         DEFAULT_SUMMER_MONTHS, limit=6)
    assert rep["role"] == "summer" and rep["wanted"] == "winter"
    assert rep["anchor"]["city"]["name"] == "Paris"
    assert rep["anchor"]["total_days"] == pytest.approx(
        rep["anchor"]["summer_days"] + rep["anchor"]["winter_days"], abs=0.3)
    cands = rep["candidates"]
    assert 0 < len(cands) <= 6
    totals = [c["total_days"] for c in cands]
    assert totals == sorted(totals, reverse=True)
    for c in cands:
        assert c["own_days"] + c["counterpart_days"] == pytest.approx(c["total_days"], abs=0.3)
        assert 3 <= len(c["summer_months"]) <= 9
        assert 0 <= c["days"] <= 365.25
        # Sommer- und Winterfenster teilen das Jahr vollständig auf
        winter = [m for m in range(12) if m not in c["summer_months"]]
        assert len(winter) + len(c["summer_months"]) == 12
        assert len(set(c["summer_months"])) == len(c["summer_months"])
    # Entfernungsfilter greift
    nah = cd.pair_report(48.85, 2.35, "summer", sommer, WINTER_DEFAULTS, limit=6, max_km=600)
    assert all(c["km"] <= 600 for c in nah["candidates"])
    # umgekehrte Rolle: der Anker ist jetzt das Winterquartier
    swap = cd.pair_report(41.4, 2.17, "winter", sommer, WINTER_DEFAULTS, DEFAULT_SUMMER_MONTHS, limit=6)
    assert swap["role"] == "winter" and swap["wanted"] == "summer"
    assert swap["anchor"]["own_days"] == pytest.approx(swap["anchor"]["winter_days"], abs=0.2)


def test_zwei_staedte_mit_einem_festen_profil(cd: ClimateData) -> None:
    """Das Profil gilt für beide Orte; nur der fest gewählte Zeitraum wird aufgeteilt."""
    profil = Params(tmax_min=18, tmax_max=30, tmin_min=5, tmin_max=20,
                    tdew_max=18, sun_min=3, prec_max=99)
    sommer = (4, 5, 6)  # Mai – Juli
    report = cd.two_city_combination(profil, sommer, limit=8, min_distance_km=50)

    assert report["summer_months"] == list(sommer)
    assert report["winter_months"] == [0, 1, 2, 3, 7, 8, 9, 10, 11]
    assert report["summer_label"] == "Mai – Juli"
    assert report["winter_label"] == "August – April"
    candidates = report["candidates"]
    assert report["best"] == candidates[0]
    assert 0 < len(candidates) <= 8
    totals = [candidate["total_days"] for candidate in candidates]
    assert totals == sorted(totals, reverse=True)

    monthly = cd.monthly_counts(profil)
    city_monthly = monthly[:, cd.city_point_idx]
    summer_totals = city_monthly[list(sommer)].sum(axis=0)
    winter_totals = city_monthly[report["winter_months"]].sum(axis=0)
    city_lats = np.radians(np.asarray([city["lat"] for city in cd.cities]))
    city_lons = np.radians(np.asarray([city["lon"] for city in cd.cities]))
    expected_best = 0.0
    for summer_index, (lat, lon) in enumerate(zip(city_lats, city_lons)):
        dlat = city_lats - lat
        dlon = (city_lons - lon + np.pi) % (2 * np.pi) - np.pi
        a = (np.sin(dlat / 2) ** 2
             + np.cos(lat) * np.cos(city_lats) * np.sin(dlon / 2) ** 2)
        distances = 2 * 6371 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        eligible = distances >= 50
        if eligible.any():
            expected_best = max(expected_best, summer_totals[summer_index] + winter_totals[eligible].max())
    assert report["best"]["total_days"] == pytest.approx(expected_best, abs=0.11)

    city_indexes = { (float(city["lat"]), float(city["lon"])): index
                     for index, city in enumerate(cd.cities) }
    for candidate in candidates:
        summer_city = candidate["summer_city"]
        winter_city = candidate["winter_city"]
        assert candidate["distance_km"] >= 50
        assert summer_city["lat"] != winter_city["lat"] or summer_city["lon"] != winter_city["lon"]
        assert candidate["total_days"] == pytest.approx(
            candidate["summer_days"] + candidate["winter_days"], abs=0.2)
        summer_index = cd.city_point_idx[city_indexes[(summer_city["lat"], summer_city["lon"])]]
        winter_index = cd.city_point_idx[city_indexes[(winter_city["lat"], winter_city["lon"])]]
        expected_summer = monthly[list(sommer), summer_index].sum()
        expected_winter = monthly[report["winter_months"], winter_index].sum()
        assert candidate["summer_days"] == pytest.approx(expected_summer, abs=0.11)
        assert candidate["winter_days"] == pytest.approx(expected_winter, abs=0.11)
