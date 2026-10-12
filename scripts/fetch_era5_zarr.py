#!/usr/bin/env python3
"""Baut das Klima-Raster direkt aus offenen ERA5-Daten (Zarr auf Google Cloud).

Warum nicht die Open-Meteo-API?
-------------------------------
Die frei nutzbare Archive-API zählt **gewichtete Aufrufe je IP**: 600/min,
5.000/h und 10.000/Tag, wobei ein Aufruf mit mehr als zwei Wochen Zeitraum je
Ort mehrfach zählt. Der Europa-Rasterabruf (2.250 Landpunkte × 3.653 Tage)
entspricht damit rund 290.000 „calls" – das freie Kontingent reicht für etwa
fünf der 188 Blöcke pro Tag, und weil GitHub-Runner-IPs geteilt sind, ist es
oft schon vor dem ersten Aufruf erschöpft (HTTP 429). Genau daran ist der
erste Datenlauf gescheitert (siehe build-log.txt).

Dieses Skript liest dieselben ERA5-Werte stattdessen aus der offenen,
analysis-ready Kopie von Google Research:

    gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3

anonym, ohne Schlüssel, ohne Tageskontingent und mit allen benötigten
Variablen. Gezogen wird stündlich über den Europa-Ausschnitt, daraus entstehen
die Tageswerte des Datensatzes:

    tmax  max(2m_temperature)                       K -> °C
    tmin  min(2m_temperature)                       K -> °C
    tdew  mean(2m_dewpoint_temperature)             K -> °C
    prec  sum(total_precipitation)                  m -> mm
    sun   Stunden mit Direkteinstrahlung >= 120 W/m²
          (total_sky_direct_solar_radiation_at_surface / 3600)
          – das ist exakt die ECMWF-Definition der Sonnenscheindauer.

ERA5-Akkumulationen (Niederschlag, Strahlung) gelten jeweils für die
*vorangehende* Stunde, deshalb werden sie um eine Stunde versetzt eingelesen
(Stunden 01–24 eines Tages).

Der Lauf arbeitet mit Kontrollpunkten **je Jahr** (``data/checkpoints/…``),
lässt sich also nach einem Zeitbudget-Abbruch fortsetzen, und meldet seinen
Fortschritt nach ``app/data/build_progress.json`` (daraus zeichnet die
Startseite den Balken, solange die Website noch keine Daten zeigen darf).

Aufruf:
  python scripts/fetch_era5_zarr.py                 # volle Konfiguration
  python scripts/fetch_era5_zarr.py --test          # Mini-Ausschnitt (Test)
  python scripts/fetch_era5_zarr.py --store /pfad   # lokaler Zarr-Speicher
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_progress import Progress, now_iso                            # noqa: E402
from fetch_climate_grid import (CHECKPOINT_DIR, DEFAULT_CFG as BASE_CFG, OUT_DIR,  # noqa: E402
                                SCALES, SENTINEL, UNITS, build_grid, build_land_mask,
                                config_slug, fill_gaps, log, quantize)

VARS = ("tmax", "tmin", "tdew", "sun", "prec")

ZARR_CFG = {
    "source": "era5_zarr",
    # Öffentliche, anonym lesbare ERA5-Kopie (Google ARCO / WeatherBench2).
    "zarr_store": "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3",
    "zarr_variables": {
        "t2m": "2m_temperature",
        "d2m": "2m_dewpoint_temperature",
        "tp": "total_precipitation",
        "fdir": "total_sky_direct_solar_radiation_at_surface",
    },
    # ECMWF-Definition der Sonnenscheindauer: Stunde zählt, wenn die direkte
    # Einstrahlung auf die Fläche im Stundenmittel über 120 W/m² liegt.
    "sun_threshold_w_m2": 120.0,
    "days_per_read": 5,           # Tage je Lesevorgang (Speicherbedarf)
    "max_minutes": 285,
}

SOURCE_TEXT = ("ERA5-Reanalyse (Copernicus Climate Change Service / ECMWF), offene "
               "analysis-ready Kopie von Google Research")
ATTRIBUTION = ("Contains modified Copernicus Climate Change Service information (2026). "
               "ERA5 © ECMWF unter der Copernicus-Lizenz, bereitgestellt als offene "
               "ARCO-Zarr-Kopie über Google Cloud (gs://gcp-public-data-arco-era5).")

# Welche Tagesgröße aus welcher ERA5-Variablen entsteht – und wie.
DERIVATION = {
    "tmax": ("t2m", "max"),
    "tmin": ("t2m", "min"),
    "tdew": ("d2m", "mean"),
    "prec": ("tp", "sum_mm"),
    "sun": ("fdir", "sunshine_hours"),
}
# Akkumulierte Größen gelten für die vorangegangene Stunde -> eine Stunde später lesen
ACCUMULATED = {"tp", "fdir"}
KELVIN = 273.15


# --------------------------------------------------------------- Datenspeicher
def open_store(url: str):
    """Zarr-Speicher öffnen: ``gs://…`` anonym, alles andere als lokaler Pfad."""
    import xarray as xr

    if url.startswith("gs://"):
        import gcsfs
        import zarr
        path = url[len("gs://"):]
        fs = gcsfs.GCSFileSystem(token="anon")
        errors = []
        try:
            try:
                store = zarr.storage.FsspecStore(fs=fs, path=path, read_only=True)
            except TypeError:                       # ältere zarr-Versionen
                store = zarr.storage.FsspecStore(fs, path)
            return xr.open_zarr(store, chunks=None, consolidated=None)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"FsspecStore: {type(exc).__name__}: {exc}")
        try:
            return xr.open_zarr(url, storage_options={"token": "anon"},
                                chunks=None, consolidated=None)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"open_zarr: {type(exc).__name__}: {exc}")
        raise RuntimeError("Zarr-Speicher nicht lesbar – " + " | ".join(errors))
    return xr.open_zarr(url, chunks=None, consolidated=None)


def coord_of(ds, candidates: tuple[str, ...], label: str) -> str:
    for name in candidates:
        if name in ds.coords or name in ds.dims:
            return name
    raise RuntimeError(f"{label}-Achse nicht gefunden (vorhanden: {sorted(ds.coords)})")


# --------------------------------------------------------------- Gitterzuordnung
def axis_index(values: np.ndarray, targets: np.ndarray, tol: float = 1e-4) -> np.ndarray:
    """Index jedes Zielpunkts in der (feineren) Speicherachse, mit Prüfung."""
    values = np.asarray(values, dtype=np.float64)
    idx = np.abs(values[:, None] - np.asarray(targets, dtype=np.float64)[None, :]).argmin(axis=0)
    dev = float(np.abs(values[idx] - targets).max())
    if dev > tol:
        raise RuntimeError(f"Zielgitter liegt nicht auf dem Speichergitter "
                           f"(größte Abweichung {dev:.4f}°)")
    return idx


def segments(idx: np.ndarray) -> list[list[int]]:
    """Zerlegt eine Indexfolge in gleichsinnig monotone Abschnitte.

    Zwei Gründe für einen Bruch: der Längengrad-Umbruch des Speichers (…359,75° →
    0°) und eine fallend gespeicherte Achse (manche Datensätze zählen 90° → −90°).
    Jeder Abschnitt wird als zusammenhängendes Fenster gelesen; zusammengefügt
    ergeben sie wieder genau die Zielpunkte, in Zielreihenfolge.
    """
    werte = [int(v) for v in np.asarray(idx).ravel().tolist()]
    if not werte:
        return []
    out: list[list[int]] = []
    current = [werte[0]]
    richtung = 0
    for value in werte[1:]:
        schritt = value - current[-1]
        if schritt == 0 or (richtung and (schritt > 0) != (richtung > 0)):
            out.append(current)
            current = [value]
            richtung = 0
        else:
            current.append(value)
            richtung = schritt
    out.append(current)
    return out


def positions_in(segs: list[list[int]]) -> np.ndarray:
    """Positionen der Zielpunkte innerhalb des zusammengesetzten Blocks.

    Berücksichtigt die Laufrichtung des Abschnitts: Bei fallend gespeicherter
    Achse liegt der zweite Zielpunkt *hinter* dem ersten im Block, obwohl sein
    Achsindex kleiner ist.
    """
    pos: list[int] = []
    offset = 0
    for seg in segs:
        # Der Block beginnt beim *kleinsten* Achsindex des Fensters – bei fallend
        # gespeicherter Achse ist das der Abschnitts-Ende, bei aufsteigender der
        # Anfang. So stimmt die Zuordnung in beide Richtungen.
        base = min(seg[0], seg[-1])
        pos.extend(offset + (v - base) for v in seg)
        offset += abs(seg[-1] - seg[0]) + 1
    return np.array(pos, dtype=int)


def axis_slice(seg: list[int], descending: bool, values: np.ndarray) -> slice:
    """Positionsabschnitt als **Beschriftungs**-Slice (xarray selektiert per Wert).

    xarray verlangt bei fallend gespeicherter Achse ``slice(groß, klein)``, bei
    aufsteigender ``slice(klein, groß)`` – sonst liefert die Auswahl still
    einen leeren Block.
    """
    a, b = float(values[seg[0]]), float(values[seg[-1]])
    return slice(max(a, b), min(a, b)) if descending else slice(min(a, b), max(a, b))


class Geometry:
    """Zuordnung Zielraster (0,75°) -> Speicherachse (0,25°).

    Die Chunks des Speichers sind *globale* Felder je Stunde und Variable: Ein
    räumlicher Ausschnitt spart also keine Übertragung, nur Speicher. Deshalb
    wird der Längengradbereich komplett gelesen (einmal!) und erst im Speicher
    auf die Zielpunkte reduziert – zwei getrennte Lesevorgänge (wegen des
    Bereichs westlich von Null) würden dieselben Chunks doppelt laden.
    """

    def __init__(self, ds, cfg: dict, lats: np.ndarray, lons: np.ndarray):
        self.time = coord_of(ds, ("valid_time", "time"), "Zeit")
        self.lat = coord_of(ds, ("latitude", "lat"), "Breiten")
        self.lon = coord_of(ds, ("longitude", "lon"), "Längen")
        lat_values = np.asarray(ds[self.lat].values, dtype=np.float64)
        lon_values = np.asarray(ds[self.lon].values, dtype=np.float64)
        self.lat_desc = bool(lat_values[0] > lat_values[-1])
        self.lon_desc = bool(lon_values[0] > lon_values[-1])
        self.lat_values = lat_values

        # Speicher zählt Längengrade oft 0..360 – Zielpunkte liegen bei -25..45
        signed = np.where(lon_values > 180.0, lon_values - 360.0, lon_values)
        lat_idx = axis_index(lat_values, lats)
        lon_idx = axis_index(signed, lons)
        self.lat_segs = segments(lat_idx)
        self.lat_pos = positions_in(self.lat_segs)
        self.lon_idx = lon_idx                      # absolute Indizes, in Zielreihenfolge
        self.n_lat = sum(abs(s[-1] - s[0]) + 1 for s in self.lat_segs)
        log(f"Speicher: {self.time}, {self.lat} ({lat_values.size}), {self.lon} ({lon_values.size})")
        log(f"Ausschnitt: {len(self.lat_segs)} Breitenabschnitt/‑abschnitte "
            f"({self.n_lat} Stützstellen) × {lon_values.size} Längengrade, "
            f"Ziel {lats.size}x{lons.size} Punkte")

    def read(self, da, start: str, end: str) -> np.ndarray:
        """Stundenblock (T, n_lat_ziel, n_lon_ziel) für einen Zeitraum."""
        parts = []
        for seg in self.lat_segs:
            sel = {self.time: slice(start, end),
                   self.lat: axis_slice(seg, self.lat_desc, self.lat_values)}
            parts.append(np.asarray(da.sel(**sel).values, dtype=np.float32))
        block = np.concatenate(parts, axis=1) if len(parts) > 1 else parts[0]
        return block[:, self.lat_pos][:, :, self.lon_idx]


# ---------------------------------------------------------------- Tageswerte
def to_daily(block: np.ndarray, n_days: int, kind: str, cfg: dict) -> np.ndarray:
    """Stundenblock (24*n_days, lat, lon) -> Tageswerte (n_days, lat, lon).

    Ehrliche Lücken statt halber Wahrheiten: Fehlt auch nur eine Stunde eines
    Tages (oder war der Block zu kurz), wird der Tag als Lücke (NaN) gewertet –
    ``fill_gaps`` ergänzt ihn später aus Nachbarpunkten und die Abdeckung landet
    im Qualitätsbericht. Ein aus 20 Stunden „gemitteltes" Tagesmaximum wäre
    sonst ein falscher Wert, den niemand mehr als solchen erkennt.
    """
    hours = 24 * n_days
    if block.shape[0] != hours:
        log(f"  ! {block.shape[0]} statt {hours} Stunden gelesen – "
            f"betroffene Tage werden als Lücken gewertet")
        fixed = np.full((hours,) + tuple(block.shape[1:]), np.nan, dtype=np.float32)
        n = min(hours, block.shape[0])
        fixed[:n] = block[:n]
        block = fixed
    shaped = block.reshape(n_days, 24, *block.shape[1:]).astype(np.float32)
    vollstaendig = ~np.isnan(shaped).any(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        if kind == "max":
            out = np.nanmax(shaped, axis=1) - KELVIN
        elif kind == "min":
            out = np.nanmin(shaped, axis=1) - KELVIN
        elif kind == "mean":
            out = np.nanmean(shaped, axis=1) - KELVIN
        elif kind == "sum_mm":
            out = np.nansum(np.clip(shaped, 0.0, None), axis=1) * 1000.0   # m -> mm
        elif kind == "sunshine_hours":
            watt = np.clip(shaped, 0.0, None) / 3600.0
            out = (watt >= float(cfg["sun_threshold_w_m2"])).sum(axis=1).astype(np.float32)
        else:
            raise ValueError(f"unbekannte Ableitung: {kind}")
    return np.where(vollstaendig, out, np.nan).astype(np.float32)


def day_range(start: str, end: str) -> np.ndarray:
    """Alle Tage von ``start`` bis ``end`` (einschließlich) als datetime64[D]."""
    first = np.datetime64(start, "D")
    n = int((np.datetime64(end, "D") - first).astype(int)) + 1
    return first + np.arange(n).astype("timedelta64[D]")


def harvest_period(ds, cfg: dict, geom: Geometry, days: np.ndarray, land: np.ndarray,
                   progress: Progress | None = None) -> dict[str, np.ndarray]:
    """Tageswerte für einen zusammenhängenden Zeitraum, auf Landpunkte reduziert."""
    variables = cfg["zarr_variables"]
    for key, name in variables.items():
        if name not in ds.variables:
            raise RuntimeError(f"Variable {name} ({key}) fehlt im Datenspeicher")

    # Zielvariable <- Quellvariable: jede Quelle wird genau einmal gelesen
    by_source: dict[str, list[tuple[str, str]]] = {}
    for target, (src, kind) in DERIVATION.items():
        by_source.setdefault(src, []).append((target, kind))

    per_read = max(1, int(cfg["days_per_read"]))
    n_days = days.size
    flat = land.reshape(-1)
    daily: dict[str, np.ndarray] = {v: np.full((n_days, int(flat.sum())), np.nan, dtype=np.float32)
                                    for v in VARS}
    produced = {v: False for v in VARS}

    for src in sorted(by_source):
        var = ds[variables[src]]
        for offset in range(0, n_days, per_read):
            chunk = days[offset: offset + per_read]
            first, last = str(chunk[0]), str(chunk[-1])
            if src in ACCUMULATED:
                # Akkumulation gilt für die vorangegangene Stunde: 01:00 … 24:00
                t0 = f"{first}T01:00"
                t1 = f"{str(np.datetime64(last) + np.timedelta64(1, 'D'))}T00:00"
            else:
                t0 = f"{first}T00:00"
                t1 = f"{last}T23:00"
            block = geom.read(var, t0, t1)
            for target, kind in by_source[src]:
                values = to_daily(block, chunk.size, kind, cfg)
                if target == "sun":
                    values = values * 3600.0        # Stunden -> Sekunden (Speicherformat)
                daily[target][offset: offset + chunk.size] = \
                    values.reshape(chunk.size, -1)[:, flat]
                produced[target] = True
            del block
            if progress is not None:
                progress.update(message=f"{variables[src]} {first} … {last}")

    missing = [v for v, ok in produced.items() if not ok]
    if missing:
        raise RuntimeError(f"keine Werte für {missing} erzeugt")

    return {name: quantize(name, np.ascontiguousarray(daily[name].T)) for name in VARS}


# ------------------------------------------------------------------ Kontrollpunkte
def checkpoint_dir(cfg: dict) -> Path:
    """Kontrollpunkt-Ordner; ``checkpoint_dir`` in der Konfiguration überschreibt
    den repo-weiten Standard (Tests arbeiten in einem temporären Ordner)."""
    base = Path(cfg["checkpoint_dir"]) if cfg.get("checkpoint_dir") else CHECKPOINT_DIR
    return base / f"zarr-{config_slug(cfg)}"


def year_bounds(cfg: dict, year: int) -> tuple[str, str]:
    start = max(cfg["start_date"], f"{year}-01-01")
    end = min(cfg["end_date"], f"{year}-12-31")
    return start, end


def years_of(cfg: dict) -> list[int]:
    y0 = int(cfg["start_date"][:4])
    y1 = int(cfg["end_date"][:4])
    return list(range(y0, y1 + 1))


# --------------------------------------------------------------------- Lauf
def run(cfg: dict, out_dir: Path, progress: Progress | None = None,
        force: bool = False, limit_years: int | None = None) -> int:
    ds = open_store(cfg["zarr_store"])
    lat, lon, land = target_grid(cfg)
    geom = Geometry(ds, cfg, lat, lon)
    n_land = int(land.sum())
    # Landpunkte in derselben Reihenfolge wie die App sie erwartet (zeilenweise)
    punkte_lat, punkte_lon = build_grid(cfg)
    if punkte_lat.size != n_land:
        raise RuntimeError(f"Landmaske uneinheitlich: {punkte_lat.size} vs. {n_land} Punkte")
    log(f"Zielraster: {lat.size}x{lon.size} = {lat.size * lon.size} Punkte, davon Land: {n_land}")

    ckpt = checkpoint_dir(cfg)
    ckpt.mkdir(parents=True, exist_ok=True)
    tmp_dir = ckpt / ".tmp"
    tmp_dir.mkdir(exist_ok=True)

    years = years_of(cfg)
    if limit_years:
        years = years[:limit_years]
    total_days = int(day_range(cfg["start_date"], cfg["end_date"]).size)

    if progress:
        progress.update(phase="Abruf", status="running", blocks_total=len(years),
                        source=SOURCE_TEXT,
                        message=f"{len(years)} Jahre à {n_land} Landpunkte aus "
                                f"{cfg['zarr_store'].split('/')[-1]}")

    todo = [y for y in years if not (ckpt / f"y{y}.npz").exists() or force]
    log(f"Kontrollpunkte: {len(years) - len(todo)} von {len(years)} Jahren vorhanden, "
        f"{len(todo)} offen ({ckpt})")

    deadline = time.monotonic() + float(cfg["max_minutes"]) * 60.0
    started = time.time()
    fetched = 0
    for year in todo:
        if time.monotonic() > deadline:
            log(f"Zeitbudget von {cfg['max_minutes']:.0f} min erreicht – "
                f"die übrigen Jahre bleiben für den nächsten Lauf")
            break
        start, end = year_bounds(cfg, year)
        days = day_range(start, end)
        t0 = time.time()
        log(f"  Jahr {year}: {days.size} Tage ({start} … {end})")
        arrays = harvest_period(ds, cfg, geom, days, land, progress)
        target = ckpt / f"y{year}.npz"
        tmp = tmp_dir / target.name
        np.savez_compressed(tmp, days=(days - np.datetime64("1970-01-01")).astype(np.int64),
                            **arrays)
        os.replace(tmp, target)
        fetched += 1
        dt = time.time() - t0
        done = len(years) - len([y for y in years if not (ckpt / f"y{y}.npz").exists()])
        rate = (time.time() - started) / max(fetched, 1)
        rest = max(0, len(todo) - fetched) * rate / 60.0
        log(f"  Jahr {year} fertig in {dt:.0f}s "
            f"({dt / max(days.size, 1) * 1000:.0f} ms/Tag), "
            f"{done}/{len(years)} Jahre, noch ca. {rest:.0f} min")
        if progress:
            progress.update(force=True, phase="Abruf", blocks_done=done, blocks_total=len(years),
                            percent=round(done / max(len(years), 1) * 100, 1),
                            rate=f"{dt:.0f} s/Jahr",
                            eta_minutes=int(rest),
                            message=f"Jahr {year} gelesen ({done} von {len(years)} Jahren)")

    # ---------------------------------------------------------- Zusammensetzen
    files = sorted(ckpt.glob("y*.npz"))
    if not files:
        log("Keine Kontrollpunkte vorhanden – Abbruch")
        return fetched
    if progress:
        progress.update(force=True, phase="Zusammenbau", percent=99.0,
                        message=f"{len(files)} Jahresblöcke werden zusammengesetzt")

    base = int((np.datetime64(cfg["start_date"]) - np.datetime64("1970-01-01")).astype(int))
    arrays = {k: np.full((n_land, total_days), SENTINEL, dtype=np.int8) for k in VARS}
    covered = 0
    for f in files:
        with np.load(f) as z:
            days = z["days"].astype(np.int64)
            cols = days - base
            ok = (cols >= 0) & (cols < total_days)
            for k in VARS:
                arrays[k][:, cols[ok]] = z[k][:, ok]
        covered += 1
    log(f"Jahresblöcke zusammengesetzt: {covered}/{len(years)}")

    days_all = np.datetime64(cfg["start_date"]) + np.arange(total_days)
    month_of_day = days_all.astype("datetime64[M]").astype(int) % 12
    gap_stats = fill_gaps(cfg, arrays, punkte_lat, punkte_lon, month_of_day)
    ungefuellt = int(gap_stats.get("remaining", 0))
    coverage = 1.0 - gap_stats["missing_point_days"] / max(1, n_land * total_days)
    if gap_stats["missing_point_days"]:
        log(f"  Lücken gefüllt: {gap_stats}")
    log(f"  Abdeckung: {coverage * 100:.2f} % der Punkt-Tage")

    mask = build_land_mask(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    grid_path = out_dir / "climate_grid.npz"
    np.savez_compressed(
        grid_path,
        lat=punkte_lat.astype(np.float32), lon=punkte_lon.astype(np.float32),
        **arrays,
        land_mask_packed=np.packbits(mask.reshape(-1)),
        land_mask_shape=np.array(mask.shape, dtype=np.int32),
    )
    log(f"geschrieben: {grid_path} ({grid_path.stat().st_size / 1e6:.1f} MB)")

    meta = {
        "source": SOURCE_TEXT,
        "attribution": ATTRIBUTION,
        "model": "era5",
        "start_date": str(days_all[0]), "end_date": str(days_all[-1]),
        "n_days": int(total_days), "n_years": round(total_days / 365.25, 2),
        "n_points": int(n_land), "grid_step_deg": cfg["step"],
        "bbox": {"lat_min": cfg["lat_min"], "lat_max": cfg["lat_max"],
                 "lon_min": cfg["lon_min"], "lon_max": cfg["lon_max"]},
        "variables": {
            k: {"scale": SCALES[k], "unit": UNITS[k],
                "api": cfg["zarr_variables"][DERIVATION[k][0]],
                "derived_from": DERIVATION[k][1]}
            for k in VARS},
        "zarr_store": cfg["zarr_store"],
        "sun_threshold_w_m2": cfg["sun_threshold_w_m2"],
        "land_mask": {"step": cfg["mask_step"], "shape": list(mask.shape)},
        "coverage": {"fraction": round(float(coverage), 6),
                     "missing_point_days": int(gap_stats["missing_point_days"]),
                     "filled_from_neighbour": int(gap_stats.get("filled_from_neighbour", 0)),
                     "filled_from_climatology": int(gap_stats.get("filled_from_climatology", 0)),
                     "unfilled_point_days": ungefuellt,
                     "years": f"{covered}/{len(years)}"},
        "generated_at": now_iso(),
    }
    (out_dir / "climate_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False))
    log(f"Metadaten geschrieben: {out_dir / 'climate_meta.json'}")

    # Vollständig heißt: alle Jahre gelesen UND kein Punkt-Tag mehr ohne Wert.
    # Einzelne geschätzte Tage (aus Nachbarpunkten) sind erlaubt – sie stehen
    # ehrlich als coverage in den Metadaten und im Qualitätsbericht.
    complete = (covered == len(years)) and ungefuellt == 0
    if complete:
        for f in files:
            f.unlink()
        for pfad in (tmp_dir, ckpt):          # zuerst der Unterordner, dann der Elternordner
            try:
                pfad.rmdir()
            except OSError:
                pass
        log("Vollständig – Kontrollpunkte aufgeräumt")
        if progress:
            progress.update(force=True, phase="Fertig", status="done", percent=100.0,
                            blocks_done=len(years), blocks_total=len(years), eta_minutes=0,
                            message=f"Datensatz vollständig: {n_land} Punkte × {total_days} Tage, "
                                    f"Abdeckung {coverage * 100:.1f} %")
    else:
        log(f"Unvollständig ({covered}/{len(years)} Jahre, {ungefuellt} Punkt-Tage ohne Wert) "
            f"– Kontrollpunkte bleiben erhalten")
        if progress:
            progress.update(force=True, phase="Teilstand", status="running",
                            percent=round(covered / max(len(years), 1) * 100, 1),
                            message=f"{covered} von {len(years)} Jahren vorhanden – "
                                    f"nächster Lauf macht weiter")
    return fetched


def target_grid(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zielgitter (1-D-Achsen + Landmaske) – gleiche Geometrie wie die App."""
    from global_land_mask import globe

    lats = np.arange(cfg["lat_min"], cfg["lat_max"] + 1e-9, cfg["step"])
    lons = np.arange(cfg["lon_min"], cfg["lon_max"] + 1e-9, cfg["step"])
    lon2, lat2 = np.meshgrid(lons, lats)
    land = globe.is_land(lat2.ravel(), lon2.ravel())
    if land.dtype != bool:
        land = land > 0.5
    return lats, lons, land.reshape(lat2.shape)


def load_config(args) -> dict:
    cfg = dict(BASE_CFG)
    cfg.update(ZARR_CFG)
    cfg_file = ROOT / "scripts" / "grid_config.json"
    if cfg_file.exists():
        loaded = json.loads(cfg_file.read_text())
        cfg.update(loaded)
        # Die Zarr-Quelle hat Vorrang vor Resten der API-Konfiguration
        cfg.setdefault("source", "era5_zarr")
        for key, default in ZARR_CFG.items():
            if key in ("source",):
                continue
            loaded_value = loaded.get(key)
            if isinstance(default, dict) and isinstance(loaded_value, dict):
                merged = dict(default)
                merged.update(loaded_value)
                cfg[key] = merged
    cfg.update({k: v for k, v in ZARR_CFG.items() if k not in cfg})
    if args.store:
        cfg["zarr_store"] = args.store
    if args.step:
        cfg["step"] = args.step
    if args.start:
        cfg["start_date"] = args.start
    if args.end:
        cfg["end_date"] = args.end
    if args.max_minutes:
        cfg["max_minutes"] = args.max_minutes
    if args.threshold:
        cfg["sun_threshold_w_m2"] = args.threshold
    if args.test:
        # Mini-Lauf zum Ausprobieren: kleiner Ausschnitt, ein Monat, hartes Zeitlimit.
        # Explizit übergebene CLI-Werte bleiben stehen.
        test = {"step": 3.0, "lat_min": 36.0, "lat_max": 60.0, "lon_min": -6.0,
                "lon_max": 24.0, "days_per_read": 5, "max_minutes": 20,
                "test_mode": True,
                "end_date": str(np.datetime64(cfg["start_date"]) + np.timedelta64(30, "D"))}
        for key, value in test.items():
            if key == "end_date" and args.end:
                continue
            if key == "step" and args.step:
                continue
            cfg[key] = value
        if args.start:
            cfg["start_date"] = args.start
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None, help="anderer Zarr-Speicher (gs://… oder Pfad)")
    ap.add_argument("--out", default=None, help="Zielordner (Standard: app/data)")
    ap.add_argument("--step", type=float, default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--threshold", type=float, default=None,
                    help="Schwelle für Sonnenschein in W/m² (Standard 120)")
    ap.add_argument("--years", type=int, default=None, help="nur die ersten N Jahre (Test)")
    ap.add_argument("--test", action="store_true", help="Mini-Ausschnitt, kurze Zeitspanne")
    ap.add_argument("--force", action="store_true", help="Kontrollpunkte neu schreiben")
    ap.add_argument("--reset", action="store_true", help="Kontrollpunkte verwerfen")
    ap.add_argument("--no-progress", action="store_true",
                    help="keine Fortschrittsdatei schreiben/veröffentlichen")
    args = ap.parse_args()

    cfg = load_config(args)
    out_dir = Path(args.out) if args.out else OUT_DIR

    if args.reset:
        import shutil
        shutil.rmtree(checkpoint_dir(cfg), ignore_errors=True)

    log("Konfiguration: " + json.dumps(
        {k: cfg[k] for k in ("source", "zarr_store", "step", "start_date", "end_date",
                             "days_per_read", "sun_threshold_w_m2", "max_minutes", "test_mode")
         if k in cfg}, ensure_ascii=False))

    progress = None
    if not args.no_progress:
        run_url = ""
        if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID"):
            server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
            run_url = f"{server}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        # Logdatei für die letzten Zeilen im Balken: im Workflow wird die
        # Ausgabe nach fetch.log umgeleitet (KLIMA_LOG_FILE), lokal nach stdout.
        tail = Path(os.environ["KLIMA_LOG_FILE"]) if os.environ.get("KLIMA_LOG_FILE") \
            else ROOT / "build-log.txt"
        progress = Progress(out_dir=out_dir, tail_source=tail, run_url=run_url)
        progress.update(force=True, phase="Start", status="running", percent=0.5,
                        blocks_done=0, source=SOURCE_TEXT,
                        message=f"ERA5-Abruf gestartet ({cfg['start_date']} … {cfg['end_date']}, "
                                f"{cfg['step']}° über Europa)")

    t0 = time.time()
    try:
        fetched = run(cfg, out_dir, progress, force=args.force, limit_years=args.years)
    except Exception as exc:  # noqa: BLE001
        log(f"ABBRUCH: {type(exc).__name__}: {exc}")
        if progress:
            progress.finish("failed", f"{type(exc).__name__}: {exc}", phase="Fehler")
        raise
    log(f"fertig in {time.time() - t0:.0f}s, {fetched} Jahre in diesem Lauf")
    if progress and fetched == 0:
        progress.update(force=True, message="Keine neuen Jahre gelesen (Kontrollpunkte vollständig)")


if __name__ == "__main__":
    main()
