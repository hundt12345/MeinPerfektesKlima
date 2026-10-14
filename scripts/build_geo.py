#!/usr/bin/env python3
"""Baut die statischen Geo-Assets für die App:

  app/data/countries.geojson  - Landgrenzen als Linien (Europa + Anrainer)
  app/data/cities.json        - Städte in Europa (Name, Land, Koordinaten, Einwohner)

Eingaben (siehe scripts/download_sources.sh und scripts/build_countries.mjs):
  data/raw/countries-world.geojson    (aus world-atlas / Natural Earth 1:10m)
  data/raw/borders-world.geojson      (Landgrenzen, topojson.mesh)
  data/raw/geonames_cities15000.csv   (GeoNames cities15000)

Aufruf:  python scripts/build_geo.py

Zwei Fallen, die früher sichtbare Fehler verursacht haben:

* **Datumsgrenze.** Russland (Tschukotka, Wrangelinsel) und Fidschi kreuzen in
  world-atlas den 180. Längengrad; die Längengrade springen dort von +180 auf −180.
  Planar gelesen entstehen daraus Flächen quer über die Welt – auf der Karte als
  gerade „Striche“ bei 65° N, 69° N und 71° N, und Städte zwischen 65° und 69° N
  (z. B. Murmansk) fielen aus der Liste. Die Ringe werden deshalb vorher stetig
  gemacht (:func:`fix_antimeridian`).
* **Kartenausschnitt.** Werden *Flächen* auf den Ausschnitt zugeschnitten, bekommt
  jedes angeschnittene Land eine gerade Kante entlang des Ausschnitts (29° N, 50° E,
  74° N, 30° W). Gezeichnet werden deshalb nur Grenz-*Linien*; zugeschnittene
  Linien haben keine künstlichen Kanten.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from shapely.geometry import LineString, MultiLineString, Point, box, mapping, shape
from shapely.ops import linemerge
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.names import EXTRA_ALIASES, GERMAN_NAMES, search_key  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "app" / "data"

# Ausschnitt der Karte (etwas größer als der Datenausschnitt)
CLIP = box(-30.0, 29.0, 50.0, 74.0)
# Länder, die als Städte-Lieferanten zählen (Europa i.w.S.)
EUROPE_COUNTRIES = {
    "Albania", "Andorra", "Austria", "Belarus", "Belgium", "Bosnia and Herzegovina",
    "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland",
    "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Kosovo",
    "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Moldova", "Monaco",
    "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland", "Portugal",
    "Romania", "Russia", "San Marino", "Serbia", "Slovakia", "Slovenia", "Spain",
    "Sweden", "Switzerland", "Turkey", "Ukraine", "United Kingdom", "Vatican",
    "Georgia", "Armenia", "Azerbaijan",
    "Faroe Islands", "Åland", "Gibraltar", "Guernsey", "Jersey", "Isle of Man",
}
# Kurz- bzw. Sondernamen in Natural Earth -> Name in EUROPE_COUNTRIES. Ohne diese
# Zuordnung fehlten z. B. Sarajevo und Skopje („Bosnia and Herz.“, „Macedonia“).
NE_COUNTRY_NAMES = {
    "Bosnia and Herz.": "Bosnia and Herzegovina", "Macedonia": "North Macedonia",
    "N. Cyprus": "Cyprus", "Cyprus U.N. Buffer Zone": "Cyprus", "Akrotiri": "Cyprus",
    "Dhekelia": "Cyprus", "Faeroe Is.": "Faroe Islands", "Vatican": "Vatican",
}
# Punkte knapp vor der (vereinfachten) Küste gehören zum nächsten Land
# (Kopenhagen, Split, Ajaccio lagen sonst „im Meer“ und fehlten).
COAST_TOLERANCE_DEG = 0.1

# Deutsche Ländernamen für die Anzeige
DE_NAMES = {
    "Albania": "Albanien", "Andorra": "Andorra", "Austria": "Österreich",
    "Belarus": "Belarus", "Belgium": "Belgien", "Bosnia and Herzegovina": "Bosnien-Herzegowina",
    "Bulgaria": "Bulgarien", "Croatia": "Kroatien", "Cyprus": "Zypern", "Czechia": "Tschechien",
    "Denmark": "Dänemark", "Estonia": "Estland", "Finland": "Finnland", "France": "Frankreich",
    "Germany": "Deutschland", "Greece": "Griechenland", "Hungary": "Ungarn", "Iceland": "Island",
    "Ireland": "Irland", "Italy": "Italien", "Kosovo": "Kosovo", "Latvia": "Lettland",
    "Liechtenstein": "Liechtenstein", "Lithuania": "Litauen", "Luxembourg": "Luxemburg",
    "Malta": "Malta", "Moldova": "Moldau", "Monaco": "Monaco", "Montenegro": "Montenegro",
    "Netherlands": "Niederlande", "North Macedonia": "Nordmazedonien", "Norway": "Norwegen",
    "Poland": "Polen", "Portugal": "Portugal", "Romania": "Rumänien", "Russia": "Russland",
    "San Marino": "San Marino", "Serbia": "Serbien", "Slovakia": "Slowakei", "Slovenia": "Slowenien",
    "Spain": "Spanien", "Sweden": "Schweden", "Switzerland": "Schweiz", "Turkey": "Türkei",
    "Ukraine": "Ukraine", "United Kingdom": "Vereinigtes Königreich", "Vatican": "Vatikan",
    "Georgia": "Georgien", "Armenia": "Armenien", "Azerbaijan": "Aserbaidschan",
    "Faroe Islands": "Färöer", "Åland": "Åland", "Gibraltar": "Gibraltar", "Guernsey": "Guernsey",
    "Jersey": "Jersey", "Isle of Man": "Isle of Man",
    "Algeria": "Algerien", "Morocco": "Marokko", "Tunisia": "Tunesien", "Libya": "Libyen",
    "Egypt": "Ägypten", "Syria": "Syrien", "Iraq": "Irak", "Iran": "Iran",
    "Kazakhstan": "Kasachstan", "Jordan": "Jordanien", "Lebanon": "Libanon",
}


def round_coords(obj, ndigits=3):
    if isinstance(obj, (list, tuple)):
        return [round_coords(x, ndigits) for x in obj]
    if isinstance(obj, float):
        return round(obj, ndigits)
    return obj


def _unwrap(ring: list) -> list:
    """Längengrade eines Rings stetig machen (kein Sprung über ±180°)."""
    out = [[float(ring[0][0]), float(ring[0][1])]]
    offset = 0.0
    for (x0, _y0), (x1, y1) in zip(ring, ring[1:]):
        if x1 - x0 > 180:
            offset -= 360.0
        elif x1 - x0 < -180:
            offset += 360.0
        out.append([float(x1) + offset, float(y1)])
    return out


def fix_antimeridian(geometry: dict) -> dict:
    """Polygone, die den 180. Längengrad kreuzen, für planare Rechnungen reparieren.

    Nach dem Entfalten liegt z. B. Tschukotka bei 180–190° statt quer über die
    ganze Welt; der europäische Teil bleibt in −180…180°. Ringe, die sich danach
    nicht schließen (Antarktis umrundet den Pol), bleiben unverändert.
    """
    if geometry["type"] not in ("Polygon", "MultiPolygon"):
        return geometry
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    fixed = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            unwrapped = _unwrap(ring)
            if abs(unwrapped[-1][0] - unwrapped[0][0]) > 1e-6:
                unwrapped = [[float(x), float(y)] for x, y in ring]
            rings.append(unwrapped)
        xs = [x for x, _ in rings[0]]
        shift = 360.0 if min(xs) < -180 else (-360.0 if min(xs) >= 180 else 0.0)
        center = (min(xs) + max(xs)) / 2 + shift
        out = []
        for i, ring in enumerate(rings):
            k = shift if i == 0 else round((center - sum(x for x, _ in ring) / len(ring)) / 360.0) * 360.0
            out.append([[x + k, y] for x, y in ring])
        fixed.append(out)
    return {"type": "MultiPolygon", "coordinates": fixed}


def _split_line(line: list) -> list[list]:
    """Linie an Sprüngen über die Datumsgrenze auftrennen."""
    parts, current = [], [line[0]]
    for a, b in zip(line, line[1:]):
        if abs(b[0] - a[0]) > 180:
            parts.append(current)
            current = []
        current.append(b)
    parts.append(current)
    return [p for p in parts if len(p) >= 2]


def build_countries() -> None:
    """Landgrenzen (zwischen zwei Ländern) als Linien, auf den Ausschnitt zugeschnitten."""
    mesh = json.loads((RAW / "borders-world.geojson").read_text())
    geometry = mesh.get("geometry", mesh)
    lines = [LineString(part) for line in geometry["coordinates"] for part in _split_line(line)]
    clipped = MultiLineString(lines).intersection(CLIP)
    merged = linemerge(clipped) if not clipped.is_empty else clipped
    parts = list(getattr(merged, "geoms", [merged]))
    out_lines = []
    for part in parts:
        if part.geom_type != "LineString" or part.length < 0.05:
            continue
        simple = part.simplify(0.01, preserve_topology=False)
        coords = round_coords(mapping(simple)["coordinates"])
        if len(coords) >= 2:
            out_lines.append(coords)
    out = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"kind": "land_borders", "source": "Natural Earth 1:10m (world-atlas 2.0.2)"},
        "geometry": {"type": "MultiLineString", "coordinates": out_lines},
    }]}
    (OUT / "countries.geojson").write_text(json.dumps(out, separators=(",", ":")))
    size = (OUT / "countries.geojson").stat().st_size
    print(f"countries.geojson: {len(out_lines)} Grenzlinien, {size/1024:.0f} kB")


def _country_shapes() -> tuple[list, list[str]]:
    world = json.loads((RAW / "countries-world.geojson").read_text())
    geoms, names = [], []
    for f in world["features"]:
        name = (f.get("properties") or {}).get("name") or "?"
        g = shape(fix_antimeridian(f["geometry"]))
        if not g.is_valid:
            g = g.buffer(0)
        geoms.append(g)
        names.append(NE_COUNTRY_NAMES.get(name, name))
    return geoms, names


def _city_record(name: str, country: str, lat: float, lon: float, pop: int) -> dict:
    display = GERMAN_NAMES.get(name, name)
    aliases = [a for a in dict.fromkeys(EXTRA_ALIASES.get(name, [])) if a not in (display, name)]
    record = {
        "name": display,
        "country": country,
        "name_de": DE_NAMES.get(country, country),
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "pop": pop,
        "aliases": aliases,
    }
    if display != name:
        record["name_local"] = name
    record["search"] = search_key(record)
    return record


def build_cities(min_pop: int = 25000) -> None:
    geoms, names = _country_shapes()
    tree = STRtree(geoms)

    def country_of(lat: float, lon: float) -> str | None:
        p = Point(lon, lat)
        for i in tree.query(p):
            if geoms[i].contains(p):
                return names[i] if names[i] in EUROPE_COUNTRIES else None
        # Nicht in einer Fläche: Küstenpunkt? Nächstes Land in der Toleranz.
        best, best_d = None, COAST_TOLERANCE_DEG
        for i in tree.query(p.buffer(COAST_TOLERANCE_DEG)):
            d = geoms[i].distance(p)
            if d <= best_d:
                best, best_d = names[i], d
        return best if best in EUROPE_COUNTRIES else None

    rows = []
    with open(RAW / "geonames_cities15000.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            # Etwas größer als das Klimaraster (34–72° N); Madeira (32,6° N) läge
            # weit außerhalb und bekäme Werte eines fernen Rasterpunkts.
            if not (33.5 < lat < 73.0 and -27.0 < lon < 47.0):
                continue
            try:
                pop = int(row["population"])
            except (TypeError, ValueError):
                pop = 0
            name = (row["name"] or "").strip()
            if not name:
                continue
            rows.append((name, lat, lon, pop))

    cities, smaller = [], {}
    for name, lat, lon, pop in rows:
        country = country_of(lat, lon)
        if country is None:
            continue
        if pop >= min_pop:
            cities.append(_city_record(name, country, lat, lon, pop))
        elif pop > smaller.get(country, (None, -1))[1]:
            smaller[country] = (name, pop, lat, lon)
    # Kleine Länder ohne Stadt ≥ min_pop (Malta, Andorra, …) behalten ihren größten Ort.
    present = {c["country"] for c in cities}
    for country, (name, pop, lat, lon) in sorted(smaller.items()):
        if country not in present:
            cities.append(_city_record(name, country, lat, lon, pop))

    # Duplikate entfernen: gleicher Name sehr nah beieinander, oder ein Stadtteil
    # mit exakt derselben Einwohnerzahl wie die Stadt („City of London“).
    cities.sort(key=lambda c: -c["pop"])
    kept = []
    for c in cities:
        if any((abs(c["lat"] - k["lat"]) < 0.35 and abs(c["lon"] - k["lon"]) < 0.5
                and k["name"].lower() == c["name"].lower())
               or (abs(c["lat"] - k["lat"]) < 0.1 and abs(c["lon"] - k["lon"]) < 0.15
                   and k["pop"] == c["pop"]) for k in kept):
            continue
        kept.append(c)
    (OUT / "cities.json").write_text(json.dumps(kept, separators=(",", ":"), ensure_ascii=False))
    size = (OUT / "cities.json").stat().st_size
    print(f"cities.json: {len(kept)} Städte, {size/1024:.0f} kB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_countries()
    build_cities()
