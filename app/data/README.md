# Datenordner

| Datei | Inhalt | Erzeugt von |
|---|---|---|
| `climate_grid.npz` | tägliche ERA5-Werte 2016–2025 für ~2.250 Landpunkte (int8, quantisiert) | `scripts/fetch_climate_grid.py` (GitHub-Action „Klimadaten bauen“) |
| `climate_meta.json` | Zeitraum, Skalierungsfaktoren, Raster-Geometrie, Quelle | dito |
| `countries.geojson` | vereinfachte Ländergrenzen für den Kartenhintergrund | `scripts/build_geo.py` |
| `cities.json` | 4.099 Städte (Name, Land, Koordinaten, Einwohner, Suchaliase) | `scripts/build_geo.py` |

`climate_grid.npz` und `climate_meta.json` werden **absichtlich** im Repo gehalten:
Render deployt damit ohne externe API-Aufrufe. Zum Neubau siehe README im Projektwurzelverzeichnis.

Für die Offline-Entwicklung kann `scripts/make_dev_grid.py` ein synthetisches Raster
erzeugen (im Kopf der Datei als solches markiert) – niemals für Veröffentlichungen nutzen.
