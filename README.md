# Mein perfektes Klima – Europa

Interaktive Europakarte nach dem Vorbild von *myperfectweather.com*: Du stellst dein
Wohlfühlwetter ein (Höchst- und Tiefsttemperatur, Taupunkt, Sonnenscheindauer,
optional Niederschlag) und siehst sofort, **wie viele Tage pro Jahr** dieses Wetter
wo erreicht wird – von Blau (wenige Tage) bis Rot (viele Tage).

Drei Modi:

1. **Ganzes Jahr** – „Wo ist es übers Jahr gerechnet am häufigsten genau so, wie ich es mag?“
2. **Urlaubsmonat** – „Ich will im Juli Urlaub machen: Wo gibt es dann die meisten Tage mit
   meinem Wunschwetter?“ (Die Monatsauswahl erscheint nur in diesem Modus.)
3. **Winterquartier** – „Welcher Ort trägt mich das ganze Jahr?“ bzw. „Ich habe mein Sommerquartier –
   wo ist das passende Winterquartier?“ (siehe [unten](#der-winterquartier-modus))

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

Der Monatsfilter kennt außerdem **Zeiträume**: `month=5-9` (Mai – September) oder
`month=10-3` (Oktober – März, über den Jahreswechsel). Damit lassen sich ganze
Jahreszeiten auf die Karte legen – die Zahlen sind weiterhin exakte Zählungen,
denn die Monatszählungen sind additiv: „Tage im Mai – September“ = Summe der
Tageszählungen dieser fünf Monate.

## Der Winterquartier-Modus

Die Urlaubsfrage „zwei Wochen im Juli“ ist eine andere als die Frage nach einem
**Winterquartier**. Dafür gibt es den dritten Modus: Er stellt zwei Wetterprofile
gegenüber – ein **Sommer**-Profil (die normalen Regler) und ein **Winterquartier**-Profil
(eigene Regler, z. B. 14–26 °C, Taupunkt ≤ 17 °C, 4 h Sonne) – und verteilt das Jahr
in ein **Sommerfenster** und die übrigen Monate. Gezählt wird jeder Tag nur einmal,
die Fenster überschneiden sich nicht. Die Karte kennt drei Ansichten:

| Ansicht | Was sie zeigt |
|---|---|
| **Kombination** | Sommer-Tage + Winterquartier-Tage – die Kennzahl für „ein Ort fürs ganze Jahr“ |
| **Sommer** | nur die Wohlfühl-Tage im Sommerfenster |
| **Winter** | nur die Wohlfühl-Tage in den Wintermonaten |

Zwei Fragen lassen sich damit beantworten:

1. **Ein Ort, optimal aufgeteilt** – „Optimale Kombination finden“ probiert alle
   zusammenhängenden Aufteilungen des Jahres durch (Sommerfenster mit 3–9 Monaten,
   über den Jahreswechsel erlaubt) und meldet Aufteilung **und** Ort mit der höchsten
   Jahressumme, dazu die besten Städte und Alternativ-Aufteilungen zum Anklicken.
   Rechenweg: Für jeden Monat liegt die Tageszahl beider Profile vor; die Summe eines
   Fensters ist damit eine Präfixsumme über zwölf Monatsvektoren – deshalb kostet die
   Optimierung ~0,1 s statt einer Zählung je Aufteilung.
2. **Zwei Orte („Gegenstück finden“)** – du wählst auf der Karte dein Quartier und
   stellst ein, ob es dein **Sommer**- oder **Winter**quartier ist. Die Liste zeigt
   dann die besten Gegenstücke für die andere Jahreshälfte: eigene Tageszahl, Tageszahl
   dort, **Summe beider Quartiere**, Entfernung (optional auf einen Radius begrenzt)
   und die für das Paar beste Monatsaufteilung. So wird aus „Winter in Lissabon“
   schnell „Sommer in Cagliari, Winter in Almería → 253 Wohlfühl-Tage im Jahr“.

Grenzen: Ein „Winterquartier“ wird hier genauso streng gemessen wie ein Urlaubsort –
gezählt werden Tage, an denen *alle* Kriterien des jeweiligen Profils gleichzeitig
erfüllt sind (inklusive Sonnenschein und Taupunkt). ERA5-Mittel über zehn Jahre
glätten einzelne Extremjahre; die Aufteilung ist eine Optimierung der Tageszahlen,
keine Empfehlung zu Miete, Visum oder Anreise.

## Datenqualität: das Qualitätstor

Früher konnte ein kaputter oder synthetischer Datensatz unbemerkt auf der Website landen
(erkennbar an unplausiblen Werten, z. B. „Südspanien kälter im Juli als im Januar“).
Deshalb prüft `scripts/check_grid.py` nach jedem Datenlauf automatisch gegen
**Klimanormalwerte von 22 europäischen Städten** (Monatsmittel Jan/Jul für Höchst- und
Tiefsttemperatur, Sonnenscheindauer, Jahresniederschlag) sowie gegen strukturelle
Erwartungen (Jahresgang, Wertebereiche, Vollständigkeit). Das Ergebnis steht in
`app/data/quality_report.json`; schlägt das Tor fehl, wird der Action-Lauf rot.

Seit dem Umbau ist das Tor **hart**, nicht mehr nur ein Warnbanner: Bestehen die
bereitgestellten Daten die Prüfung nicht (oder fehlen sie ganz), antworten alle
Karten-Endpunkte mit **HTTP 503** und die Startseite zeigt statt einer Karte mit
falschen Zahlen den **Baufortschritt** des Datenlaufs (Balken, Phase, ETA, Logzeilen).
Die Website zeigt also entweder geprüfte echte ERA5-Daten oder gar keine.
Für Entwicklung/Demos lässt sich das Tor mit der Umgebungsvariable
`KLIMA_ALLOW_UNVERIFIED=1` öffnen – die Kennzeichnung `synthetic: true` bleibt dann
sichtbar.

Die physikalischen Wertegrenzen des Tors hängen am Ausschnitt: Dieses Raster reicht
von Island (72° N) bis in den Ostirak (34° N / 45° O). Gemessen wurden dort
**−43 °C** (Kältewelle bei Archangelsk, 6. Januar 2017) und **+52 °C** (Hitzewelle
im irakisch-iranischen Grenzgebiet, 28. Juli 2020) – beides reale Extreme, die von
den ursprünglich für „Mitteleuropa“ gesetzten Grenzen (−35/+48 °C) zu Unrecht als
Datenfehler gewertet wurden. Deshalb gelten jetzt −50…+55 °C (Höchstwert) bzw.
−60…+40 °C (Tiefstwert), **zusätzlich** geprüft wird, ob der Hitzerekord im Süden
im Sommer und der Kälterekord im Norden im Winter liegt – ein Einheitendreher
(Kelvin statt Celsius) oder eine vertauschte Skala fällt damit weiterhin sofort auf.
Jeder Bericht trägt außerdem den Fingerabdruck des geprüften Datensatzes
(`generated_at`, Punkte, Tage, Zeitraum); passt er nicht zum geladenen Raster, gilt
sein Urteil nicht (siehe `report_matches_dataset` in `app/main.py`). Fehlende Tage eines abgebrochenen Abrufs werden beim Zusammenbauen aus
Nachbarpunkten ergänzt und als `coverage` in den Metadaten ausgewiesen.

## Daten

| Was | Woher | Auflösung / Umfang |
|---|---|---|
| Temperatur, Taupunkt, Sonnenschein, Niederschlag | **ERA5**-Reanalyse (Copernicus/ECMWF), offene Kopie [ARCO ERA5](https://github.com/google-research/arco-era5) auf Google Cloud (`gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3`) | stündlich gelesen → täglich abgeleitet, 2016–2025, Raster 0,75° über 34°–72° N / 25° W–45° O (~2.250 Landpunkte) |
| Landmaske fürs Ausblenden der Meeresflächen | [global-land-mask](https://pypi.org/project/global-land-mask/) (Natural Earth/OSM-basiert) | 0,05° |
| Ländergrenzen (Grundkarte) | [world-atlas](https://www.npmjs.com/package/world-atlas) (Natural Earth 1:10m) | Vektor, selbst gerendert |
| Städteliste für Suche/Marker/Top-Orte | [GeoNames cities15000](https://www.geonames.org/) | 4.099 Orte ≥ 25.000 Einwohner in Europa |

Lizenzen/Attribution: ERA5-Daten enthalten modifizierte Copernicus-Klimadaten
(2026); bereitgestellt von ECMWF, gespiegelt als offene, analysis-ready Kopie durch
Google Research (Copernicus-Lizenz, keine API-Nutzungsbedingungen, kein Tageskontingent,
kein Schlüssel nötig). Natural Earth ist gemeinfrei, GeoNames CC-BY-4.0, Leaflet BSD-2.
Der frühere Weg über die Open-Meteo-API entfällt – siehe
[„Warum nicht die Open-Meteo-API?“](#warum-nicht-die-open-meteo-api).

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
  fetch_era5_zarr.py      ERA5 aus offenen Zarr-Daten lesen, Tageswerte ableiten   (Standard)
  build_progress.py       Baufortschritt schreiben + auf Zweig „klima-fortschritt" veröffentlichen
  fetch_climate_grid.py   alternativer Abruf über die Open-Meteo-API (nur mit Kontingent)
  make_dev_grid.py        plausibles synthetisches Raster für Offline-Entwicklung/Demo
  check_grid.py           Qualitätstor: Vergleich mit Klimanormalwerten (22 Städte)
  build_geo.py            Ländergrenzen + Städteliste bauen
  download_sources.sh     Rohquellen (world-atlas, Leaflet, GeoNames) laden
app/build_progress.py     liest den Fortschritt (Datei/GitHub) und bewertet ihn für die UI
.github/workflows/
  build-climate-data.yml  führt fetch_era5_zarr.py + check_grid.py auf einem
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

Der Datenabruf braucht offenen Internetzugang (hier: anonym zu
`storage.googleapis.com`) und liest rund 700 GB Stundenwerte – das ist auf einer
Free-Web-Instanz weder möglich noch sinnvoll (kein persistentes Datenvolumen,
6-Stunden-Lauf beim Deploy, CPU-Kontingent). Deshalb:

* **Datenernte** = GitHub Action (einmalig bzw. zur Aktualisierung), ~2–3 h,
  mit Kontrollpunkten je Jahr und live veröffentlichtem Fortschritt,
* **Web-App** = liest nur die fertige `app/data/climate_grid.npz` (kein Netzzugriff zur Laufzeit,
  keine API-Limits im Betrieb).

Die Fortschrittsdatei wird während des Laufs auf den Zweig `klima-fortschritt`
geschrieben (nicht auf `main`, damit Render nicht pro Meldung neu deployt); die App
liest sie über `raw.githubusercontent.com` und zeigt daraus den Balken.

## Daten aktualisieren

1. `scripts/grid_config.json` anpassen (Zeitraum/Ausschnitt/Auflösung;
   `"test_mode": false` = voller Lauf, `true` = winziges Testraster).
2. GitHub → *Actions* → **Klimadaten bauen** → *Run workflow*
   (Eingaben: `quelle` = `zarr`, `max_minutes` = 290, `jahre`/`sonnenschwelle` optional,
   `reset` = `true` verwirft Kontrollpunkte).
   Der Lauf dauert ca. 2–3 Stunden und committet
   `app/data/climate_grid.npz` + `climate_meta.json` + `quality_report.json` zurück.
   Erreicht er sein Zeitbudget (`max_minutes`, Vorgabe 290 – der GitHub-Job bricht
   erst bei 350 Minuten hart ab), beendet er sich **geordnet**; die Kontrollpunkte
   (`data/checkpoints/zarr-…/y<jahr>.npz`) bleiben liegen und der nächste Lauf macht
   dort weiter. Also: erneut *Run workflow* klicken, bis das Protokoll „Vollständig“ meldet.
3. Render deployt den neuen Commit automatisch.

Der Lauf startet auch von selbst, wenn sich an `scripts/fetch_era5_zarr.py`,
`scripts/grid_config.json` oder am Workflow etwas ändert (Push auf `main`).

Hinweise zum Lauf:

* **Fortschritt beobachten:** Die Startseite zeigt während des Laufs einen Balken
  (Phase, Jahre, Restzeit, letzte Protokollzeilen) – sie aktualisiert sich alle 15 s
  selbst und lädt neu, sobald die Daten geprüft vorliegen. Zusätzlich entsteht alle
  10 Minuten ein Commit „Klimadaten: Zwischenstand …“ mit Protokoll und Kontrollpunkten,
  falls der Runner hart abbricht.
* **Keine Drosselung:** Die Zarr-Quelle kennt kein Aufrufkontingent; das Zeitbudget
  ist die einzige Grenze. (Nur der Fallback `quelle: open-meteo` teilt Anfragen bei
  HTTP 429 weiterhin auf und braucht dafür ~38 Tage – siehe
  [„Warum nicht die Open-Meteo-API?“](#warum-nicht-die-open-meteo-api).)
* **Nichts zu tun:** Liegt bei einem Push-Auslöser schon ein vollständiger, geprüfter
  ERA5-Datensatz, überspringt der Lauf den Abruf. Manuell gestartet (`workflow_dispatch`)
  läuft er immer; mit `reset: true` (bzw. lokal `--force`) wird komplett neu aufgebaut.

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

### Warum nicht die Open-Meteo-API?

Der erste Datenlauf über die Open-Meteo-Archive-API ist am **freien Kontingent**
gescheitert, und zwar grundsätzlich – nicht an einer zu hohen Rate:

| Messwert | Wert |
|---|---|
| Freie Aufrufe je IP | 600/min, 5.000/h, **10.000/Tag**, 300.000/Monat |
| Zählweise | Ein Aufruf zählt mehrfach, wenn > 2 Wochen Zeitraum oder > 10 Variablen je Ort |
| Bedarf für dieses Raster | 2.250 Landpunkte × 3.653 Tage ≈ **290.000 gewichtete Aufrufe** |
| Folge | ~30 Läufe à 10.000 – und GitHub-Runner teilen sich IPs, das Kontingent ist oft schon vor dem ersten Aufruf weg (HTTP 429) |
| Messung im Runner | 1 Block (12 Punkte × 10 Jahre) = 200 OK in 4,8 s; ganzer Lauf ≈ 5 Blöcke/Tag ≈ 38 Tage |
| Kostenpflichtige Alternative | Professional-Tarif 99 $/Monat (Historical + Climate API inklusive) |

Belege: `data/quellen-probe.txt`, `data/quellen-probe-tageswerte.txt`, `build-log.txt`.

Der jetzige Weg liest **dieselben ERA5-Werte** aus der offenen, analysis-ready Kopie
von Google Research (ARCO): anonym, ohne Schlüssel, ohne Tageskontingent. Kosten
entstehen dabei nur als ausgehender Traffic des Runners; die Aktion dauert statt
38 Tagen etwa 2–3 Stunden. Der Nachteil: Es sind Stundenwerte (≈700 GB Lesevolumen),
aus denen Tageshöchst-/tiefstwerte, Taupunktmittel, Niederschlagssumme und
Sonnenscheindauer (Stunden ≥ 120 W/m² Direktstrahlung) erst abgeleitet werden.
Die Open-Meteo-Variante bleibt als `quelle: open-meteo` im Workflow erhalten, falls
jemand später einen API-Schlüssel hat.

### Reicht der Free-Plan?

**Für den Betrieb der Website: ja.** Für das **Bauen** der Daten: nein – das
übernimmt bewusst die GitHub Action (siehe oben), Render free hat dafür weder
ausreichend CPU-Stunden noch persistenten Speicher. Eckdaten:

* Speicher: Der Dienst liegt im Betrieb bei ~250–300 MB RAM (Datensatz 41 MB entpackt +
  Render-Caches) – die Free-Instanz hat 512 MB, das passt mit Reserve.
* CPU: Eine Kartenberechnung dauert serverseitig ~0,1–0,3 s (IDW-Gewichte gecacht);
  auch auf der geteilten Free-CPU bleibt die Karte dadurch flüssig.
* Der Datensatz (~26 MB komprimiert) liegt im Repo, Render nimmt ihn beim Deploy mit;
  die App lädt ihn beim Start im Hintergrund vor, der erste Nutzer wartet also nicht
  auf den Daten-Load.
* Solange **keine** geprüften Daten im Repo liegen, antwortet die App mit 503 und zeigt
  den Baufortschritt – ein leerer Free-Service wirkt also nicht „kaputt“, sondern
  erklärt, was gerade passiert.
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
| `GET /api/winterquarters/optimize` | beste Aufteilung Sommer-/Winterquartier (+ Ort, Städte, Alternativen) |
| `GET /api/winterquarters/pair` | Gegenstück-Suche zu einem festen Quartier (`lat, lon, role=summer|winter`) |
| `GET /api/cities`, `GET /api/countries` | statische Geo-Daten |

Alle Karten-/Orts-Endpunkte verstehen dieselben Parameter:
`tmax_min, tmax_max, tmin_min, tmin_max, tdew_max, sun_min, prec_max, width, fixed` sowie

| Parameter | Bedeutung |
|---|---|
| `month` | Zeitraum im Normalmodus: `all`, eine Nummer (`7`), ein Bereich inkl. Jahreswechsel (`5-9`, `10-3`) oder eine Liste (`1,2,12`) |
| `view` | `plain` (Vorgabe) oder `wq` = Winterquartier-Modus |
| `wview` | im Winterquartier-Modus die Kartenansicht: `combo`, `summer`, `winter` |
| `sfrom`, `sto` | Sommerfenster als Monat 1–12 (Winter = die übrigen Monate, 1–11 Monate) |
| `wtmax_min … wprec_max` | Wetterprofil des Winterquartiers (gleiche Namen mit `w`-Präfix) |
| `min_months`, `max_months` | erlaubte Saisonlänge für die Optimierung (Vorgabe 3–9) |
| `role`, `max_km` | Rolle des gewählten Ortes und Radiusgrenze bei `/api/winterquarters/pair` |

Im Winterquartier-Modus beschreiben die normalen Parameter (`tmax_min` …) das
**Sommer**-Profil. `/api/point` liefert dann zusätzlich den Block `wq` (Sommer-,
Winter- und Kombinations-Tage, Monatsprofile, Einordnung).

## Tests

```bash
pytest tests                  # Zähl-Kern, Rendering, API, Datentor, ERA5-Ableitungen
node tests/ui_statusseite.mjs # Statusseite mit Balken in jsdom (ohne Server)
node tests/ui_smoketest.mjs   # Karte in jsdom gegen eine laufende Instanz
```

Beides läuft auch als GitHub Action **Tests** bei jedem Push und Pull Request
(`.github/workflows/tests.yml`).

`tests/test_fetch_era5_zarr.py` baut einen kleinen Zarr-Speicher mit **geschlossenen
Funktionen** als Wetter und prüft die Ableitung auf exakte Tageswerte (inklusive
Längengrad-Umbruch bei 0°, Akkumulationsfenster 01:00–24:00, Sonnenschein-Schwelle,
unvollständige Tage als Lücken) – ganz ohne Internet.
`tests/test_data_gate.py` prüft das harte Tor: synthetische Daten ⇒ 503 +
Baufortschritt, `KLIMA_ALLOW_UNVERIFIED=1` ⇒ Karte mit Kennzeichnung.

`tests/test_climate.py` und `tests/test_api.py` deckt den Winterquartier-Modus ab:
Zählung über Monatsfenster (inkl. Jahreswechsel), Addition der beiden Profile,
Optimierung der Aufteilung (nachgerechnet gegen `comfort_counts`), Gegenstück-Suche
(Sortierung, Entfernungsfilter, beide Rollen) und die neuen Endpunkte.

Der UI-Smoketest prüft u. a., dass **keine** Kacheln von Drittanbietern geladen werden
(kein Watermark), dass Länderflächen selbst gezeichnet werden und dass Klicks auf Karte,
Städtemarker und Top-Orte die Detailkarte mit Klimaprofil öffnen. Außerdem prüft er die
Sichtbarkeitsregeln der Modi (Monatsauswahl nur im Urlaubsmonat, Winterquartier-Karten
und die Gegenstück-Liste nur im Winterquartier-Modus – inklusive der CSS-Regel
`[hidden] { display: none !important; }`, ohne die `display:flex` die Monatsauswahl
sichtbar ließe) und den ganzen Ablauf des Winterquartier-Modus bis zur Optimierung.
