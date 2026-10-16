#!/usr/bin/env python3
"""Baut ``app/data/grid_elevation.npz``: mittlere Höhenlage jeder Klima-Rasterzelle.

Warum das nötig ist: Das Klimaraster hat 0,75° Schritt (ca. 55–85 km). Eine
solche Zelle ist in den Alpen, den Pyrenäen oder in Skandinavien viele hundert
bis über 2000 m höher als die Stadt, der sie zugeordnet wird. Meran (310 m)
bekommt die Werte einer Zelle mit einer mittleren Höhe von ~2090 m – deshalb
zeigt die Ortskarte für Meran einen Juli mit 16 °C Tageshöchstwert statt der
realen ~28,5 °C. Die Höhenlage der Zelle ist die Eingangsgröße, um Stadtwerte
physikalisch (Lapsrate) auf die Höhe des Ortes umzurechnen (``app/elevation.py``).

Quelle: **ETOPO1** (NOAA/NCEI, public domain), hier in der 10-Arkminuten-Fassung
(``etopo10_*_g_i2.bin``, 2161×1081 int16, Zellmitte −180+(i+0,5)·Δ bzw.
90−(j+0,5)·Δ, Δ=1/6°). Download siehe ``scripts/download_sources.sh``
(Spiegel: github.com/g2e/etopo10, abgeleitet aus ETOPO1). Lizenz: public domain.

Aufruf:
    bash scripts/download_sources.sh          # lädt data/raw/etopo10/…
    python scripts/build_elevation.py

Ausgabe: ``app/data/grid_elevation.npz`` mit ``lat``, ``lon`` (wie das
Klimaraster) und ``elev`` (int16, Meter über NN). Dazu ``grid_elevation_meta.json``
mit Quelle, Lizenz und Geometrie.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RAW = ROOT / "data" / "raw" / "etopo10"
OUT = ROOT / "app" / "data"
GRID = OUT / "climate_grid.npz"
META = OUT / "climate_meta.json"

SOURCE_URL = "https://codeload.github.com/g2e/etopo10/tar.gz/refs/heads/master"
ATTRIBUTION = ("ETOPO1 (NOAA National Centers for Environmental Information, public domain), "
               "10-Arkminuten-Fassung, Mittel über die Landfläche der Rasterzelle")
LICENSE = "public domain (ETOPO1, Amante & Eakins 2009)"


def read_esri_hdr(path: Path) -> dict:
    """Liest einen ESRI-.hdr (Schlüssel/Wert, CR oder LF getrennt)."""
    hdr: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(" ")
        hdr[key.strip().upper()] = value.strip()
    return hdr


def load_etopo(bin_path: Path) -> tuple[np.ndarray, dict]:
    """ETOPO-Raster als (lat, lon, Werte in Metern)."""
    hdr = read_esri_hdr(bin_path.with_suffix(".hdr"))
    ncols = int(hdr["NCOLS"])
    nrows = int(hdr["NROWS"])
    cell = float(hdr["CELLSIZE"])
    xll = float(hdr.get("XLLCENTER", hdr.get("XLLCORNER", "-180")))
    yll = float(hdr.get("YLLCENTER", hdr.get("YLLCORNER", "-90")))
    nodata = float(hdr.get("NODATA_VALUE", "-32768"))
    order = hdr.get("BYTEORDER", "LSBFIRST").upper()
    dtype = np.dtype("<i2") if order.startswith("LSB") else np.dtype(">i2")
    data = np.fromfile(bin_path, dtype=dtype)
    if data.size != ncols * nrows:
        raise SystemExit(f"{bin_path.name}: {data.size} Werte, erwartet {ncols*nrows}")
    grid = data.reshape(nrows, ncols).astype(np.float32)
    grid[grid <= nodata] = np.nan
    # Zellmitten: die erste Zeile liegt im Norden (ESRI/GMT-Konvention).
    lats = yll + (nrows - np.arange(nrows) - 0.5) * cell
    lons = xll + (np.arange(ncols) + 0.5) * cell
    return grid, {"lats": lats, "lons": lons, "cell": cell, "shape": (nrows, ncols)}


def cell_mean_elevation(grid: np.ndarray, geo: dict, lat: float, lon: float,
                        half: float) -> float:
    """Mittlere Höhe der Landfläche in der Klimazelle um (lat, lon).

    ``half`` ist der halbe Zellabstand des Klimarasters (0,375° bei 0,75°).
    Gezählt werden alle ETOPO-Zellen, deren *Mitte* in dem Kasten liegen; Meeres-
    und Seeflächen (Höhe ≤ 0) fallen heraus, sonst würde eine Küstenzelle mit
    halbem Atlantik als „auf Meereshöhe“ gezählt.
    """
    lats, lons = geo["lats"], geo["lons"]
    rows = (lats >= lat - half) & (lats <= lat + half)
    cols = (lons >= lon - half) & (lons <= lon + half)
    if not rows.any() or not cols.any():
        return float("nan")
    block = grid[np.ix_(rows, cols)]
    land = block[block > 0]
    if land.size == 0:
        return float("nan")
    return float(land.mean())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", default=str(RAW / "etopo10_ice_g_i2.bin"),
                    help="ETOPO-Raster (int16, ESRI .hdr daneben)")
    ap.add_argument("--out", default=str(OUT), help="Zielverzeichnis")
    ap.add_argument("--grid", default=str(GRID), help="Klimaraster (lat/lon der Zellen)")
    ap.add_argument("--dry-run", action="store_true", help="nur prüfen, nichts schreiben")
    args = ap.parse_args()

    bin_path = Path(args.bin)
    if not bin_path.exists():
        raise SystemExit(
            f"ETOPO-Raster fehlt: {bin_path}\n"
            f"Quelle: {SOURCE_URL}\n"
            "  -> bash scripts/download_sources.sh")

    grid = np.load(args.grid)
    lat = grid["lat"].astype(np.float64)
    lon = grid["lon"].astype(np.float64)
    step = 0.75
    meta_path = Path(args.grid).with_name("climate_meta.json")
    if meta_path.exists():
        step = float(json.loads(meta_path.read_text()).get("grid_step_deg", step))

    etopo, geo = load_etopo(bin_path)
    half = step / 2.0
    elev = np.array([cell_mean_elevation(etopo, geo, float(a), float(b), half)
                     for a, b in zip(lat, lon)], dtype=np.float32)

    ok = np.isfinite(elev)
    if not ok.all():
        # Kein Land in der Zelle (sollte im Klimaraster nicht vorkieren): der
        # nächstgelegene gültige Wert springt ein.
        if ok.any():
            idx = np.flatnonzero(ok)
            pos = np.searchsorted(idx, np.flatnonzero(~ok))
            pos = np.clip(pos, 0, idx.size - 1)
            elev[~ok] = elev[idx[pos]]
        else:
            raise SystemExit("Keine einzige Zelle hat Land – Raster/ETOPO passen nicht zusammen.")

    values = np.rint(elev).astype(np.int16)
    print(f"Zellen: {values.size} · Höhe {int(values.min())} .. {int(values.max())} m, "
          f"Median {int(np.median(values))} m")
    print(f"ETOPO: {geo['shape'][1]}×{geo['shape'][0]} Zellen à {geo['cell']*60:.1f}′")

    # Plausibilität: die höchsten Zellen müssen im Gebirge liegen, die tiefsten
    # an der Küste; die Nordsee muss negativ sein.
    top = np.argsort(-values)[:5]
    for i in top:
        print(f"  höchste Zelle {lat[i]:.2f}° N {lon[i]:.2f}° O: {int(values[i])} m")
    for name, la, lo in (("Meran", 46.67, 11.16), ("München", 48.14, 11.58),
                         ("Berlin", 52.52, 13.41), ("Mailand", 45.46, 9.19)):
        d = np.hypot(lat - la, (lon - lo) * np.cos(np.radians(la)))
        i = int(np.argmin(d))
        print(f"  Referenz {name}: Zelle ({lat[i]:.2f}, {lon[i]:.2f}) = {int(values[i])} m")

    if args.dry_run:
        print("dry-run: nichts geschrieben")
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "grid_elevation.npz",
                        lat=lat.astype(np.float32), lon=lon.astype(np.float32),
                        elev=values)
    info = {
        "source": ATTRIBUTION,
        "license": LICENSE,
        "source_url": SOURCE_URL,
        "method": ("Mittel der ETOPO1-Landfläche (Höhe > 0) über alle 10′-Zellen, deren Mitte "
                   f"innerhalb ±{half}° der Klima-Rasterzelle liegen"),
        "cell_step_deg": step,
        "n_points": int(values.size),
        "unit": "m",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "grid_elevation_meta.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    size = (out / "grid_elevation.npz").stat().st_size / 1024
    print(f"geschrieben: {out/'grid_elevation.npz'} ({size:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
