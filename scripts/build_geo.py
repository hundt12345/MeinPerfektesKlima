#!/usr/bin/env python3
"""Baut die statischen Geo-Assets für die App:

  app/data/countries.geojson  - vereinfachte Ländergrenzen (Europa + Anrainer)
  app/data/cities.json        - Städte in Europa (Name, Land, Koordinaten, Einwohner)

Eingaben (siehe scripts/download_sources.sh):
  data/raw/countries-world.geojson    (aus world-atlas / Natural Earth 1:10m)
  data/raw/geonames_cities15000.csv   (GeoNames cities15000)

Aufruf:  python scripts/build_geo.py
"""
from __future__ import annotations

import csv
import json
import unicodedata
from pathlib import Path

from shapely.geometry import Point, box, mapping, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
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
}

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
    "Algeria": "Algerien", "Morocco": "Marokko", "Tunisia": "Tunesien", "Libya": "Libyen",
    "Egypt": "Ägypten", "Syria": "Syrien", "Iraq": "Irak", "Iran": "Iran",
    "Kazakhstan": "Kasachstan", "Jordan": "Jordanien", "Lebanon": "Libanon",
}

# Kuratierte Aliase (u. a. deutsche Namen) für die Suche
ALIASES = {
    "Vienna": ["Wien"], "Munich": ["München"], "Cologne": ["Köln"],
    "Zurich": ["Zürich"], "Geneva": ["Genf", "Genève"], "Bern": ["Bern"],
    "Basel": ["Basel"], "Rome": ["Rom", "Roma"], "Milan": ["Mailand", "Milano"],
    "Naples": ["Neapel", "Napoli"], "Turin": ["Turin", "Torino"], "Venice": ["Venedig", "Venezia"],
    "Florence": ["Florenz", "Firenze"], "Genoa": ["Genua", "Genova"], "Lyon": ["Lyon"],
    "Marseille": ["Marseille"], "Bordeaux": ["Bordeaux"], "Toulouse": ["Toulouse"],
    "Nice": ["Nizza", "Nice"], "Strasbourg": ["Straßburg", "Strasbourg"],
    "Ghent": ["Gent", "Gand"], "Antwerp": ["Antwerpen", "Anvers"], "Bruges": ["Brügge", "Brugge"],
    "Copenhagen": ["Kopenhagen", "København"], "Gothenburg": ["Göteborg"],
    "Stockholm": ["Stockholm"], "Helsinki": ["Helsinki", "Helsingfors"],
    "Warsaw": ["Warschau", "Warszawa"], "Krakow": ["Krakau", "Kraków"], "Prague": ["Prag", "Praha"],
    "Bucharest": ["Bukarest", "București"], "Budapest": ["Budapest"], "Belgrade": ["Belgrad", "Beograd"],
    "Zagreb": ["Zagreb", "Agram"], "Ljubljana": ["Laibach", "Ljubljana"], "Sofia": ["Sofia"],
    "Athens": ["Athen", "Athina"], "Thessaloniki": ["Thessaloniki", "Saloniki"],
    "Lisbon": ["Lissabon", "Lisboa"], "Porto": ["Porto"], "Madrid": ["Madrid"],
    "Barcelona": ["Barcelona"], "Seville": ["Sevilla"], "Valencia": ["Valencia"],
    "Bilbao": ["Bilbao"], "Malaga": ["Málaga"], "Moscow": ["Moskau", "Moskva"],
    "Saint Petersburg": ["Sankt Petersburg", "Sankt-Peterburg"], "Kiev": ["Kiew", "Kyiv"],
    "Kyiv": ["Kiew", "Kiev"], "Kharkiv": ["Charkiw", "Charkow"], "Odessa": ["Odessa", "Odesa"],
    "Minsk": ["Minsk"], "Riga": ["Riga"], "Vilnius": ["Wilna", "Vilnius"], "Tallinn": ["Reval", "Tallinn"],
    "Brussels": ["Brüssel", "Bruxelles"], "Istanbul": ["Istanbul"], "Ankara": ["Ankara"],
    "Dublin": ["Dublin"], "Edinburgh": ["Edinburgh"], "Glasgow": ["Glasgow"],
    "London": ["London"], "Manchester": ["Manchester"], "Birmingham": ["Birmingham"],
    "Oslo": ["Oslo"], "Bergen": ["Bergen"], "Reykjavik": ["Reykjavík"],
    "Amsterdam": ["Amsterdam"], "Rotterdam": ["Rotterdam"], "The Hague": ["Den Haag", "Haag"],
    "Cairo": ["Kairo"], "Tunis": ["Tunis"], "Algiers": ["Algier", "Alger"], "Casablanca": ["Casablanca"],
}


def norm(s: str) -> str:
    """Normalisiert Namen für die Suche (Kleinschreibung, ohne Diakritika)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    for a, b in (("ø", "o"), ("å", "a"), ("æ", "ae"), ("ß", "ss"), ("ł", "l"), ("đ", "d"), ("ð", "d")):
        s = s.replace(a, b)
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in s).split())


def round_coords(obj, ndigits=3):
    if isinstance(obj, (list, tuple)):
        return [round_coords(x, ndigits) for x in obj]
    if isinstance(obj, float):
        return round(obj, ndigits)
    return obj


def build_countries() -> None:
    src = RAW / "countries-world.geojson"
    geo = json.loads(src.read_text())
    feats = []
    for f in geo["features"]:
        name = (f.get("properties") or {}).get("name") or "?"
        geom = shape(f["geometry"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty or not geom.intersects(CLIP):
            continue
        geom = geom.intersection(CLIP).simplify(0.02, preserve_topology=True)
        if geom.is_empty:
            continue
        feats.append({
            "type": "Feature",
            "properties": {"name": name, "name_de": DE_NAMES.get(name, name)},
            "geometry": {"type": geom.geom_type, "coordinates": round_coords(mapping(geom)["coordinates"])},
        })
    out = {"type": "FeatureCollection", "features": feats}
    (OUT / "countries.geojson").write_text(json.dumps(out, separators=(",", ":")))
    size = (OUT / "countries.geojson").stat().st_size
    print(f"countries.geojson: {len(feats)} Länder, {size/1024:.0f} kB")


def build_cities(min_pop: int = 25000) -> None:
    world = json.loads((RAW / "countries-world.geojson").read_text())
    geoms, names = [], []
    for f in world["features"]:
        name = (f.get("properties") or {}).get("name")
        if name not in EUROPE_COUNTRIES:
            continue
        g = shape(f["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        geoms.append(g)
        names.append(name)
    tree = STRtree(geoms)

    cities = []
    with open(RAW / "geonames_cities15000.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            if not (32.0 < lat < 73.0 and -27.0 < lon < 47.0):
                continue
            try:
                pop = int(row["population"])
            except (TypeError, ValueError):
                pop = 0
            if pop < min_pop:
                continue
            name = (row["name"] or "").strip()
            if not name:
                continue
            p = Point(lon, lat)
            idx = tree.query(p)
            country = None
            for i in idx:
                if geoms[i].contains(p):
                    country = names[i]
                    break
            if country is None:
                continue
            cities.append({
                "name": name,
                "country": country,
                "name_de": DE_NAMES.get(country, country),
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "pop": pop,
                "aliases": ALIASES.get(name, []),
                "search": norm(" ".join([name] + ALIASES.get(name, []))),
            })

    # Duplikate (gleicher Name, sehr nah beieinander) entfernen
    cities.sort(key=lambda c: -c["pop"])
    kept = []
    for c in cities:
        if any(abs(c["lat"] - k["lat"]) < 0.35 and abs(c["lon"] - k["lon"]) < 0.5
               and k["name"].lower() == c["name"].lower() for k in kept):
            continue
        kept.append(c)
    (OUT / "cities.json").write_text(json.dumps(kept, separators=(",", ":"), ensure_ascii=False))
    size = (OUT / "cities.json").stat().st_size
    print(f"cities.json: {len(kept)} Städte, {size/1024:.0f} kB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_countries()
    build_cities()
