#!/usr/bin/env python3
"""Erzeugt ein *synthetisches* Raster im gleichen Format wie fetch_climate_grid.py.

Nur für die lokale Entwicklung/Test der App gedacht – wenn die Umgebung keinen
Internetzugang zu Open-Meteo hat, aber Karte und Bedienung getestet werden
sollen. Die echten Daten kommen aus dem GitHub-Action-Workflow.

Aufruf:  python scripts/make_dev_grid.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "data"

CFG = dict(lat_min=34.0, lat_max=72.0, lon_min=-25.0, lon_max=45.0, step=0.75,
           start_date="2016-01-01", end_date="2025-12-31", mask_step=0.05)


def main() -> None:
    from global_land_mask import globe

    rng = np.random.default_rng(7)
    lats = np.arange(CFG["lat_min"], CFG["lat_max"] + 1e-9, CFG["step"])
    lons = np.arange(CFG["lon_min"], CFG["lon_max"] + 1e-9, CFG["step"])
    lon2, lat2 = np.meshgrid(lons, lats)
    lat = lat2.ravel()
    lon = lon2.ravel()
    land = globe.is_land(lat, lon)
    lat, lon = lat[land], lon[land]
    print(f"{lat.size} Landpunkte")

    start = np.datetime64(CFG["start_date"])
    end = np.datetime64(CFG["end_date"])
    days = np.arange(start, end + 1)
    n_days = days.size
    day_of_year = days.astype("datetime64[D]").astype(int) % 365.25
    season = np.sin(2 * np.pi * (day_of_year - 105) / 365.25)  # Maximum im Juli

    n_pts = lat.size
    lat_rel = (lat - 52.0) / 18.0        # Nord-Süd-Gradient
    cont = -0.9 * np.exp(-((lon + 10) / 14.0) ** 2)  # maritimer Westen kühler
    base = 20.0 - 11.0 * lat_rel + cont  # Jahresmitteltemperatur

    amp = 9.0 + 5.0 * (np.abs(lat_rel) > 0.4) + 4.0 * (lat > 60)
    noise = rng.normal(0, 3.2, size=(n_pts, n_days))
    tmean = base[:, None] - amp[:, None] * season[None, :] + noise
    trange = 7.0 + 4.0 * (1 - season[None, :]) + rng.normal(0, 1.2, size=(n_pts, n_days))
    tmax = tmean + trange / 2
    tmin = tmean - trange / 2
    tdew = tmean - 2.5 - 3.0 * (1 - season[None, :]) + rng.normal(0, 1.5, size=(n_pts, n_days))

    daylength = 12 + 5.0 * np.tan(np.radians(lat))[:, None] * season[None, :]
    clear = 0.35 + 0.25 * season[None, :] - 0.08 * np.maximum(0, -cont)[:, None]
    sun = np.clip(daylength * (clear + rng.normal(0, 0.12, size=(n_pts, n_days))), 0, 20)

    prec = np.where(rng.random((n_pts, n_days)) < 0.32,
                    rng.gamma(1.1, 3.2, size=(n_pts, n_days)), 0.0)

    def q(a, scale, lo):
        return np.clip(np.rint(a * scale), lo, 127).astype(np.int8)

    store = {
        "tmax": q(tmax, 2.0, -127), "tmin": q(tmin, 2.0, -127), "tdew": q(tdew, 2.0, -127),
        "sun": q(sun, 4.0, 0), "prec": q(prec, 2.0, 0),
    }
    mlat = np.arange(CFG["lat_min"], CFG["lat_max"] + 1e-9, CFG["mask_step"])
    mlon = np.arange(CFG["lon_min"], CFG["lon_max"] + 1e-9, CFG["mask_step"])
    mlon2, mlat2 = np.meshgrid(mlon, mlat)
    mask = globe.is_land(mlat2, mlon2)

    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT / "climate_grid.npz",
        lat=lat.astype(np.float32), lon=lon.astype(np.float32), **store,
        land_mask_packed=np.packbits(mask.reshape(-1)),
        land_mask_shape=np.array(mask.shape, dtype=np.int32),
    )
    meta = {
        "source": "SYNTHETISCHE TESTDATEN (scripts/make_dev_grid.py) – nicht verwenden!",
        "attribution": "keine",
        "model": "synthetic",
        "start_date": str(days[0]), "end_date": str(days[-1]),
        "n_days": int(n_days), "n_years": round(n_days / 365.25, 2), "n_points": int(n_pts),
        "grid_step_deg": CFG["step"],
        "bbox": {"lat_min": CFG["lat_min"], "lat_max": CFG["lat_max"],
                 "lon_min": CFG["lon_min"], "lon_max": CFG["lon_max"]},
        "variables": {k: {"scale": s, "unit": u, "api": "synthetic"} for k, s, u in [
            ("tmax", 2.0, "°C"), ("tmin", 2.0, "°C"), ("tdew", 2.0, "°C"),
            ("sun", 0.25, "h"), ("prec", 0.5, "mm")]},
        "land_mask": {"step": CFG["mask_step"], "shape": list(mask.shape)},
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    (OUT / "climate_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    size = (OUT / "climate_grid.npz").stat().st_size / 1e6
    print(f"geschrieben: {OUT/'climate_grid.npz'} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
