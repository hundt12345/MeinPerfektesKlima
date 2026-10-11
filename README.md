# Mein perfektes Klima – Europa

Interaktive Europakarte nach dem Vorbild von *myperfectweather.com*: Du stellst dein
Wohlfühlwetter ein (Höchst- und Tiefsttemperatur, Taupunkt, Sonnenscheindauer,
optional Niederschlag) und siehst sofort, **wie viele Tage pro Jahr** dieses Wetter
wo erreicht werden – von Blau (wenige Tage) bis Rot (viele Tage).

Zwei Modi:

1. **Ganzes Jahr** – „Wo ist es übers Jahr gerechnet am häufigsten genau so, wie ich es mag?“
2. **Bestimmter Monat** – „Ich will im Juli Urlaub machen: Wo gibt es dann die meisten Tage mit
   meinem Wunschwetter?“

Dazu: Klick auf einen Punkt liefert ein Monatsprofil, eine Top-Orte-Liste für die
aktuellen Einstellungen und teilbare Links (alle Parameter stecken im URL-Hash).

![Beispielkarte](docs/beispiel-karte.png)

## Wie funktioniert die Auswertung?

Für jeden Rasterpunkt liegen **tägliche** Werte von 2016–2025 vor. Für jeden Punkt
und jeden Tag wird geprüft, ob *alle* Kriterien gleichzeitig erfüllt sind:

| Kriterium | Bedeutung |
|---|---|
| `tmax_min` … `tmax_max` | Tageshöchsttemperatur im Wunschbereich |
| `tmin_min` … `tmin_max` | Tagestiefsttemperatur im Wunschbereich |
| `tdew_max` | mittlerer Taupunkt an diesem Tag ≤ Grenzwert („schwül“-Filter) |
| `sun_min` | Sonnenscheindauer ≥ Grenzwert (Stunden/Tag) |
| `prec_max` | Niederschlagssumme ≤ Grenzwert (optional) |

Die Karte zeigt die mittlere Anzahl erfüllter Tage pro Jahr (bzw. im gewählten Monat),
interpoliert zwischen den Rasterpunkten. Die Farbwerte selbst sind die exakten
Rasterpunkt-Zählungen – Hover und Klick zeigen sie ungeglättet an.

## Daten

| Was | Woher | Auflösung / Umfang |
|---|---|---|
| Temperatur, Taupunkt, Sonnenschein, Niederschlag | **ERA5**-Reanalyse (Copernicus/ECMWF) über die [Open-Meteo](https://open-meteo.com/)-Historical-API | täglich, 2016–2025, Raster 0,75° über 34°–72° N / 25° W–45° O (~2.250 Landpunkte) |
| Landmaske fürs Ausblenden der Meeresflächen | [global-land-mask](https://pypi.org/project/global-land-mask/) (Natural Earth/OSM-basiert) | 0,05° |
| Ländergrenzen | [world-atlas](https://www.npmjs.com/package/world-atlas) (Natural Earth 1:10m) | Vektor |
| Städteliste für Suche/Top-Orte | [GeoNames cities15000](https://www.geonames.org/) | 4.099 Orte ≥ 25.000 Einwohner in Europa |
| Kartenhintergrund | CARTO/OpenStreetMap-Kacheln, Leaflet 1.9 | Web-Mercator |

Lizenzen/Attribution: ERA5-Daten © Copernicus/ECMWF, bereitgestellt über Open-Meteo
(CC-BY-4.0; die Open-Meteo-API selbst CC-BY-NC-4.0 – für eine kommerzielle Nutzung
bitte eigene Hosting-/API-Lizenz klären). Natural Earth ist gemeinfrei, GeoNames
CC-BY-4.0, Leaflet BSD-2.

**Grenzen der Aussage:** ERA5 ist ein Reanalyse-Modell (≈0,25°), kein Stationsmesswert.
In Gebirgen und an Küsten sind Abweichungen von mehreren Grad möglich; der Datensatz
ist zusätzlich auf 0,75° ausgedünnt und Werte sind auf 0,5 °C, 0,25 h bzw. 0,5 mm
gerundet. „Ein Jahr“ ist das Mittel über 10 Jahre, extreme Einzeljahre werden also
geglättet.

## Architektur

```
app/
  main.py            FastAPI: Seiten + JSON-/PNG-Endpunkte
  climate.py         Datenzugriff, Tageszählung, Web-Mercator-Rendering (PIL/scipy)
  data/              Datensätze (Klimaraster, Ländergrenzen, Städte)
  static/            Startseite (index.html), Styles, Leaflet (vendored)
scripts/
  fetch_climate_grid.py   ERA5-Daten über Open-Meteo holen + quantisieren  (braucht Internet)
  make_dev_grid.py        synthetisches Testraster für die Offline-Entwicklung
  build_geo.py            Ländergrenzen + Städteliste bauen
  download_sources.sh     Rohquellen (world-atlas, Leaflet, GeoNames) laden
.github/workflows/
  build-climate-data.yml  führt fetch_climate_grid.py auf einem GitHub-Runner aus
                          und committet app/data/climate_grid.npz zurück
render.yaml          Render-Blueprint (Web-Service)
```

### Warum ein separater Daten-Workflow?

Der Datenabruf braucht offenen Internetzugang zu `archive-api.open-meteo.com`.
Sandboxes/CI-Umgebungen ohne diesen Zugang können das Raster nicht erzeugen – der
GitHub-Action-Runner schon. Deshalb:

* **Datenernte** = GitHub Action (einmalig bzw. zur Aktualisierung),
* **Web-App** = liest nur die fertige `app/data/climate_grid.npz` (kein Netzzugriff zur Laufzeit,
  keine API-Limits im Betrieb).

## Daten aktualisieren

1. `scripts/grid_config.json` anpassen (Zeitraum/Ausschnitt/Auflösung;
   `"test_mode": false` = voller Lauf, `true` = winziges Testraster).
2. GitHub → *Actions* → **Klimadaten bauen** → *Run workflow*.
   Der Lauf dauert ca. 10–25 Minuten und committet
   `app/data/climate_grid.npz` + `climate_meta.json` zurück in den Branch.
3. Render deployt den neuen Commit automatisch.

## Lokal starten

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000
```

Ohne Internetzugang (oder vor der ersten Datenernte) kann ein synthetisches
Testraster erzeugt werden – es zeigt nur die Mechanik, keine echten Klimawerte:

```bash
pip install global-land-mask        # nur nötig für die Skripte
python scripts/build_geo.py         # Länder + Städte
python scripts/make_dev_grid.py     # synthetisches Raster (klar als solches markiert)
```

## Deployment auf Render

**Variante A – Blueprint (empfohlen):** In Render *New → Blueprint* wählen, dieses Repo
verbinden. Render liest `render.yaml` und legt den Service an:

* Build: `pip install -r requirements.txt`
* Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
* Health check: `/healthz`

**Variante B – manuell:** *New → Web Service*, Repo verbinden, Runtime *Python 3*,
Build- und Startkommando wie oben, Region Frankfurt.

Hinweise:

* Der Datensatz (~30 MB) liegt im Repo, damit Render ihn beim Deploy ohne API-Aufrufe mitnimmt.
  Der Dienst lädt die Daten erst bei der ersten Anfrage (Lazy Load), das dauert ~2–4 s.
* Der **Free-Plan** reicht zum Ausprobieren: 512 MB RAM sind ausreichend, der Dienst
  schläft aber nach Inaktivität ein (erster Aufruf danach ~50 s). Für Dauerbetrieb
  `plan: starter` in `render.yaml` setzen.
* Render zieht standardmäßig den Branch `main` – den Pull Request also vorher mergen.

## API

| Endpunkt | Zweck |
|---|---|
| `GET /api/meta` | Datensatz-Infos, Standardwerte, Presets, Rasterpunkte (für Hover) |
| `GET /api/map` | Statistik (min./max. Tage, Legendenwerte) + Rasterwerte |
| `GET /api/map.png` | Kartenbild (Web-Mercator-PNG, transparent über See) |
| `GET /api/point?lat=&lon=` | Monatsprofil & Jahreswert für einen Ort |
| `GET /api/ranking` | beste Städte für die aktuellen Parameter |
| `GET /api/cities`, `GET /api/countries` | statische Geo-Daten |

Alle Karten-Endpunkte verstehen dieselben Parameter:
`tmax_min, tmax_max, tmin_min, tmin_max, tdew_max, sun_min, prec_max, month (1–12|all), width, fixed`.
