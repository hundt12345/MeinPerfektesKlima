#!/usr/bin/env python3
"""Holt tägliche ERA5-Klimadaten für ein Europa-Raster über die Open-Meteo-API
und speichert sie kompakt als ``app/data/climate_grid.npz`` (+ Metadaten).

Das Skript braucht offenen Internetzugang zu ``archive-api.open-meteo.com`` und
läuft deshalb im GitHub-Action-Workflow ``.github/workflows/build-climate-data.yml``
(normale Sandboxes/CI ohne Netzzugang können die API nicht erreichen).

Ausgabe:
  app/data/climate_grid.npz    tmax, tmin, tdew, sun, prec (int8), lat, lon,
                               landmaske (gepackte Bits), Raster-Geometrie
  app/data/climate_meta.json   Zeitraum, Skalen, Quelle, Raster-Info

Aufruf:
  python scripts/fetch_climate_grid.py                 # volle Konfiguration
  python scripts/fetch_climate_grid.py --test          # kleines Testraster
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
API = "https://archive-api.open-meteo.com/v1/archive"

# Daily-Variablen der Open-Meteo-API -> Name im Datensatz
VARIABLES = {
    "tmax": "temperature_2m_max",
    "tmin": "temperature_2m_min",
    "tdew": "dew_point_2m_mean",
    "sun": "sunshine_duration",       # Sekunden pro Tag
    "prec": "precipitation_sum",      # mm pro Tag
}
SCALES = {  # Quantisierung auf int8: q = clip(round(v * scale), 0/-127, 127)
    "tmax": 2.0, "tmin": 2.0, "tdew": 2.0,   # 0,5 °C
    "sun": 4.0 / 3600.0,                     # 0,25 h  (sunshine_duration in s)
    "prec": 2.0,                             # 0,5 mm
}
UNITS = {"tmax": "°C", "tmin": "°C", "tdew": "°C", "sun": "h", "prec": "mm"}

DEFAULT_CFG = {
    "lat_min": 34.0, "lat_max": 72.0,
    "lon_min": -25.0, "lon_max": 45.0,
    "step": 0.75,
    "start_date": "2016-01-01",
    "end_date": "2025-12-31",
    "model": "era5",
    "mask_step": 0.05,
    "batch_points": 20,
    "chunk_years": 5,
    "threads": 6,
}


def log(msg: str) -> None:
    print(f"[{datetime.utcnow():%H:%M:%S}] {msg}", flush=True)


def http_json(url: str, tries: int = 6, timeout: int = 300):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "meinperfektesklima-databuild"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(60, 3 * 2 ** attempt)
            log(f"  ! Fehler ({exc}); neuer Versuch in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Anfrage endgültig fehlgeschlagen: {last}\n{url[:200]}")


def build_grid(cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """Reguläres Raster über Europa, gefiltert auf Landpunkte."""
    from global_land_mask import globe

    lats = np.arange(cfg["lat_min"], cfg["lat_max"] + 1e-9, cfg["step"])
    lons = np.arange(cfg["lon_min"], cfg["lon_max"] + 1e-9, cfg["step"])
    lon2, lat2 = np.meshgrid(lons, lats)
    lat_flat, lon_flat = lat2.ravel(), lon2.ravel()
    land = globe.is_land(lat_flat, lon_flat)
    if land.dtype != bool:
        land = land > 0.5
    log(f"Raster {len(lats)}x{len(lons)} = {lat_flat.size} Punkte, davon Land: {int(land.sum())}")
    return lat_flat[land], lon_flat[land]


def fetch_chunk(lat: np.ndarray, lon: np.ndarray, start: str, end: str, model: str) -> dict[str, np.ndarray]:
    """Ein API-Aufruf für mehrere Punkte und einen Zeitraum."""
    query = {
        "latitude": ",".join(f"{v:.4f}" for v in lat),
        "longitude": ",".join(f"{v:.4f}" for v in lon),
        "start_date": start,
        "end_date": end,
        "daily": ",".join(VARIABLES.values()),
        "timezone": "UTC",
        "models": model,
    }
    names = list(VARIABLES)
    url = f"{API}?{urllib.parse.urlencode(query)}"
    data = http_json(url)
    if isinstance(data, dict):
        data = [data]
    out: dict[str, list[np.ndarray]] = {k: [] for k in names}
    times: list[str] | None = None
    for entry in data:
        daily = entry.get("daily") or {}
        t = daily.get("time") or []
        if times is None:
            times = t
        for k, api_name in VARIABLES.items():
            vals = daily.get(api_name)
            if vals is None:
                raise RuntimeError(f"Variable {api_name} fehlt in der Antwort")
            out[k].append(np.array(vals, dtype=np.float64))
    result = {k: np.vstack(v) for k, v in out.items()}
    result["_time"] = np.array(times, dtype="datetime64[D]")  # type: ignore[assignment]
    return result


def fetch_all(lat: np.ndarray, lon: np.ndarray, cfg: dict, limit: int | None = None) -> tuple[dict[str, np.ndarray], np.ndarray]:
    n = lat.size
    if limit:
        n = min(n, limit)
        lat, lon = lat[:n], lon[:n]
        log(f"TESTMODUS: nur die ersten {n} Punkte")

    start, end = cfg["start_date"], cfg["end_date"]
    y0, y1 = int(start[:4]), int(end[:4])
    chunks = []
    for y in range(y0, y1 + 1, cfg["chunk_years"]):
        cs = max(start, f"{y}-01-01")
        ce = min(end, f"{min(y + cfg['chunk_years'] - 1, y1)}-12-31")
        chunks.append((cs, ce))
    log(f"Zeiträume: {chunks}")

    batch = cfg["batch_points"]
    jobs = []
    for i in range(0, n, batch):
        for cs, ce in chunks:
            jobs.append((i, cs, ce))
    log(f"{len(jobs)} API-Aufrufe, {cfg['threads']} Threads")

    results: list[tuple[int, str, str, dict]] = []
    failures: list[tuple[int, str, str]] = []

    def work(job):
        i, cs, ce = job
        j = min(i + batch, n)
        return job, fetch_chunk(lat[i:j], lon[i:j], cs, ce, cfg["model"])

    with ThreadPoolExecutor(max_workers=cfg["threads"]) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        done = 0
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                _, res = fut.result()
                results.append((job[0], job[1], job[2], res))
            except Exception as exc:  # noqa: BLE001
                log(f"  ! Batch ab {job[0]} ({job[1]}..{job[2]}) fehlgeschlagen: {exc}")
                failures.append(job)
            done += 1
            if done % 20 == 0 or done == len(jobs):
                log(f"  {done}/{len(jobs)} Aufrufe erledigt")

    # Fehlgeschlagene Batches einzeln nachholen (kleinere Häppchen)
    for i, cs, ce in failures:
        for k in range(i, min(i + batch, n), 5):
            j = min(k + 5, n)
            job, res = work((k, cs, ce))
            results.append((k, cs, ce, res))

    if not results:
        raise SystemExit("Keine Daten empfangen")

    time_axis = results[0][3]["_time"]
    arrays = {k: np.full((n, time_axis.size), np.nan, dtype=np.float32) for k in VARIABLES}
    for i, cs, ce, res in results:
        j = i + res["tmax"].shape[0]
        for k in VARIABLES:
            arrays[k][i:j, :] = res[k]
        if not np.array_equal(res["_time"], time_axis):
            raise RuntimeError("Zeitachsen der Batches stimmen nicht überein")

    # Lücken füllen: linear über die Zeit, sonst mit dem Punktmittel
    for k, arr in arrays.items():
        if np.isnan(arr).any():
            log(f"  {k}: {int(np.isnan(arr).sum())} Lücken werden gefüllt")
            for row in range(arr.shape[0]):
                col = arr[row]
                bad = np.isnan(col)
                if bad.all():
                    col[:] = 0.0
                elif bad.any():
                    idx = np.arange(col.size)
                    col[bad] = np.interp(idx[bad], idx[~bad], col[~bad])
    return arrays, time_axis


def build_land_mask(cfg: dict) -> np.ndarray:
    from global_land_mask import globe

    step = cfg["mask_step"]
    lats = np.arange(cfg["lat_min"], cfg["lat_max"] + 1e-9, step)
    lons = np.arange(cfg["lon_min"], cfg["lon_max"] + 1e-9, step)
    lon2, lat2 = np.meshgrid(lons, lats)
    mask = globe.is_land(lat2, lon2)
    if mask.dtype != bool:
        mask = mask > 0.5
    log(f"Landmaske {mask.shape} ({mask.mean()*100:.1f}% Land)")
    return mask


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="kleines Testraster, wenige Punkte")
    ap.add_argument("--force-test", action="store_true", help="Testmodus auch bei test_mode=false")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Punkte")
    ap.add_argument("--step", type=float, default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = dict(DEFAULT_CFG)
    cfg_file = ROOT / "scripts" / "grid_config.json"
    if cfg_file.exists():
        cfg.update(json.loads(cfg_file.read_text()))
    if args.step:
        cfg["step"] = args.step
    if args.start:
        cfg["start_date"] = args.start
    if args.end:
        cfg["end_date"] = args.end
    if args.test:
        cfg.update({"step": 3.0, "batch_points": 8, "chunk_years": 5, "threads": 4})

    lat, lon = build_grid(cfg)
    t0 = time.time()
    arrays, time_axis = fetch_all(lat, lon, cfg, limit=args.limit)
    n_days = time_axis.size
    n_points = lat.size
    log(f"{n_points} Punkte x {n_days} Tage in {time.time()-t0:.0f}s")

    # Quantisieren
    store: dict[str, np.ndarray] = {}
    for k, arr in arrays.items():
        q = np.rint(arr * SCALES[k])
        lo = -127 if k in ("tmax", "tmin", "tdew") else 0
        q = np.clip(q, lo, 127).astype(np.int8)
        store[k] = q
        log(f"  {k}: {np.nanmin(arr):.1f} .. {np.nanmax(arr):.1f} {UNITS[k]}")

    mask = build_land_mask(cfg)
    out = Path(args.out) if args.out else ROOT / "app" / "data" / "climate_grid.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        lat=lat.astype(np.float32),
        lon=lon.astype(np.float32),
        **store,
        land_mask_packed=np.packbits(mask.reshape(-1)),
        land_mask_shape=np.array(mask.shape, dtype=np.int32),
    )
    size_mb = out.stat().st_size / 1e6
    log(f"geschrieben: {out} ({size_mb:.1f} MB)")

    meta = {
        "source": "ERA5 Reanalyse über Open-Meteo Historical Weather API (archive-api.open-meteo.com)",
        "attribution": "Daten: ERA5 (Copernicus/ECMWF) via Open-Meteo.com – Lizenz CC-BY-4.0 (Open-Meteo: CC-BY-NC-4.0)",
        "model": cfg["model"],
        "start_date": str(time_axis[0]),
        "end_date": str(time_axis[-1]),
        "n_days": int(n_days),
        "n_years": round(n_days / 365.25, 2),
        "n_points": int(n_points),
        "grid_step_deg": cfg["step"],
        "bbox": {"lat_min": cfg["lat_min"], "lat_max": cfg["lat_max"],
                 "lon_min": cfg["lon_min"], "lon_max": cfg["lon_max"]},
        "variables": {k: {"scale": SCALES[k], "unit": UNITS[k], "api": VARIABLES[k]} for k in VARIABLES},
        "land_mask": {"step": cfg["mask_step"], "shape": list(mask.shape)},
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    (out.parent / "climate_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log("Metadaten geschrieben: app/data/climate_meta.json")


if __name__ == "__main__":
    sys.exit(main())
