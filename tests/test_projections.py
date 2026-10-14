"""Approximation physics, monthly source data, gating and consistent endpoint semantics."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.climate import ClimateData, Params
from app.main import app
from app.projections import ProjectionCatalog, estimate_dewpoint
from scripts.build_projections import REGIONS, monthly_means

DATA = Path(os.environ["KLIMA_DATA_DIR"])
MANIFEST = Path(__file__).parents[1] / "app/data/projections.json"


@pytest.fixture(scope="module")
def cd():
    return ClimateData(DATA)


def test_dewpoint_constant_rh_and_no_int8_overflow():
    temperature = np.array([0., 20., 35.])
    dewpoint = np.array([-5., 10., 22.])
    changed = estimate_dewpoint(temperature, dewpoint, 2.)
    assert np.all(changed > dewpoint)
    assert np.all(changed < temperature + 2.)
    a, b = 17.625, 243.04
    original_rh = np.exp(a * dewpoint / (b + dewpoint) - a * temperature / (b + temperature))
    new_rh = np.exp(a * changed / (b + changed) - a * (temperature + 2) / (b + temperature + 2))
    assert np.allclose(original_rh, new_rh)
    assert np.allclose(estimate_dewpoint(temperature, dewpoint, 0), dewpoint)


@pytest.mark.parametrize("period", ["2030", "2050", "2050-ssp126", "2050-ssp585"])
def test_projected_daily_values_and_climatology(cd, period):
    point = cd.point_index(50.94, 6.96)
    sel = slice(0, cd.n_days)
    delta, ratio = cd.projections.tables[period]
    months = cd.day_month
    future_max = cd._raw_for("tmax", sel, period, point)[0] / 2
    future_min = cd._raw_for("tmin", sel, period, point)[0] / 2
    assert np.allclose(future_max - cd.raw["tmax"][point] / 2., delta[point, months], atol=3e-6)
    assert np.allclose(future_min - cd.raw["tmin"][point] / 2., delta[point, months], atol=3e-6)
    assert np.array_equal(cd._raw_for("sun", sel, period, point)[0], cd.raw["sun"][point])
    assert np.allclose(cd._raw_for("prec", sel, period, point)[0], cd.raw["prec"][point] * ratio[point, months])
    p = Params()
    climate = cd.climatology(point, period)
    assert climate["year"]["tmax"] == pytest.approx(future_max.mean(), abs=.051)
    assert climate["months"]["sun"] == cd.climatology(point, "all")["months"]["sun"]
    assert climate["projection"]["region"] == "WCE"
    assert np.allclose(cd.monthly_counts(p, period).sum(axis=0), cd.counts(p, period=period))
    # Daily comparison must use float thresholds, not rounded int8 bounds.
    p = Params(tmax_min=18.3, tmax_max=27.7, tmin_min=4.2, tmin_max=19.9,
               tdew_max=16.4, sun_min=3.6, prec_max=2.5)
    future_dew = cd._raw_for("tdew", sel, period, point)[0] / 2
    expected = ((future_max >= p.tmax_min) & (future_max <= p.tmax_max)
                & (future_min >= p.tmin_min) & (future_min <= p.tmin_max)
                & (future_dew <= p.tdew_max) & (cd.raw["sun"][point] / 4. >= p.sun_min)
                & (cd._raw_for("prec", sel, period, point)[0] / 2 <= p.prec_max))
    assert np.array_equal(cd.days_mask(p, point=point, period=period)[0], expected)


def test_manifest_regions_and_scenarios(cd):
    assert len(cd.projections.data["models"]) == 31
    assert cd.projections.nearest_region_points == 0
    for lat, lon, region in [(50.94, 6.96, "WCE"), (41.4, 2.17, "MED"),
                             (59.91, 10.75, "NEU"), (64.14, -21.94, "GIC")]:
        point = cd.point_index(lat, lon)
        assert cd.projections.point_info("2050", point)["region"] == region
    assert not np.array_equal(cd.projections.tables["2050"][0], cd.projections.tables["2050-ssp585"][0])
    info = cd.period_info("2050")
    assert info["years"] == 20
    assert info["analog_sample_years"] == pytest.approx(10, abs=.02)
    assert info["approximation"] is True
    assert info["scenario"] == "ssp245"
    assert len(info["assumptions"]) == 6


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "baseline", "monthly"])
def test_manifest_failure_never_enables_estimates(tmp_path, monkeypatch, damage):
    path = tmp_path / "projections.json"
    if damage == "invalid_json":
        path.write_text("not json")
    elif damage != "missing":
        data = json.loads(MANIFEST.read_text())
        if damage == "baseline":
            data["baseline"]["start_year"] = 1981
        else:
            data["scenarios"]["ssp245"]["2050"]["regions"]["WCE"]["temperature_delta_c"] = [float("nan")] * 12
        path.write_text(json.dumps(data))
    monkeypatch.setenv("KLIMA_PROJECTIONS_FILE", str(path))
    climate = ClimateData(DATA)
    assert climate.projections is None
    assert climate.projection_error
    assert not next(p for p in climate.period_options() if p["id"] == "2050")["available"]
    with pytest.raises(ValueError):
        climate.counts(Params(), period="2050")


def test_monthly_builder_averages_and_rejects_gaps():
    header = '"date",' + ','.join(REGIONS)
    lines = [header] + [f'2021-{m:02d},' + ','.join([str(m)] * len(REGIONS)) for m in range(1, 13)]
    array = monthly_means('\n'.join(lines), 2021, 2021)
    assert array.shape == (7, 12)
    assert np.array_equal(array[0], np.arange(1, 13))
    with pytest.raises(ValueError, match="Incomplete"):
        monthly_means('\n'.join(lines[:-1]), 2021, 2021)
    with pytest.raises(ValueError, match="Duplicate"):
        monthly_means('\n'.join(lines + [lines[-1]]), 2021, 2021)


def test_all_projection_endpoints(cd):
    client = TestClient(app)
    q = {"period": "2050-ssp585"}
    map_response = client.get("/api/map", params={**q, "width": 320}).json()
    assert map_response["period_info"]["approximation"]
    assert map_response["period_info"]["scenario"] == "ssp585"
    png = client.get("/api/map.png", params={**q, "width": 320})
    assert png.content.startswith(b"\x89PNG")
    point = client.get("/api/point", params={**q, "lat": 50.94, "lon": 6.96}).json()
    assert point["period"]["id"] == q["period"]
    assert point["climate"]["projection"]["region"] == "WCE"
    ranking = client.get("/api/ranking", params={**q, "limit": 3}).json()
    assert ranking["period"] == q["period"]
    cities = client.get("/api/cities/climate", params=q).json()
    assert cities["city_method"] == "nearest_grid_point_regional_delta"
    assert cities["period"]["approximation"]
    for season in ("winter", "summer"):
        result = client.get("/api/winterquarters/escape", params={**q, "lat": 50.94, "lon": 6.96, "season": season}).json()
        assert result["period"]["approximation"]
        assert result["best"]["total_days"] - result["baseline_days"] == pytest.approx(result["best"]["gain_days"], abs=.11)
    assert client.get("/api/map", params={"period": "2050-rcp85"}).status_code == 400
