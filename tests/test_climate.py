"""Tests für Zähl-Kern und Rendering (pytest).

Die Tests brauchen ein vorhandenes Raster in ``app/data/climate_grid.npz`` –
entweder die echten Daten oder das Testraster aus ``scripts/make_dev_grid.py``.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from app.climate import SCALES, ClimateData, Params

DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"
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
