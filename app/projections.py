"""Regional CMIP6 delta-change approximation, not a daily model simulation.

Keep the original daily variability, relative humidity, sunshine and dry days.
Only monthly regional temperature shifts and precipitation intensity ratios are
model-derived. This deliberately exposes the limitations in every period record.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

SCENARIO_LABELS = {"ssp126": "SSP1-2.6", "ssp245": "SSP2-4.5", "ssp585": "SSP5-8.5"}
ASSUMPTIONS = [
    "Regionale monatliche CMIP6-Änderungen auf historische ERA5-Tage übertragen; keine lokalen täglichen Modellläufe.",
    "Tmax und Tmin erhalten dieselbe Änderung der regionalen Mitteltemperatur.",
    "Taupunkt geschätzt bei unveränderter relativer Feuchte (Magnus-Formel; Tagesmitteltemperatur aus Tmin/Tmax).",
    "Sonnenstunden unverändert aus ERA5 übernommen, nicht für die Zukunft projiziert.",
    "Niederschlagsintensität mit regionalem Monatsfaktor skaliert; Trocken-/Nassabfolge und Wettervariabilität unverändert.",
    "Modellstreuung bezieht sich auf regionale Monatsänderungen, nicht auf ein Unsicherheitsintervall der Wohlfühltage.",
]


def estimate_dewpoint(tmean_c, dewpoint_c, delta_c):
    """Magnus over liquid water, assuming constant RH; returns unquantized °C.

    Uses daily Tmax/Tmin midpoint as a proxy for Tmean, not measured mean RH.
    Baseline saturation is capped at 100% to avoid dewpoint exceeding Tmean.
    """
    a, b = 17.625, 243.04
    rh_log = np.minimum(a * dewpoint_c / (b + dewpoint_c)
                        - a * tmean_c / (b + tmean_c), 0.0)
    warmed = tmean_c + delta_c
    gamma = rh_log + a * warmed / (b + warmed)
    return b * gamma / (a - gamma)


def _inside(lon: np.ndarray, lat: np.ndarray, geometry: dict) -> np.ndarray:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    result = np.zeros(lon.shape, dtype=bool)
    for polygon in polygons:
        inside_polygon = np.zeros(lon.shape, dtype=bool)
        for ring_index, ring in enumerate(polygon):
            inside_ring = np.zeros(lon.shape, dtype=bool)
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                if y1 == y2:
                    continue
                crossing = ((y1 > lat) != (y2 > lat)) & (lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1)
                inside_ring ^= crossing
            if ring_index == 0:
                inside_polygon = inside_ring
            else:
                inside_polygon &= ~inside_ring
        result |= inside_polygon
    return result


class ProjectionCatalog:
    def __init__(self, path: Path, lat: np.ndarray, lon: np.ndarray, start_year: int, end_year: int):
        self.data = json.loads(path.read_text())
        d = self.data
        if d.get("schema_version") != 1 or d.get("method") != "regional_monthly_delta_change":
            raise ValueError("Unsupported projection manifest")
        if d["baseline"] != {"start_year": start_year, "end_year": end_year}:
            raise ValueError("Projection baseline does not match the ERA5 dataset; rebuild projections")
        if len(d["models"]) < 10 or set(d["scenarios"]) != set(SCENARIO_LABELS):
            raise ValueError("Incomplete projection ensemble")
        self.region_keys = list(d["regions"])
        if set(self.region_keys) != {"GIC", "NEU", "WCE", "EEU", "MED", "RAR", "WCA"}:
            raise ValueError("Incomplete regional coverage")
        self.region_index = np.full(lat.shape, -1, dtype=int)
        for i, key in enumerate(self.region_keys):
            geometry = d["regions"][key]["geometry"]
            self.region_index[_inside(lon, lat, geometry)] = i
        # Points exactly on a region edge or outside its land polygon use the
        # nearest polygon vertex (latitude-scaled distance), not a global delta.
        missing = self.region_index < 0
        self.nearest_region_points = int(missing.sum())
        if missing.any():
            distances = []
            for key in self.region_keys:
                geometry = d["regions"][key]["geometry"]
                polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
                vertices = np.asarray([v for polygon in polygons for v in polygon[0]])
                distance = ((vertices[:, 0, None] - lon[missing]) * np.cos(np.radians(lat[missing]))) ** 2
                distance += (vertices[:, 1, None] - lat[missing]) ** 2
                distances.append(distance.min(axis=0))
            self.region_index[missing] = np.argmin(distances, axis=0)
        self.tables = {}
        self.records = {}
        for scenario, windows in d["scenarios"].items():
            if set(windows) != {"2030", "2050"}:
                raise ValueError("Missing projection window")
            for year, window in windows.items():
                expected = (2021, 2040) if year == "2030" else (2041, 2060)
                if (window["start_year"], window["end_year"]) != expected:
                    raise ValueError("Incorrect future window")
                identifier = year if scenario == "ssp245" else f"{year}-{scenario}"
                temperature, precipitation = [], []
                for key in self.region_keys:
                    region = window["regions"][key]
                    required = {"temperature_delta_c", "temperature_delta_p10_c", "temperature_delta_p90_c",
                                "precipitation_ratio", "precipitation_ratio_p10", "precipitation_ratio_p90"}
                    if set(region) != required:
                        raise ValueError("Incomplete monthly variables/percentiles")
                    for name, values in region.items():
                        array = np.asarray(values, dtype=float)
                        if array.shape != (12,) or not np.isfinite(array).all():
                            raise ValueError(f"Invalid monthly array: {key}/{name}")
                    temp = np.asarray(region["temperature_delta_c"], dtype=np.float32)
                    rain = np.asarray(region["precipitation_ratio"], dtype=np.float32)
                    if np.any(np.abs(temp) > 15) or np.any(rain <= 0) or np.any(rain > 5):
                        raise ValueError("Implausible regional changes")
                    if (np.any(np.asarray(region["temperature_delta_p10_c"]) > temp + 1e-5)
                            or np.any(np.asarray(region["temperature_delta_p90_c"]) < temp - 1e-5)
                            or np.any(np.asarray(region["precipitation_ratio_p10"]) > rain + 1e-5)
                            or np.any(np.asarray(region["precipitation_ratio_p90"]) < rain - 1e-5)):
                        raise ValueError("Unordered ensemble percentiles")
                    temperature.append(temp)
                    precipitation.append(rain)
                self.tables[identifier] = (np.asarray(temperature)[self.region_index],
                                           np.asarray(precipitation)[self.region_index])
                self.records[identifier] = {
                    "id": identifier, "label": f"Projektion {year} · {SCENARIO_LABELS[scenario]} (Näherung)",
                    "available": True, "projection": True, "approximation": True,
                    "start_date": f"{window['start_year']}-01-01", "end_date": f"{window['end_year']}-12-31",
                    "years": window["end_year"] - window["start_year"] + 1,
                    "baseline": d["baseline"], "scenario": scenario, "model_count": len(d["models"]),
                    "source": d["source"], "source_url": d["source_url"], "license": d["license"],
                    "method": d["method"], "assumptions": ASSUMPTIONS,
                    "nearest_region_points": self.nearest_region_points,
                    "description": f"Klimamodell-basierte Näherung für {window['start_year']}–{window['end_year']}: "
                        f"{len(d['models'])} CMIP6-Modelle, {SCENARIO_LABELS[scenario]}. Regionale monatliche Ensemble-Mediane "
                        f"relativ zu {start_year}–{end_year}, auf ERA5-Tage übertragen. Taupunkt geschätzt, Sonne unverändert; "
                        "keine Prognose einzelner Wettertage und keine Änderung von Extremvariabilität.",
                }

    def point_info(self, identifier: str, point: int) -> dict:
        record = self.records[identifier]
        region_key = self.region_keys[int(self.region_index[point])]
        year = identifier.split("-")[0]
        return {"region": region_key, "region_name": self.data["regions"][region_key]["name"],
                **self.data["scenarios"][record["scenario"]][year]["regions"][region_key]}
