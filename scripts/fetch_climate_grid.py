#!/usr/bin/env python3
"""Holt tägliche ERA5-Klimadaten für ein Europa-Raster über die Open-Meteo-API
und speichert sie kompakt als ``app/data/climate_grid.npz`` (+ Metadaten).

Warum so umständlich?
---------------------
Die Open-Meteo-Archive-API ist kostenlos, aber mengenbegrenzt. Gemessen auf einem
GitHub-Runner (siehe ``.github/workflows/api-timing-probe.yml``): eine Anfrage mit
20 Punkten × 5 Jahren (36.500 Punkt-Tage, ~1,5 MB) läuft in ~1,5 s durch – aber schon
die nächste zu große Anfrage bekommt **HTTP 429**. Das Kontingent zählt also die
*Punkt-Tage* pro Zeiteinheit, nicht die Anzahl der Aufrufe.

Deshalb:
  * feste, einstellbare Rate (``point_days_per_min``) mit Token-Bucket-Scheduling,
  * 429-Antworten führen zu Abkühlen + Absenken der Rate (adaptiv),
  * jeder Block wird als Kontrollpunkt (``data/checkpoints/``) gesichert, sodass ein
    abgebrochener Lauf jederzeit fortgesetzt werden kann (``--resume`` ist Standard),
  * Kontrollpunkte werden **über ihre Datumsachse** zusammengesetzt – die Blockgröße
    darf sich zwischen Läufen ändern, ohne Daten zu beschädigen,
  * fehlende Tage (``null`` in der API, abgebrochene Läufe) werden mit -128 markiert
    und am Ende aus Nachbarpunkten bzw. der Monatsklimatologie gefüllt; die
    Vollständigkeit steht als ``coverage`` in den Metadaten und wird von
    ``scripts/check_grid.py`` als Plausibilitätstor geprüft,
  * ``--max-minutes`` beendet den Lauf geordnet, bevor der Job-Timeout greift.

Ausgabe:
  app/data/climate_grid.npz    tmax, tmin, tdew, sun, prec (int8), lat, lon,
                               Landmaske (gepackte Bits), Raster-Geometrie
  app/data/climate_meta.json   Zeitraum, Skalen, Quelle, Raster-Info, coverage

Aufruf:
  python scripts/fetch_climate_grid.py                # volle Konfiguration
  python scripts/fetch_climate_grid.py --test         # kleines Testraster
  python scripts/fetch_climate_grid.py --max-minutes 280
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
API = "https://archive-api.open-meteo.com/v1/archive"
CHECKPOINT_DIR = ROOT / "data" / "checkpoints"
OUT_DIR = ROOT / "app" / "data"   # Ziel der fertigen Dateien (Tests ändern das)
EPOCH = np.datetime64("1970-01-01")

# Daily-Variablen der Open-Meteo-API -> Name im Datensatz
VARIABLES = {
    "tmax": "temperature_2m_max",
    "tmin": "temperature_2m_min",
    "tdew": "dew_point_2m_mean",
    "sun": "sunshine_duration",       # Sekunden pro Tag
    "prec": "precipitation_sum",      # mm pro Tag
}
SCALES = {  # Quantisierung auf int8: q = clip(round(v * scale), lo, 127)
    "tmax": 2.0, "tmin": 2.0, "tdew": 2.0,   # 0,5 °C
    "sun": 4.0 / 3600.0,                     # 0,25 h
    "prec": 2.0,                             # 0,5 mm
}
UNITS = {"tmax": "°C", "tmin": "°C", "tdew": "°C", "sun": "h", "prec": "mm"}
SENTINEL = -128

DEFAULT_CFG = {
    "lat_min": 34.0, "lat_max": 72.0,
    "lon_min": -25.0, "lon_max": 45.0,
    "step": 0.75,
    "start_date": "2016-01-01",
    "end_date": "2025-12-31",
    "model": "era5",
    "mask_step": 0.05,
    "batch_points": 10,          # Punkte je API-Aufruf (klein = weniger Risiko bei 429)
    "chunk_years": 5,            # Jahre je API-Aufruf
    "point_days_per_min": 64000, # Anfragen-Takt (geschätzt ≈ 4.500 „Gewicht“/min)
    "threads": 3,
    "max_minutes": 285,
    "test_mode": False,
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ Netzwerk
class Pace:
    """Verteilt die Anfragen so, dass die Punkt-Tage pro Minute eingehalten werden."""

    def __init__(self, point_days_per_min: float):
        self.lock = threading.Lock()
        self.per_min = float(point_days_per_min)
        self.next_at = time.monotonic()
        self.requests = 0
        self.cool_downs = 0

    def acquire(self, cost: float) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + cost / self.per_min * 60.0
            self.requests += 1
        wait = start - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def penalize(self, reason: str) -> None:
        with self.lock:
            self.per_min = max(600.0, self.per_min * 0.6)
            self.next_at = max(self.next_at, time.monotonic() + 45.0)
            self.cool_downs += 1
            log(f"  ! {reason}: Rate auf {self.per_min:.0f} Punkt-Tage/min gesenkt, 45 s Pause")


def http_json(url: str, pace: Pace, cost: float, tries: int = 5, timeout: int = 600):
    """Anfrage mit Taktung; bei 429 wird gewartet und die Rate gesenkt."""
    last: Exception | None = None
    for attempt in range(tries):
        pace.acquire(cost)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "meinperfektesklima-databuild"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 503, 502, 500):
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait = float(retry_after) if (retry_after or "").strip().isdigit() else 0.0
                pace.penalize(f"HTTP {exc.code}")
                if wait > 0:
                    log(f"    warte laut Retry-After {wait:.0f} s")
                    time.sleep(wait)
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(60.0, 3.0 * 2 ** attempt)
            log(f"  ! Fehler ({type(exc).__name__}: {exc}); neuer Versuch in {wait:.0f} s")
            time.sleep(wait)
    raise RuntimeError(f"Anfrage endgültig fehlgeschlagen: {last}")


# ------------------------------------------------------------------- Raster
def build_grid(cfg: dict) -> tuple[np.ndarray, np.ndarray]:
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


def periods(cfg: dict) -> list[tuple[str, str]]:
    start, end = cfg["start_date"], cfg["end_date"]
    y0, y1 = int(start[:4]), int(end[:4])
    out = []
    for y in range(y0, y1 + 1, cfg["chunk_years"]):
        cs = max(start, f"{y}-01-01")
        ce = min(end, f"{min(y + cfg['chunk_years'] - 1, y1)}-12-31")
        out.append((cs, ce))
    return out


def config_slug(cfg: dict) -> str:
    keys = ("lat_min", "lat_max", "lon_min", "lon_max", "step", "start_date", "end_date", "model")
    blob = json.dumps({k: cfg[k] for k in keys}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- Abruf
def as_float32(vals: list) -> tuple[np.ndarray, int]:
    """API-Werte nach float32; ``null`` wird zu NaN (und später zur Lücke)."""
    try:
        return np.asarray(vals, dtype=np.float32), 0
    except (TypeError, ValueError):
        out = np.full(len(vals), np.nan, dtype=np.float32)
        good = [i for i, v in enumerate(vals) if v is not None]
        out[good] = [vals[i] for i in good]
        return out, len(vals) - len(good)


def fetch_batch(lat: np.ndarray, lon: np.ndarray, start: str, end: str, model: str,
                pace: Pace) -> dict[str, np.ndarray]:
    query = {
        "latitude": ",".join(f"{v:.4f}" for v in lat),
        "longitude": ",".join(f"{v:.4f}" for v in lon),
        "start_date": start, "end_date": end,
        "daily": ",".join(VARIABLES.values()),
        "timezone": "UTC", "models": model,
    }
    url = f"{API}?{urllib.parse.urlencode(query)}"
    cost = float(lat.size * (np.datetime64(end) - np.datetime64(start)).astype(int) + lat.size)
    data = http_json(url, pace, cost)
    if isinstance(data, dict):
        data = [data]
    if len(data) != lat.size:
        raise RuntimeError(f"{len(data)} Orte in der Antwort, {lat.size} erwartet")
    out: dict[str, list[np.ndarray]] = {k: [] for k in VARIABLES}
    times = None
    n_null = 0
    for entry in data:
        daily = entry.get("daily") or {}
        t = daily.get("time") or []
        if times is None:
            times = t
        elif t != times:
            raise RuntimeError("Zeitachse weicht zwischen den Orten ab")
        for key, api_name in VARIABLES.items():
            vals = daily.get(api_name)
            if vals is None:
                raise RuntimeError(f"Variable {api_name} fehlt in der Antwort")
            arr, missing = as_float32(vals)
            n_null += missing
            if arr.size != len(times):
                raise RuntimeError(f"{api_name}: {arr.size} Werte, {len(times)} erwartet")
            out[key].append(arr)
    if n_null:
        log(f"  ! {n_null} null-Werte in dieser Antwort -> werden als Lücken gefüllt")
    res = {k: np.vstack(v) for k, v in out.items()}
    res["_time"] = np.array(times, dtype="datetime64[D]")
    return res


def quantize(name: str, arr: np.ndarray) -> np.ndarray:
    """float32 -> int8; NaN wird zum Sentinel -128 (Lücke) statt zu Müll."""
    lo = -127 if name in ("tmax", "tmin", "tdew") else 0
    with np.errstate(invalid="ignore"):
        scaled = np.rint(arr * SCALES[name])
    good = np.isfinite(scaled)
    out = np.full(arr.shape, SENTINEL, dtype=np.int8)
    out[good] = np.clip(scaled[good], lo, 127).astype(np.int8)
    return out


# ------------------------------------------------------------- Lücken füllen
def lattice_index(cfg: dict, lat: np.ndarray, lon: np.ndarray) -> dict:
    la = np.round((lat - cfg["lat_min"]) / cfg["step"]).astype(int)
    lo = np.round((lon - cfg["lon_min"]) / cfg["step"]).astype(int)
    return {(int(a), int(b)): p for p, (a, b) in enumerate(zip(la, lo))}, la, lo


def fill_gaps(cfg: dict, arrays: dict, lat: np.ndarray, lon: np.ndarray,
              month_of_day: np.ndarray) -> dict:
    """Sentinel-Werte aus Nachbarpunkten (Rastergeometrie, ohne scipy) ergänzen.

    Fehlende Tage würden sonst stillschweigend als „kein Wohlfühl-Tag“ gezählt –
    das verstellt die Karte (gerade in Südeuropa, wo viele Rasterpunkte liegen).
    """
    index, la, lo = lattice_index(cfg, lat, lon)
    miss = np.zeros_like(arrays["tmax"], dtype=bool)
    for name in VARIABLES:
        miss |= arrays[name] == SENTINEL
    n_points = lat.size
    stats = {"missing_points": int((miss[:, 0]).sum()), "missing_point_days": int(miss.sum()),
             "filled_from_neighbour": 0, "filled_from_climatology": 0}
    if stats["missing_point_days"] == 0:
        return stats

    def neighbours(p: int, rings: int = 4) -> list[int]:
        a, b = la[p], lo[p]
        out: list[int] = []
        for r in range(1, rings + 1):
            for da in range(-r, r + 1):
                for db in range(-r, r + 1):
                    if max(abs(da), abs(db)) != r:
                        continue
                    q = index.get((a + da, b + db))
                    if q is not None and not miss[q, 0]:
                        out.append(q)
            if len(out) >= 4:
                break
        return out

    for p in range(n_points):
        if not miss[p, 0]:
            continue
        nb = neighbours(p)
        for name in VARIABLES:
            cols = np.flatnonzero(arrays[name][p] == SENTINEL)
            if cols.size == 0:
                continue
            if nb:
                src = np.stack([arrays[name][q][cols] for q in nb]).astype(np.float32)
                valid = src != SENTINEL
                acc = np.where(valid, src, 0.0).sum(axis=0)
                cnt = valid.sum(axis=0)
                fill = np.full(cols.size, SENTINEL, dtype=np.int8)
                ok = cnt > 0
                fill[ok] = np.rint(acc[ok] / cnt[ok]).astype(np.int8)
                arrays[name][p, cols] = fill
                still = cols[fill == SENTINEL]
                stats["filled_from_neighbour"] += int(cols.size - still.size)
                cols = still
            if cols.size:
                # Rest: Monatsmittel des Punkts (bzw. Monatsmittel aller Punkte)
                months = month_of_day[cols]
                for m in range(12):
                    sel = cols[months == m]
                    if sel.size == 0:
                        continue
                    own = arrays[name][p, month_of_day == m]
                    own = own[own != SENTINEL]
                    if own.size:
                        val = int(np.rint(own.astype(np.float32).mean()))
                    else:
                        pool = arrays[name][:, month_of_day == m]
                        pool = pool[pool != SENTINEL]
                        val = int(np.rint(pool.astype(np.float32).mean())) if pool.size else 0
                    arrays[name][p, sel] = val
                stats["filled_from_climatology"] += int(cols.size)
    stats["remaining"] = int((arrays["tmax"] == SENTINEL).sum())
    return stats


# ------------------------------------------------------------------ Lauf
def run(cfg: dict, limit: int | None = None) -> int:
    """Führt den Abruf aus; Rückgabewert = Zahl der in diesem Lauf geholten Blöcke."""
    lat, lon = build_grid(cfg)
    n = lat.size
    if limit:
        n = min(n, limit)
        lat, lon = lat[:n], lon[:n]
        log(f"Begrenzung auf die ersten {n} Punkte")

    per = periods(cfg)
    batch = int(cfg["batch_points"])
    slug = config_slug(cfg)
    ckpt = CHECKPOINT_DIR / slug
    ckpt.mkdir(parents=True, exist_ok=True)

    jobs = []
    for i in range(0, n, batch):
        for pi, (cs, ce) in enumerate(per):
            jobs.append((i, min(i + batch, n), pi, cs, ce))

    def path_of(i: int, pi: int, j: int) -> Path:
        return ckpt / f"b{i:05d}-{j:05d}-p{pi}.npz"

    done, todo = 0, []
    for job in jobs:
        i, j, pi, cs, ce = job
        if path_of(i, pi, j).exists():
            done += 1
        else:
            todo.append(job)
    log(f"Kontrollpunkte: {done} von {len(jobs)} Blöcken vorhanden, {len(todo)} offen "
        f"(Verzeichnis {ckpt})")

    pace = Pace(cfg["point_days_per_min"])
    threads = max(1, int(cfg["threads"]))
    deadline = time.monotonic() + float(cfg["max_minutes"]) * 60.0
    started = time.time()
    fetched = 0

    def work(job):
        i, j, pi, cs, ce = job
        res = fetch_batch(lat[i:j], lon[i:j], cs, ce, cfg["model"], pace)
        store = {k: quantize(k, res[k]) for k in VARIABLES}
        np.savez_compressed(path_of(i, pi, j), time=res["_time"].astype("int64"), **store)
        return j - i

    if todo:
        from concurrent.futures import FIRST_COMPLETED, wait

        pool = ThreadPoolExecutor(max_workers=threads)
        queue = list(todo)
        running: dict = {}
        try:
            while queue or running:
                while queue and len(running) < threads and time.monotonic() < deadline:
                    job = queue.pop(0)
                    running[pool.submit(work, job)] = job
                if not running:
                    if queue:
                        log(f"Zeitbudget von {cfg['max_minutes']:.0f} min erreicht – "
                            f"{len(queue)} Blöcke bleiben für den nächsten Lauf")
                    break
                done_futs, _ = wait(list(running), timeout=30, return_when=FIRST_COMPLETED)
                if not done_futs and time.monotonic() > deadline:
                    log("Zeitbudget erreicht – laufende Anfragen werden noch abgewartet")
                    done_futs, _ = wait(list(running), timeout=300, return_when=FIRST_COMPLETED)
                for fut in done_futs:
                    job = running.pop(fut)
                    try:
                        pts = fut.result()
                        fetched += 1
                        if fetched % 5 == 0 or fetched == len(todo):
                            rate = (time.time() - started) / max(fetched, 1)
                            rest = (len(todo) - fetched) * rate / 60.0
                            log(f"  {fetched}/{len(todo)} Blöcke ({pts} Punkte je Block, "
                                f"{rate:.1f} s/Block, noch ca. {rest:.0f} min, "
                                f"Rate {pace.per_min:.0f} Punkt-Tage/min, {pace.cool_downs} Drosseln)")
                    except Exception as exc:  # noqa: BLE001
                        log(f"  ! Block {job[0]}..{job[1]} {job[3]}..{job[4]} fehlgeschlagen: {exc}")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    # -------- Zusammensetzen (über die Datumsachse, robust gegen gemischte Blöcke)
    files = sorted(ckpt.glob("b*-p*.npz"))
    if not files:
        log("Keine Kontrollpunkte vorhanden – Abbruch")
        return fetched

    n_days = int((np.datetime64(cfg["end_date"]) - np.datetime64(cfg["start_date"])).astype(int)) + 1
    base = int((np.datetime64(cfg["start_date"]) - EPOCH).astype(int))
    arrays = {k: np.full((n, n_days), SENTINEL, dtype=np.int8) for k in VARIABLES}
    covered = 0
    skipped = 0
    for f in files:
        m = re.match(r"b(\d{5})-(\d{5})-p(\d+)\.npz", f.name)
        if not m:
            continue
        i, j = int(m.group(1)), int(m.group(2))
        if j > n:
            skipped += 1
            continue
        with np.load(f) as z:
            t = z["time"].astype("datetime64[D]")
            cols = (t - EPOCH).astype(int) - base
            okc = (cols >= 0) & (cols < n_days)
            if not okc.all():
                log(f"  ! {f.name}: {int((~okc).sum())} Tage außerhalb des Zielzeitraums ignoriert")
            cols = cols[okc]
            for k in VARIABLES:
                vals = z[k][:, okc] if not okc.all() else z[k]
                arrays[k][i:j, cols] = vals
        covered += 1
    expect = len({(i, j, pi) for i, j, pi, _, _ in jobs})
    log(f"Blöcke zusammengesetzt: {covered}/{expect} (übersprungen: {skipped})")

    days = np.datetime64(cfg["start_date"]) + np.arange(n_days)
    month_of_day = days.astype("datetime64[M]").astype(int) % 12

    gap_stats = fill_gaps(cfg, arrays, lat, lon, month_of_day)
    coverage = 1.0 - gap_stats["missing_point_days"] / max(1, n * n_days)
    if gap_stats["missing_point_days"]:
        log(f"  Lücken gefüllt: {gap_stats}")
    log(f"  Abdeckung: {coverage*100:.2f} % der Punkt-Tage")

    mask = build_land_mask(cfg)
    out = OUT_DIR / "climate_grid.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out, lat=lat.astype(np.float32), lon=lon.astype(np.float32), **arrays,
        land_mask_packed=np.packbits(mask.reshape(-1)),
        land_mask_shape=np.array(mask.shape, dtype=np.int32),
    )
    size_mb = out.stat().st_size / 1e6
    log(f"geschrieben: {out} ({size_mb:.1f} MB)")

    meta = {
        "source": "ERA5 Reanalyse über Open-Meteo Historical Weather API (archive-api.open-meteo.com)",
        "attribution": "Daten: ERA5 (Copernicus/ECMWF) via Open-Meteo.com – Zeitschritte Open-Meteo (CC-BY-4.0)",
        "model": cfg["model"],
        "start_date": str(days[0]), "end_date": str(days[-1]),
        "n_days": int(n_days), "n_years": round(n_days / 365.25, 2), "n_points": int(n),
        "grid_step_deg": cfg["step"],
        "bbox": {"lat_min": cfg["lat_min"], "lat_max": cfg["lat_max"],
                 "lon_min": cfg["lon_min"], "lon_max": cfg["lon_max"]},
        "variables": {k: {"scale": SCALES[k], "unit": UNITS[k], "api": VARIABLES[k]} for k in VARIABLES},
        "land_mask": {"step": cfg["mask_step"], "shape": list(mask.shape)},
        "coverage": {"fraction": round(float(coverage), 6),
                     "missing_point_days": int(gap_stats["missing_point_days"]),
                     "filled_from_neighbour": int(gap_stats.get("filled_from_neighbour", 0)),
                     "filled_from_climatology": int(gap_stats.get("filled_from_climatology", 0)),
                     "blocks": f"{covered}/{expect}"},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out.parent / "climate_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log(f"Metadaten geschrieben: {out.parent / 'climate_meta.json'}")

    complete = (gap_stats["missing_point_days"] == 0 and covered == expect)
    if complete:
        # Vollständig: Kontrollpunkte können weg (der Workflow committet die Löschung)
        for f in files:
            f.unlink()
        try:
            ckpt.rmdir()
        except OSError:
            pass
        log("Vollständig – Kontrollpunkte aufgeräumt")
    else:
        log(f"Unvollständig ({covered}/{expect}) – Kontrollpunkte bleiben für den nächsten Lauf erhalten")
    return fetched


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="kleines Raster, kurzer Zeitraum")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Rasterpunkte")
    ap.add_argument("--step", type=float, default=None, help="Rasterweite in Grad")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--rate", type=float, default=None, help="Punkt-Tage pro Minute")
    ap.add_argument("--reset", action="store_true", help="Kontrollpunkte verwerfen und neu starten")
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
    if args.max_minutes:
        cfg["max_minutes"] = args.max_minutes
    if args.rate:
        cfg["point_days_per_min"] = args.rate
    if args.test:
        cfg.update({"step": 3.0, "start_date": "2023-01-01", "end_date": "2024-12-31",
                    "batch_points": 10, "chunk_years": 2, "threads": 2,
                    "point_days_per_min": 60000, "max_minutes": 20, "test_mode": True})

    if args.reset:
        import shutil
        shutil.rmtree(CHECKPOINT_DIR / config_slug(cfg), ignore_errors=True)

    log("Konfiguration: " + json.dumps({k: cfg[k] for k in
        ("step", "start_date", "end_date", "batch_points", "chunk_years",
         "point_days_per_min", "max_minutes", "test_mode")}, ensure_ascii=False))
    t0 = time.time()
    fetched = run(cfg, limit=args.limit)
    log(f"fertig in {time.time()-t0:.0f}s, {fetched} Blöcke in diesem Lauf")
    if fetched == 0 and not (ROOT / "app" / "data" / "climate_grid.npz").exists():
        log("Es wurden keine Daten geladen – Raster fehlt weiterhin")


if __name__ == "__main__":
    sys.exit(main())
