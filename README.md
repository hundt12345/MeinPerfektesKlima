# Mein perfektes Klima – Europa

Interaktive Europakarte nach dem Vorbild von *myperfectweather.com*: Du stellst dein
Wohlfühlwetter ein (Höchst- und Tiefsttemperatur, Taupunkt, Sonnenscheindauer,
optional Niederschlag) und siehst sofort, **wie viele Tage pro Jahr** dieses Wetter
wo erreicht wird – von Blau (wenige Tage) bis Rot (viele Tage).

Zwei Modi:

1. **Ganzes Jahr** – „Wo ist es übers Jahr gerechnet am häufigsten genau so, wie ich es mag?“
2. **Urlaubsmonat** – „Ich will im Juli Urlaub machen: Wo gibt es dann die meisten Tage mit
   meinem Wunschwetter?“

Dazu:

* **Klick auf die Karte oder auf eine Stadt** öffnet eine Detailkarte mit den Wohlfühl-Tagen
  je Monat, der Einordnung („besser als 87 % aller Rasterpunkte“, Bestwert in Europa) und dem
  Klimaprofil des Ortes (Ø min/max, Taupunkt, Sonnenstunden, Niederschlag, Sommer-/Frosttage).
* **Ortssuche** über der Karte (Name tippen, z. B. „Sev…“), Top-Orte-Liste für die aktuelle
  Einstellung, teilbare Links (alle Parameter stecken im URL-Hash).
* **Grundkarte ohne Fremd-Kacheln**: Länderflächen und Grenzen werden aus Natural Earth selbst
  gezeichnet – dadurch gibt es kein Kachel-Watermark und keinen Attributionsbalken im Kartenbild
  (die Quellenangaben stehen im Fußbereich der Seite).

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

## Datenqualität: das Qualitätstor

Früher konnte ein kaputter oder synthetischer Datensatz unbemerkt auf der Website landen
(erkennbar an unplausiblen Werten, z. B. „Südspanien kälter im Juli als im Januar“).
Deshalb prüft `scripts/check_grid.py` nach jedem Datenlauf automatisch gegen
**Klimanormalwerte von 22 europäischen Städten** (Monatsmittel Jan/Jul für Höchst- und
Tiefsttemperatur, Sonnenscheindauer, Jahresniederschlag) sowie gegen strukturelle
Erwartungen (Jahresgang, Wertebereiche, Vollständigkeit). Das Ergebnis steht in
`app/data/quality_report.json`; schlägt das Tor fehl, wird der Action-Lauf rot **und**
die Website blendet einen deutlichen Warnhinweis ein, statt falsche Zahlen stillschweigend
zu zeigen. Fehlende Tage eines abgebrochenen Abrufs werden beim Zusammenbauen aus
Nachbarpunkten ergänzt und als `coverage` in den Metadaten ausgewiesen.

## Daten

| Was | Woher | Auflösung / Umfang |
|---|---|---|
| Temperatur, Taupunkt, Sonnenschein, Niederschlag | **ERA5**-Reanalyse (Copernicus/ECMWF) über die [Open-Meteo](https://open-meteo.com/)-Historical-API | täglich, 2016–2025, Raster 0,75° über 34°–72° N / 25° W–45° O (~2.250 Landpunkte) |
| Landmaske fürs Ausblenden der Meeresflächen | [global-land-mask](https://pypi.org/project/global-land-mask/) (Natural Earth/OSM-basiert) | 0,05° |
| Ländergrenzen (Grundkarte) | [world-atlas](https://www.npmjs.com/package/world-atlas) (Natural Earth 1:10m) | Vektor, selbst gerendert |
| Städteliste für Suche/Marker/Top-Orte | [GeoNames cities15000](https://www.geonames.org/) | 4.099 Orte ≥ 25.000 Einwohner in Europa |

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
  main.py            FastAPI: Seiten + JSON-/PNG-Endpunkte, Vorwärmen im Hintergrund
  climate.py         Datenzugriff, Tageszählung, Web-Mercator-Rendering (PIL/scipy)
  data/              Datensätze (Klimaraster, Ländergrenzen, Städte, Qualitätsbericht)
  static/            Startseite (index.html), Styles, Leaflet (vendored)
scripts/
  fetch_climate_grid.py   ERA5-Daten über Open-Meteo holen + quantisieren  (braucht Internet)
  make_dev_grid.py        plausibles synthetisches Raster für Offline-Entwicklung/Demo
  check_grid.py           Qualitätstor: Vergleich mit Klimanormalwerten (22 Städte)
  build_geo.py            Ländergrenzen + Städteliste bauen
  download_sources.sh     Rohquellen (world-atlas, Leaflet, GeoNames) laden
.github/workflows/
  build-climate-data.yml  führt fetch_climate_grid.py + check_grid.py auf einem
                          GitHub-Runner aus und committet das Ergebnis zurück
render.yaml          Render-Blueprint (Web-Service, Free-Plan)
```

### Rendering-Performance

Das Karten-PNG wird serverseitig in Web-Mercator erzeugt. Der teure Teil (die
IDW-Nachbarsuche der ~2.250 Rasterpunkte auf das Pixelgitter) hängt nur von der
Bildbreite ab, nicht von den Filterparametern – er wird pro Breite zwischengespeichert.
Das Feld selbst wird auf 640 px Breite interpoliert und erst danach bilinear skaliert
(die Landmaske bleibt in voller Auflösung scharf). Dadurch kostet eine
Parameteränderung serverseitig ~0,1–0,3 s statt ~1 s – wichtig auf kleinen Instanzen.

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
   Der Lauf dauert ca. 2–4 Stunden und committet
   `app/data/climate_grid.npz` + `climate_meta.json` + `quality_report.json` zurück.
   Erreicht er sein Zeitbudget (`max_minutes`, Vorgabe 285), beendet er sich
   **geordnet** vor dem GitHub-Job-Timeout; die Kontrollpunkte
   (`data/checkpoints/`) bleiben liegen und der nächste Lauf macht dort weiter.
   Also: einfach erneut *Run workflow* klicken, bis das Protokoll „Vollständig“ meldet.
3. Render deployt den neuen Commit automatisch.

Hinweise zum Lauf:

* **Fortschritt beobachten:** alle 10 Minuten entsteht ein Commit
  „Klimadaten: Zwischenstand …“, der erste wird nach ~45 Minuten veröffentlicht.
  Daran sieht man von außen, wie viele Blöcke geholt sind und ob gedrosselt wird.
* **Drosselung (HTTP 429):** Open-Meteo begrenzt die *Antwortgröße*, nicht die
  Aufrufzahl. Deshalb halbiert das Skript bei wiederholter Drosselung die Anfrage
  (erst den Zeitraum, dann die Punktzahl) und setzt die Teile wieder zusammen; die
  Taktung sinkt nur mild und erholt sich nach erfolgreichen Anfragen wieder.
  Eine einzelne Anfrage wartet nie länger als `max_wait_per_request` Sekunden –
  ein Lauf kann dadurch nicht mehr „einfrieren“.
* **Nichts zu tun:** Liegt schon ein vollständiger echter Datensatz
  (Abdeckung ≥ 99,9 %) und kein Kontrollpunkt mehr im Repo, überspringt der Lauf
  den Abruf. Wer trotzdem neu aufbauen will, nutzt den Workflow-Input
  `reset: true` (Kontrollpunkte verwerfen) bzw. lokal `--force`.

## Lokal starten

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# -> http://localhost:8000
```

Ohne Internetzugang (oder vor der ersten Datenernte) kann ein **plausibles** synthetisches
Testraster erzeugt werden – es zeigt Mechanik und realistische Klimamuster
(Abweichung zu Normalwerten ~1–2 K), ist aber klar als Testdaten gekennzeichnet und
ersetzt keine ERA5-Werte:

```bash
pip install -r requirements-dev.txt
python scripts/build_geo.py         # Länder + Städte (nur beim ersten Mal)
python scripts/make_dev_grid.py     # synthetisches Raster nach app/data
python scripts/check_grid.py        # Qualitätstor über dem lokalen Raster
```

Mit `KLIMA_DATA_DIR=/pfad/zum/raster uvicorn …` lässt sich die App gegen ein beliebiges
Datenverzeichnis starten (nutzen auch die Tests, siehe `tests/conftest.py`).

## Deployment auf Render

**Variante A – Blueprint (empfohlen):** In Render *New → Blueprint* wählen, dieses Repo
verbinden. Render liest `render.yaml` und legt den Service an:

* Build: `pip install -r requirements.txt`
* Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
* Health check: `/healthz`

**Variante B – manuell:** *New → Web Service*, Repo verbinden, Runtime *Python 3*,
Build- und Startkommando wie oben, Region Frankfurt.

### Reicht der Free-Plan?

**Ja, für den privaten/probierenden Betrieb.** Eckdaten:

* Speicher: Der Dienst liegt im Betrieb bei ~250–300 MB RAM (Datensatz 41 MB entpackt +
  Render-Caches) – die Free-Instanz hat 512 MB, das passt mit Reserve.
* CPU: Eine Kartenberechnung dauert serverseitig ~0,1–0,3 s (IDW-Gewichte gecacht);
  auch auf der geteilten Free-CPU bleibt die Karte dadurch flüssig.
* Der Datensatz (~26 MB komprimiert) liegt im Repo, Render nimmt ihn beim Deploy mit;
  die App lädt ihn beim Start im Hintergrund vor, der erste Nutzer wartet also nicht
  auf den Daten-Load.
* **Einziger echter Nachteil:** Free-Instanzen schlafen nach Inaktivität ein. Der erste
  Aufruf nach einer Pause dauert ~10–50 s (Cold Start), danach ist alles schnell.
  Für Dauerbetrieb oder mehrere gleichzeitige Nutzer: in `render.yaml`
  `plan: starter` setzen.

Hinweise:

* Der **Free-Plan** ist in `render.yaml` voreingestellt; `starter` kostet ~7 $/Monat und
  verhindert den Sleep.
* Render zieht standardmäßig den Branch `main` – den Pull Request also vorher mergen.

## API

| Endpunkt | Zweck |
|---|---|
| `GET /api/meta` | Datensatz-Infos, Standardwerte, Presets, Rasterpunkte, Qualitätsstatus |
| `GET /api/map` | Statistik (min./max. Tage, Legendenwerte) + Rasterwerte |
| `GET /api/map.png` | Kartenbild (Web-Mercator-PNG, transparent über See) |
| `GET /api/point?lat=&lon=` | Auswertung eines Ortes: Wohlfühl-Tage, Monatsprofil, Klimaprofil, nächster Ort, Einordnung |
| `GET /api/ranking` | beste Städte für die aktuellen Parameter |
| `GET /api/cities/map` | größte Orte mit Tageszahl (für anklickbare Marker) |
| `GET /api/cities/search?q=` | Ortssuche (schlankes JSON) |
| `GET /api/cities`, `GET /api/countries` | statische Geo-Daten |

Alle Karten-Endpunkte verstehen dieselben Parameter:
`tmax_min, tmax_max, tmin_min, tmin_max, tdew_max, sun_min, prec_max, month (1–12|all), width, fixed`.

## Tests

```bash
pytest tests                 # Zähl-Kern, Rendering, API (mit generiertem Testraster)
node tests/ui_smoketest.mjs  # Oberfläche in jsdom gegen eine laufende Instanz
```

Der UI-Smoketest prüft u. a., dass **keine** Kacheln von Drittanbietern geladen werden
(kein Watermark), dass Länderflächen selbst gezeichnet werden und dass Klicks auf Karte,
Städtemarker und Top-Orte die Detailkarte mit Klimaprofil öffnen.
