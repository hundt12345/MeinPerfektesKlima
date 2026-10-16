#!/usr/bin/env python3
"""Trägt die Höhenlage jeder Stadt in ``app/data/cities.json`` nach (Feld ``elev_m``).

Warum: Eine Stadt in einem Gebirgstal bekommt sonst die Klimawerte ihrer
0,75°-Rasterzelle, die im Schnitt über 1000 m höher liegt als der Ort selbst
(Meran 310 m, zugeordnete Zelle ~1990 m). Mit der Höhe des Ortes lässt sich
dieser Unterschied physikalisch korrigieren (``app/elevation.py``).

Herkunft der Höhen: Feld ``dem`` des GeoNames-Dumps (SRTM3/GTOPO30, Meter).
``scripts/build_geo.py`` nimmt die Spalte beim Bau der Städteliste direkt mit;
dieses Skript ist der Weg für einen Stand von ``cities.json``, der ohne Höhe
gebaut wurde.

Zuordnung: nächster Eintrag des Dumps innerhalb von 2 km (Koordinaten beider
Listen stammen aus GeoNames, treffen also exakt). Kein Treffer oder keine Höhe
⇒ kein ``elev_m``-Feld; die App rechnet dann unkorrigiert weiter.

Aufruf:
    python scripts/build_city_elevation.py [--dump data/raw/geonames/cities500.txt]
                                           [--cities app/data/cities.json]

Der Dump ist ein GeoNames-Export (TSV, 19 Felder; Feld 17 = ``dem``) oder eine
CSV mit Kopfzeile (``dem``/``elevation``). Ohne Angabe wird in ``data/raw``
nach ``cities500.txt``, ``cities15000.txt`` oder ``geonames_cities15000.csv``
gesucht.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CITIES = ROOT / "app" / "data" / "cities.json"
MAX_KM = 2.0
MIN_M, MAX_M = -500.0, 9000.0


def _read_dump(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lat, lon, dem) aus einem GeoNames-Export (TSV oder CSV mit Kopfzeile)."""
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    head = text[0].split("\t")
    is_csv = len(head) < 5 or head[0].strip().lower() in ("geonameid", "id", "name")
    lats: list[float] = []
    lons: list[float] = []
    dems: list[float] = []
    if is_csv:
        reader = csv.DictReader(text)
        for row in reader:
            try:
                lats.append(float(row["latitude"]))
                lons.append(float(row["longitude"]))
            except (KeyError, TypeError, ValueError):
                continue
            raw = (row.get("dem") or row.get("elevation") or "").strip()
            try:
                dems.append(float(raw))
            except ValueError:
                dems.append(float("nan"))
    else:
        for line in text:
            parts = line.split("\t")
            if len(parts) < 17:
                continue
            try:
                lats.append(float(parts[4]))
                lons.append(float(parts[5]))
                dems.append(float(parts[16]) if parts[16].strip() else float("nan"))
            except ValueError:
                continue
    if not lats:
        raise SystemExit(f"{path}: keine verwertbaren Zeilen")
    return (np.asarray(lats), np.asarray(lons), np.asarray(dems))


def _xyz(lat, lon):
    la, lo = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default=None, help="GeoNames-Export (TSV oder CSV)")
    ap.add_argument("--cities", default=str(DEFAULT_CITIES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    candidates = ([Path(args.dump)] if args.dump else
                  [ROOT / "data" / "raw" / "geonames" / "cities500.txt",
                   ROOT / "data" / "raw" / "geonames" / "cities15000.txt",
                   ROOT / "data" / "raw" / "geonames_cities15000.csv"])
    dump = next((p for p in candidates if p and p.exists()), None)
    if dump is None:
        raise SystemExit("Kein GeoNames-Dump gefunden. Gesucht wurden:\n  "
                         + "\n  ".join(str(p) for p in candidates)
                         + "\nQuelle: https://download.geonames.org/export/dump/")

    cities_path = Path(args.cities)
    cities = json.loads(cities_path.read_text(encoding="utf-8"))

    la, lo, dem = _read_dump(dump)
    from scipy.spatial import cKDTree
    tree = cKDTree(_xyz(la, lo))
    dist, idx = tree.query(_xyz([c["lat"] for c in cities], [c["lon"] for c in cities]), k=1)
    km = np.atleast_1d(dist) * 6371.0
    idx = np.atleast_1d(idx)

    matched = with_height = changed = 0
    for i, city in enumerate(cities):
        value = float("nan")
        if km[i] <= MAX_KM:
            matched += 1
            candidate = float(dem[idx[i]])
            if np.isfinite(candidate) and candidate > -9999:
                value = candidate
        if not np.isfinite(value):
            city.pop("elev_m", None)
            continue
        value = int(np.clip(round(value), MIN_M, MAX_M))
        with_height += 1
        if city.get("elev_m") != value:
            city["elev_m"] = value
            changed += 1

    print(f"{len(cities)} Städte · {matched} im Dump gefunden (≤ {MAX_KM} km) · "
          f"{with_height} mit Höhenlage · {changed} geändert")
    for name in ("Meran", "Bozen", "Innsbruck", "Chur", "Aosta", "Sitten", "München",
                 "Zürich", "Berlin", "Madrid"):
        hit = next((c for c in cities if c["name"] == name), None)
        if hit:
            print(f"  {name:10s} {hit.get('elev_m')} m")

    if args.dry_run:
        print("dry-run: nichts geschrieben")
        return 0
    cities_path.write_text(json.dumps(cities, separators=(",", ":"), ensure_ascii=False),
                           encoding="utf-8")
    print(f"geschrieben: {cities_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
