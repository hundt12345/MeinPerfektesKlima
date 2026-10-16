#!/usr/bin/env bash
# Lädt die Rohquellen für die statischen Assets (Ländergrenzen, Städteliste).
#
#   - world-atlas (npm)  -> TopoJSON der Ländergrenzen (Natural Earth 1:10m)
#   - all-the-cities     -> nicht verwendet
#   - GeoNames cities15000 (Spiegel als CSV) -> Städte inkl. Koordinaten
#   - GeoNames cities500 (GitHub-Spiegel)    -> Stadthöhen (Feld ``dem``)
#   - ETOPO1 10' (GitHub-Spiegel g2e/etopo10) -> mittlere Höhe der Rasterzellen
#
# Aufruf:  bash scripts/download_sources.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAW="$ROOT/data/raw"
mkdir -p "$RAW"

echo "== world-atlas (Ländergrenzen) =="
curl -fsSL -o "$RAW/world-atlas.tgz" https://registry.npmjs.org/world-atlas/-/world-atlas-2.0.2.tgz
tar xzf "$RAW/world-atlas.tgz" -C "$RAW"
mkdir -p "$RAW/world-atlas"
cp "$RAW/package/countries-10m.json" "$RAW/world-atlas/countries-10m.json"
cp "$RAW/package/countries-50m.json" "$RAW/world-atlas/countries-50m.json"
rm -rf "$RAW/package" "$RAW/world-atlas.tgz"

echo "== Leaflet (vendored für die Karte) =="
curl -fsSL -o "$RAW/leaflet.tgz" https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz
tar xzf "$RAW/leaflet.tgz" -C "$RAW"
mkdir -p "$ROOT/app/static/vendor/leaflet/images"
cp "$RAW/package/dist/leaflet.js"  "$ROOT/app/static/vendor/leaflet/leaflet.js"
cp "$RAW/package/dist/leaflet.css" "$ROOT/app/static/vendor/leaflet/leaflet.css"
cp "$RAW/package/dist/images/"*    "$ROOT/app/static/vendor/leaflet/images/" 2>/dev/null || true
rm -rf "$RAW/package" "$RAW/leaflet.tgz"

echo "== GeoNames cities15000 (Städte) =="
if [ ! -s "$RAW/geonames_cities15000.csv" ]; then
  curl -fsSL -o "$RAW/geonames_cities15000.csv" \
    https://raw.githubusercontent.com/curran/data/gh-pages/all/geonames_cities15000.csv
fi

echo "== GeoNames cities500 (Stadthöhen, Feld dem) =="
mkdir -p "$RAW/geonames"
if [ ! -s "$RAW/geonames/cities500.txt" ]; then
  curl -fsSL -o "$RAW/geonames.tgz" \
    https://codeload.github.com/SaveTheRbtz/geonames/tar.gz/refs/heads/master
  tar xzf "$RAW/geonames.tgz" -C "$RAW" geonames-master/20200405/cities500.txt
  cp "$RAW/geonames-master/20200405/cities500.txt" "$RAW/geonames/cities500.txt"
  rm -rf "$RAW/geonames-master" "$RAW/geonames.tgz"
fi

echo "== ETOPO1 10' (mittlere Höhe der Rasterzellen) =="
mkdir -p "$RAW/etopo10"
if [ ! -s "$RAW/etopo10/etopo10_ice_g_i2.bin" ]; then
  curl -fsSL -o "$RAW/etopo10.tgz" \
    https://codeload.github.com/g2e/etopo10/tar.gz/refs/heads/master
  tar xzf "$RAW/etopo10.tgz" -C "$RAW" \
    etopo10-master/etopo10_ice_g_i2.bin etopo10-master/etopo10_ice_g_i2.hdr \
    etopo10-master/etopo10_bed_g_i2.bin etopo10-master/etopo10_bed_g_i2.hdr
  cp "$RAW/etopo10-master/"* "$RAW/etopo10/"
  rm -rf "$RAW/etopo10-master" "$RAW/etopo10.tgz"
fi

echo "OK - Quellen liegen in data/raw/"
echo "Danach: python scripts/build_elevation.py && python scripts/build_city_elevation.py"
