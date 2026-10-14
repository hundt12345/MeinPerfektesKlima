# Mein perfektes Klima – Europa

Interaktive Europakarte nach dem Vorbild von *myperfectweather.com*: Du stellst dein
Wohlfühlwetter ein (Höchst- und Tiefsttemperatur, Taupunkt, Sonnenscheindauer,
optional Niederschlag) und siehst sofort, **wie viele Tage pro Jahr** dieses Wetter
wo erreicht wird – von Blau (wenige Tage) bis Rot (viele Tage).

Drei Modi:

1. **Ganzes Jahr** – „Wo ist es übers Jahr gerechnet am häufigsten genau so, wie ich es mag?“
2. **Reisemonat** – „Ich will im Juli Urlaub machen: Wo gibt es dann die meisten Tage mit
   meinem Wunschwetter?“ (Die Monatsauswahl erscheint nur in diesem Modus.)
3. **Winterquartier** – „Ich lebe in dieser Stadt – wohin kann ich im Winter oder Sommer ausweichen?“
   Dein Wohnort bleibt fest, die App sucht ein passendes saisonales Reiseziel (siehe [unten](#der-winterquartier-modus)).

Über **„Welches Klima?“** wählst du das Klima von heute oder eine Modellrechnung für
2030/2050 mit drei verständlich benannten Wegen der Klimapolitik
(siehe [unten](#welches-klima-heute-2030-und-2050)).

Dazu:

* **Klick auf die Karte oder auf eine Stadt** öffnet eine Detailkarte mit den Wohlfühl-Tagen
  je Monat, der Einordnung („besser als 87 % aller Rasterpunkte“, Bestwert in Europa) und dem
  Klimaprofil des Ortes (Ø min/max, Taupunkt, Sonnenstunden, Niederschlag, Sommer-/Frosttage).
* **Ortssuche** über der Karte (Name tippen, z. B. „Sev…“), Top-Orte-Liste für die aktuelle
  Einstellung, teilbare Links (alle Parameter stecken im URL-Hash). Die Suche ist tolerant:
  Akzente und Umlaute sind egal (`malaga`, `muenchen`, `koeln`), deutsche und landessprachliche
  Namen funktionieren beide (`Mailand`/`Milano`, `Kopenhagen`/`København`, `Genf`/`Genève`),
  ebenso Inseln für den Hauptort (`Mallorca` → Palma, `Kreta` → Heraklion). Städte heißen
  in der Oberfläche so, wie man sie auf Deutsch kennt (München, Rom, Moskau, Kiew …).
* **Vollständige Städtedaten**: die eingeklappte Tabelle unter der Karte listet alle Städte mit
  Klimamitteln, Hitze-/Sommer-/Winter-/Eistagen, Schwüle, Regen, Sonne und weiteren Kennzahlen.
  Sie lässt sich durchsuchen und sortieren; der CSV-Export folgt dem aktuellen Suchfilter.
* **Detaillierte OSM-Grundkarte**: Straßen, Orte und weitere Kartendetails werden dynamisch aus
  OpenStreetMap-Kacheln geladen; eine Ebenensteuerung schaltet die Klimafläche ein/aus. Die OSM-
  Attribution bleibt sichtbar. Beim Reinzoomen werden die Straßen detaillierter, die Klimadaten
  bleiben aber auf ihrem 0,75°-Raster (grob 50–80 km) – Zoom erzeugt keine neuen Messwerte.

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

## Welches Klima? Heute, 2030 und 2050

Die Karte **„Welches Klima?“** hat drei Knöpfe: **Heute | 2030 | 2050**.

* **Heute (2016–2025):** Mittel über zehn Jahre ERA5-Tagesdaten. Ein Häkchen
  **„Nur die letzten 5 Jahre (2021–2025)“** wählt den jüngeren Ausschnitt, der stärker schwankt.
* **2030 / 2050:** Darunter erscheinen drei Wege der Klimapolitik – sprechend benannt statt
  als SSP-Kürzel, jeweils mit einem Satz Erklärung und der mittleren Erwärmung Europas
  gegenüber 2016–2025 (berechnet aus `app/data/projections.json`):

| In der App | Szenario | Erklärung | Europa 2030 | Europa 2050 | Welt 2100* |
|---|---|---|---|---|---|
| **Viel Klimaschutz** | SSP1-2.6 | Die Welt senkt ihre Emissionen schnell. | +0,2 °C | +0,5 °C | ≈ +1,8 °C |
| **Mittlerer Weg** (Standard) | SSP2-4.5 | Klimaschutz etwa wie bisher, die Emissionen sinken langsam. | +0,4 °C | +1,1 °C | ≈ +2,7 °C |
| **Wenig Klimaschutz** | SSP5-8.5 | Die Emissionen steigen weiter stark. | +0,5 °C | +1,6 °C | ≈ +4,4 °C |

\* globale Erwärmung 2081–2100 gegenüber 1850–1900, beste Schätzung aus IPCC AR6 WG I
(SPM, Tabelle SPM.1). Die Europa-Werte sind Jahresmittel über alle Rasterpunkte; die
Ortsdetails nennen den Wert für den angeklickten Ort („hier etwa +1,0 °C wärmer als 2016–2025“).
Die Kürzel (SSP…) stehen weiterhin klein in Legende, Tooltips, API und CSV.

Der Zeitraum gilt einheitlich für Karte, Legende, Punktdetails, Ranking,
Städtemarker, Winterquartier-Suche und die vollständige Städtetabelle/CSV.
Beide Varianten verwenden bereits gespeicherte Daten; es wird keine externe
Klimaschnittstelle pro Klick aufgerufen. Der gesamte Datensatz umfasst nur zehn
Jahre und ist **keine offizielle 30-jährige Klimanormalperiode**.

**2030 und 2050 sind ausdrücklich modellbasierte Näherungen.**
31 CMIP6-Modelle aus dem regional aggregierten IPCC-Atlas liefern monatliche Temperaturänderungen
und Niederschlagsfaktoren für SSP1-2.6, SSP2-4.5 und SSP5-8.5. Sie werden auf die historischen
ERA5-Tage übertragen. 2030 = 2021–2040, 2050 = 2041–2060, Änderungsbasis = 2016–2025.
Taupunkt wird bei angenommener konstanter relativer Feuchte geschätzt; Sonnenstunden und
die Wetterabfolge bleiben unverändert. Es werden keine lokalen täglichen Zukunftsläufe oder
neuen Extremverteilungen simuliert. Ortsdetails zeigen die regionale Modellstreuung, nicht
Unsicherheitsintervalle der Wohlfühltage. Die Projektionen gelten für alle Auswertungen inkl. CSV.

Quelle, Annahmen, Reproduktion, Lizenz und Datentor:
[docs/klimaprojektionen.md](docs/klimaprojektionen.md).
Die kleine abgeleitete Datei `app/data/projections.json` wird mit ausgeliefert;
fehlende/ungültige oder nicht zur ERA5-Basis passende Manifeste deaktivieren Zukunftsoptionen.

**Monetarisierung und Werbung:** [docs/monetarisierung.md](docs/monetarisierung.md) beschreibt
Report- und Partnerangebote; [docs/werbung-und-wachstum.md](docs/werbung-und-wachstum.md)
beurteilt Reichweitenkampagnen, dynamische Sponsoranzeigen und Voraussetzungen für AdSense.
Ein kontextueller First-Party-Werbeplatz ist implementiert, standardmäßig ausgeschaltet und
ohne Cookies/Ad-Tracking. Aktivierung erfolgt nur über geprüfte Betreiber-Kampagnen in
`app/data/ad_campaigns.json` oder `KLIMA_AD_CAMPAIGNS_FILE`. Kein Netzwerk-Konto, Budget oder
Sponsor wurde angelegt, keine AdSense-Tags/CMP integriert.

## Der Winterquartier-Modus

Die Karte **„Dein Winterquartier“** steht in diesem Modus ganz oben:

1. **Wo wohnst du?** – Stadt eintippen. Vorschläge erscheinen sofort (↑/↓ und Enter gehen
   auch); auswählen muss man aber nichts: **„Ziele finden“ nimmt den besten Treffer** der
   Eingabe. Früher blieb der Knopf gesperrt, bis ein Vorschlag angeklickt war – wer nur
   tippte und klickte, bekam keine Reaktion.
2. **Wann willst du weg?** – im Winter oder im Sommer.
3. **Sommer ist für dich** von … bis … – ein Satz darunter sagt, wann du wo bist
   („Mai – September zu Hause, Oktober – April im Winterquartier“).

Dasselbe Wohlfühlwetter gilt an beiden Orten. Zwischen Wohnort und Ziel liegen mindestens
50 km Luftlinie. Das Sommerfenster wird **nicht automatisch verändert** und darf 1 bis 11
Monate umfassen. Alle erfassten Zielstädte werden geprüft.

Das Ergebnis nennt die schönen Tage pro Jahr, wenn man ganzjährig zu Hause bleibt, und listet
bis zu acht Ziele mit dem **Zugewinn in Tagen** (auch negativ), dem Land und der Entfernung.
Die Ziele liegen **mindestens 120 km auseinander** (`spread_km`): Früher füllten oft sieben
Nachbarorte desselben Rasterpunkts die Liste (Murcia, Molina de Segura, Alcantarilla …).
Haben mehrere Orte dieselben Werte, steht die größte Stadt in der Liste. Wohnort und Ziele
erscheinen als nummerierte Punkte auf der Karte; ein Klick öffnet die Ortsauswertung.
Wohnort, Saison und Filter bleiben in teilbaren URL-Hashes erhalten.

Die Karte zeigt ergänzend die Jahreszahl an einem **Einzelort**, nicht die Summe aus Wohnort
und Ziel; die Einzelort-Rangliste ist in diesem Modus ausgeblendet.

Die Oberfläche verwendet `GET /api/cities/search?area=data` (nur Orte im Klimaraster) und
`GET /api/winterquarters/escape` mit `lat`, `lon`, `season=winter|summer`, Wetterparametern,
`sfrom`, `sto`, `period`, `limit` und `spread_km` (Standard 120, `0` = ohne Streuung).
Die globale Suche `/api/winterquarters/combination` und die älteren Endpunkte
`/api/winterquarters/optimize` und `/api/winterquarters/pair` bleiben für bestehende
API-Clients verfügbar, werden aber von der aktuellen Oberfläche nicht mehr verwendet.

Grenzen: ERA5-Mittel über zehn Jahre glätten einzelne Extremjahre; die
Auswertung ist eine Optimierung der Tageszahlen, keine Empfehlung zu Miete,
Visum oder Anreise.

## Kartenbild: keine „Striche“ mehr

Auf der Karte waren gerade Linien zu sehen, die nicht zur Landschaft gehörten. Es gab drei
unabhängige Ursachen; alle drei sind behoben und durch Tests abgesichert
(`tests/test_search_and_geo.py`, `tests/ui_smoketest.mjs`):

1. **Ländergrenzen-Daten.** `countries.geojson` enthielt Länder*flächen*, die auf den
   Kartenausschnitt zugeschnitten waren. Jede Schnittkante wurde als Grenze gezeichnet:
   gerade Linien bei 29° N (quer durch Nordafrika), 50° O (Russland/Kasachstan/Iran),
   74° N und 30° W (Grönland). Dazu kreuzt Russland in world-atlas die Datumsgrenze; planar
   gelesen entstanden Bänder quer über die Welt – sichtbar als Linien bei 65°, 69° und 71° N
   (Karelien, Kola, Barentssee). `scripts/build_geo.py` macht solche Ringe jetzt stetig
   (`fix_antimeridian`) und schreibt nur noch **Landgrenzen als Linien** (`topojson.mesh`,
   jede Grenze einmal, zugeschnitten ohne künstliche Kanten; Küsten zeigt die Grundkarte).
   Ab Zoomstufe 10 übernimmt die genauere OSM-Grenze. Die Datei schrumpft von 331 auf 87 KB.
2. **Kachelnähte.** Bei 125 % oder 150 % Bildschirmskalierung (unter Windows üblich) zeichnet
   Chrome zwischen den 256-px-Kacheln haarfeine Lücken – ein Gitter aus Strichen, das durch die
   halbtransparente Klimafläche scheint. Die Kacheln überlappen jetzt um 1 px
   (`mix-blend-mode: normal`, 257 px), der Kartenhintergrund hat den Farbton der OSM-Karte.
   Gemessen in Headless-Chrome: vorher 90–120 Nahtlinien bei 125–150 %, jetzt keine.
3. **Harte Datenkante.** Das Klimaraster endet bei 45° O und 34° N mitten über Land; die Fläche
   brach dort mit einer geraden Kante ab. Sie läuft jetzt über 0,75° weich aus
   (`EDGE_FEATHER_DEG` in `app/climate.py`).

Nebenbei behoben (gleiche Ursache wie 1. bzw. Namensabgleich): In der Städteliste fehlten
u. a. **Kopenhagen, Sarajevo, Skopje, Split, Nikosia und Murmansk**, weil Küstenpunkte knapp
außerhalb der vereinfachten Landfläche lagen, Natural Earth Kurznamen verwendet
(„Bosnia and Herz.“, „Macedonia“) und Russland zwischen 65° und 69° N fehlte. „City of London“
(Doppeleintrag von London) ist entfernt; Malta und Andorra sind mit ihrem größten Ort vertreten.

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
| Landgrenzen (zusätzliche Linienebene) | [world-atlas](https://www.npmjs.com/package/world-atlas) (Natural Earth 1:10m) | nur Grenzen zwischen Ländern, als Linien; Küsten und Details kommen aus OSM |
| Städteliste für Suche/Marker/Top-Orte | [GeoNames cities15000](https://www.geonames.org/) | 4.163 Orte ≥ 25.000 Einwohner in Europa (dazu der größte Ort kleiner Länder), bekannte Städte mit deutschem Namen |

Lizenzen/Attribution: ERA5-Daten enthalten modifizierte Copernicus-Klimadaten
(2026); bereitgestellt von ECMWF, gespiegelt als offene, analysis-ready Kopie durch
Google Research (Copernicus-Lizenz, keine API-Nutzungsbedingungen, kein Tageskontingent,
kein Schlüssel nötig). Kartendaten: © [OpenStreetMap-Mitwirkende](https://www.openstreetmap.org/copyright)
(ODbL; dynamische Standard-Kacheln mit Attribution in der Karte). Natural Earth ist gemeinfrei,
GeoNames CC-BY-4.0, Leaflet BSD-2. Der frühere Weg über die Open-Meteo-API entfällt – siehe
[„Warum nicht die Open-Meteo-API?“](#warum-nicht-die-open-meteo-api).

**Grenzen der Aussage:** ERA5 ist ein Reanalyse-Modell (≈0,25°), kein Stationsmesswert.
In Gebirgen und an Küsten sind Abweichungen von mehreren Grad möglich; der Datensatz
ist zusätzlich auf 0,75° ausgedünnt und Werte sind auf 0,5 °C, 0,25 h bzw. 0,5 mm
gerundet. Je nach Auswahl ist „ein Jahr“ ein Mittel über den gesamten zehnjährigen
Datensatz oder nur 2021–2025; einzelne Extremjahre werden im längeren Mittel stärker geglättet.

## Architektur

```
app/
  main.py            FastAPI: Seiten + JSON-/PNG-Endpunkte, Vorwärmen im Hintergrund
  climate.py         Datenzugriff, Tageszählung, Web-Mercator-Rendering (PIL/scipy)
  names.py           deutsche Städtenamen, Suchnamen und tolerante Ortssuche
  data/              Datensätze (Klimaraster, Ländergrenzen, Städte, Qualitätsbericht)
  static/            Startseite (index.html), Styles, Leaflet (vendored)
scripts/
  fetch_era5_zarr.py      ERA5 aus offenen Zarr-Daten lesen, Tageswerte ableiten   (Standard)
  build_progress.py       Baufortschritt schreiben + auf Zweig „klima-fortschritt" veröffentlichen
  fetch_climate_grid.py   alternativer Abruf über die Open-Meteo-API (nur mit Kontingent)
  make_dev_grid.py        plausibles synthetisches Raster für Offline-Entwicklung/Demo
  check_grid.py           Qualitätstor: Vergleich mit Klimanormalwerten (22 Städte)
  build_countries.mjs     world-atlas → Länderflächen + Landgrenzen (GeoJSON)
  build_geo.py            Landgrenzen-Linien + Städteliste bauen (Datumsgrenze, Küsten, Namen)
  download_sources.sh     Rohquellen (world-atlas, Leaflet, GeoNames) laden
app/build_progress.py     liest den Fortschritt (Datei/GitHub) und bewertet ihn für die UI
.github/workflows/
  build-climate-data.yml  führt fetch_era5_zarr.py + check_grid.py auf einem
                          GitHub-Runner aus und committet das Ergebnis zurück
render.yaml          Render-Blueprint (Web-Service, Free-Plan)
```

### Oberfläche und Gestaltung

Die Oberfläche ist dunkel und **bewusst schlicht**: ruhige Flächen, eine Akzentfarbe, keine
Zierlinien, Leuchteffekte oder Verläufe. Jede Karte hat eine kurze Überschrift in normaler
Schreibweise und höchstens einen Satz Hilfe; Details (Methode, Modellstreuung, weitere
Klimawerte) sind eingeklappt. Reihenfolge der Bedienleiste: Modus → (Winterquartier) →
Welches Klima? → Dein Wohlfühlwetter (mit Schnellauswahl) → beste Orte → Ansicht.
Die App-Logik und statischen Assets kommen vom eigenen Dienst; für die Grundkarte
werden OSM-Kacheln direkt im Browser nachgeladen:

* **Schrift:** Inter (SIL OFL 1.1) liegt als zwei WOFF2-Dateien unter
  `app/static/vendor/inter/` im Repo (~133 KB zusammen) und wird selbst ausgeliefert –
  kein Google-Fonts-Aufruf, kein Layoutsprung durch nachladende Schriften, gleiches
  Bild auf allen Geräten. Die Daten stehen in `unicode-range`-Bereiche getrennt
  (Latein + Latein-Erweitert, also inkl. `ä ö ü ß` und `ł ç ş`).
* **Farbflächen, Verläufe, Symbole** sind reines CSS (inkl. Logo und Suchlupe) –
  keine Bilddateien, keine zusätzlichen Anfragen.
* **Kartendetails:** OpenStreetMap liefert Straßen und Ortsnamen bis in hohe Zoomstufen;
  die Ebenensteuerung kann die Klimafläche ausblenden. Die Leaflet-Attribution nennt die
  OSM-Mitwirkenden. Das durchscheinende Klimaraster zeigt weiterhin nur die Datenauflösung 0,75°.
* **Farbskala der Karte:** `COLOR_STOPS` in `app/climate.py` ist die eine Quelle für
  Karte, Legende (`/api/meta`) und Städtepunkte; die Stützstellen sind so gewählt,
  dass die Stufen auf dunklem Grund klar getrennt wirken.
* **Detailkarte:** große Zahl, Balken je Monat, Klimadiagramm und sechs Kennzahlen
  (tagsüber, nachts, Sonne, Regen, Sommer- und Frosttage); alles Weitere eingeklappt.
  `prefers-reduced-motion` schaltet alle Animationen ab.

Für den Free-Plan auf Render bleibt der Serverbedarf praktisch unverändert:
Die OSM-Kacheln ruft der Browser direkt beim OpenStreetMap-Kartendienst ab, sie
laufen nicht über Render und belegen dort weder App-Bandbreite noch Speicher.
Die Klimazeitraum-Auswahl nutzt das bereits deployte ERA5-Raster und benötigt
keine neue Daten-API. Die üblichen OSM-Tile-Nutzungsregeln gelten trotzdem;
bei starkem Verkehr kann später ein dedizierter Tile-Anbieter erforderlich sein.

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
* **Bandbreite:** Der Free-/Hobby-Plan enthält 5 GB Übertragung pro Monat
  (Stand 2026; früher 100 GB), danach kostet jedes weitere GB. Textantworten
  (JSON/GeoJSON/JS/CSS) werden deshalb per GZip-Middleware komprimiert – die
  Ländergrenzen schrumpfen von ~330 KB auf ~106 KB, die Städteliste von 1,6 MB
  auf 264 KB. Das Kartenbild (PNG, ~300–450 KB) ist der größte Einzelposten;
  es wird pro Einstellung serverseitig 10 Minuten lang zwischengespeichert
  (`Cache-Control: max-age=600`), sodass der Browser beim Zurückblättern nichts
  erneut laden muss.
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
| `GET /api/ads?context=wq&placement=sidebar` | optionale kontextuelle Sponsorkampagne, standardmäßig leer |
| `GET /api/meta` | Datensatz-Infos, Standardwerte, Presets, verfügbare Zeiträume/Projektionen, Rasterpunkte, Qualitätsstatus |
| `GET /api/map` | Statistik (min./max. Tage, Legendenwerte) + Rasterwerte |
| `GET /api/map.png` | Kartenbild (Web-Mercator-PNG, transparent über See) |
| `GET /api/point?lat=&lon=` | Auswertung eines Ortes: Wohlfühl-Tage, Monatsprofil, Klimaprofil, nächster Ort, Einordnung |
| `GET /api/ranking` | beste Städte für die aktuellen Parameter |
| `GET /api/cities/map` | größte Orte mit Tageszahl (für anklickbare Marker) |
| `GET /api/cities/climate` | vollständige Städteliste mit Jahresmitteln, Tageskennzahlen und Grenzwerten |
| `GET /api/cities/search?q=&area=all|data` | tolerante Ortssuche; Treffer mit `alias` (eingegebener Zweitname) und `exact`; `area=data` nur im Klimaraster |
| `GET /api/winterquarters/escape` | fester Wohnort (`lat`, `lon`), Ausweichsaison (`season=winter|summer`), Ziele (≥ `spread_km` auseinander) und Zugewinn |
| `GET /api/winterquarters/combination` | globale Zwei-Orte-Suche: ein Profil, festes Sommerfenster, beste Paare |
| `GET /api/winterquarters/optimize` | Legacy-Endpunkt: automatische Saisonaufteilung mit zwei Profilen |
| `GET /api/winterquarters/pair` | Legacy-Endpunkt: Gegenstück-Suche zu einem festen Ort (`lat, lon, role=summer|winter`) |
| `GET /api/cities`, `GET /api/countries` | statische Geo-Daten |

Die Karten- und Ortsendpunkte verstehen die gemeinsamen Wetterfilter
`tmax_min, tmax_max, tmin_min, tmin_max, tdew_max, sun_min, prec_max`; `period=all|recent5|2030|2050`
wählt historische Daten oder Zukunftsnäherungen (Standard SSP2-4.5). Alternativen:
`2030-ssp126`, `2030-ssp585`, `2050-ssp126`, `2050-ssp585`. Kartenabfragen können zusätzlich
`width` und `fixed` annehmen. Die Quartiersuche nutzt dieselben Wetterparameter plus:

| Parameter | Bedeutung |
|---|---|
| `lat`, `lon`, `season` | Wohnort im Europagebiet und Ausweichsaison (`winter` oder `summer`), nur `/escape` |
| `sfrom`, `sto` | festes, zusammenhängendes Sommerfenster (Monat 1–12); der Winterort übernimmt die übrigen Monate |
| `limit` | maximale Zahl der zurückgegebenen Kombinationen |
| `spread_km` | Mindestabstand der vorgeschlagenen Ziele untereinander (Standard 120, `0` = aus), nur `/escape` |

Für bestehende Clients bleiben die historischen Kartenfilter und getrennten
Profilparameter weiterhin verfügbar:

| Parameter | Bedeutung |
|---|---|
| `month` | Zeitraum im Normalmodus: `all`, eine Nummer (`7`), ein Bereich inkl. Jahreswechsel (`5-9`, `10-3`) oder eine Liste (`1,2,12`) |
| `view`, `wview` | Legacy: `plain`/`wq` und die Kartenansicht `combo`, `summer` oder `winter` |
| `wtmax_min … wprec_max` | Legacy-Profil des Winterquartiers (gleiche Namen mit `w`-Präfix) |
| `min_months`, `max_months` | Legacy: erlaubte Saisonlänge des Optimierers (`/api/winterquarters/optimize`) |
| `role`, `max_km` | Legacy: Rolle und optionale Distanzgrenze bei `/api/winterquarters/pair` |

Die aktuelle Oberfläche verwendet diese Legacy-Parameter nicht: Im Winterquartier-
Modus gilt ein gemeinsames Wohlfühlwetter-Profil für beide Städte und Zeiträume.

## Tests

`tests/test_search_and_geo.py` sichert die Ortssuche (Umlaute, ae/oe/ue, deutsche und lokale
Namen, Füllwörter, Datengebiet), die gestreuten Ausweichziele, die sprechenden Szenarionamen,
die Grenzlinien (keine Schnittkanten, keine Datumsgrenzen-Sprünge), die reparierte Städteliste
und den weichen Kartenrand ab. Zusätzlich prüfen `tests/test_projections.py` die Taupunktannahme, Monatsdeltas,
Filter ohne erneute int8-Quantisierung, Zuordnung der Regionen, API-Konsistenz und
Fail-closed-Verhalten ungültiger Manifeste. `tests/test_ads.py` prüft Kontext, Laufzeit,
Konfigurationswechsel, URL-Sicherheit und deaktivierte Werbung. Der UI-Smoketest prüft
Zukunfts-Methode/Modellstreuung und sichere dynamische Anzeigen.


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

`tests/test_climate.py` und `tests/test_api.py` decken Monatsfenster und die
Winterquartier-Berechnungen ab: Die Zwei-Orte-Suche wird gegen dasselbe Profil,
das unveränderte Monatsfenster, die getrennten Monatszählungen, die Summe und den
Mindestabstand geprüft. Die Wohnort-Suche wird zusätzlich in beiden Ausweichsaisons
und beiden Zeiträumen gegen exakte Monatswerte und den Zugewinn gegenüber zu Hause getestet. Die Legacy-Endpunkte werden weiterhin auf Kompatibilität
getestet.

Der UI-Smoketest prüft u. a., dass OSM-Kacheln mit Attribution und Layer-Steuerung
verwendet werden und dass Klicks auf Karte, Städtemarker und Top-Orte die Detailkarte
mit Klimaprofil öffnen. Außerdem prüft er die Zeitraumauswahl (Fünfjahresansicht;
2030/2050 in drei Szenarien als Näherungen aktiviert) und die Sichtbarkeitsregeln der Modi
(Monatsauswahl nur im Reisemonat, Wohnortsuche nur im Winterquartier-Modus –
inklusive der CSS-Regel
`[hidden] { display: none !important; }`, ohne die `display:flex` die Monatsauswahl
sichtbar ließe) sowie den festen Saisonzeitraum, die Wohnortsuche (auch nur getippt, ohne
Klick in die Liste), den Saisonwechsel, die sprechenden Szenarien, die überlappenden Kacheln
gegen Nahtstriche und das Fehlen dekorativer Striche im CSS.
