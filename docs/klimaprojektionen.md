# Klimaprojektionen 2030 / 2050 – verfügbare Näherung

Stand: **14.10.2026**. Die App bietet jetzt **modellbasierte regionale Delta-Näherungen** für 2030 und 2050 an, in Karte, Monatsansicht, Ranking, Ortsdetails, Städtetabelle/CSV und Wohnort-/Ausweichquartier-Suche. Es handelt sich **nicht** um importierte lokale tägliche Zukunfts-Modellläufe und auch nicht um eine Vorhersage einzelner Wettertage.

## Tatsächliche Datengrundlage

- [1 – Regional aggregierte CMIP6-Daten des IPCC AR6 Atlas](https://github.com/SantanderMetGroup/ATLAS/tree/a1687c6db5590eb9b618fd4e1ebba4b842084031/datasets-aggregated-regionally): monatliche, flächengewichtete Landmittel von `tas` (°C) und `pr` (mm/Tag), ein Lauf je Modell. Verwendet werden **31 vollständige Modelle**, die beide Variablen in allen drei Szenarien abdecken.
- Szenarien: **SSP1-2.6, SSP2-4.5, SSP5-8.5**. Standard ist SSP2-4.5; kein Szenario wird als Eintrittswahrscheinlichkeit ausgegeben.
- Regionen: GIC (Grönland/Island), NEU (Nordeuropa), WCE (West-/Mitteleuropa), EEU (Osteuropa), MED (Mittelmeer), RAR (russische Arktis), WCA (West-/Zentralasien). Die [IPCC-Referenzpolygone](https://github.com/SantanderMetGroup/ATLAS/blob/a1687c6db5590eb9b618fd4e1ebba4b842084031/reference-regions/IPCC-WGI-reference-regions-v4.geojson) ordnen die vorhandenen ERA5-Punkte zu. Alle 2.250 Punkte unseres aktuellen Rasters liegen in den verwendeten Polygonen. Für abweichende Raster wird ein nächstgelegenes Polygon als explizit gezählte Randzuordnung verwendet (`nearest_region_points`).
- **2030 = 2021–2040**, **2050 = 2041–2060**. Vergleichsbasis: **2016–2025** im jeweils selben Modelllauf und Szenario. Diese aktuellen 10 Jahre entsprechen der ERA5-Basis; sie sind keine offizielle 30-jährige Klimanormalperiode. Das 2030-Fenster überlappt bewusst mit der jüngsten Vergangenheit.
- Festgeschriebener Quellenstand: Git-Commit `a1687c6db5590eb9b618fd4e1ebba4b842084031`. Der Quellbestand unterstützt den IPCC-Atlas von 2021; dies ist nicht das neue GERICS-Ensemble von 2026.

## Reproduzierbare Verarbeitung

`scripts/build_projections.py` lädt die Quell-CSV-Dateien über GitHub, prüft Git-Blob-Identitäten, Einheiten und vollständige eindeutige Monatsabdeckung und erzeugt `app/data/projections.json` (ca. 350 KB). Rohdaten liegen nur im lokalen Cache, nicht im Repository.

```bash
python scripts/build_projections.py
# benötigt numpy sowie gh mit Zugriff auf öffentliche GitHub-Repositories
```

Für jeden Modelllauf, Monat, Zeitraum und jede Region:

1. Temperaturänderung = zukünftiges Monatsmittel minus Monatsmittel 2016–2025.
2. Niederschlagsfaktor = zukünftiges Monatsmittel geteilt durch Monatsmittel 2016–2025.
3. Über die 31 Modellläufe werden **Median sowie 10. und 90. Perzentil** der jeweiligen Änderungen/Faktoren berechnet.
4. Die regionalen Monatsmediane werden auf die historischen ERA5-Tage angewandt. Tagesverläufe werden nicht als simulierte Zukunftszeitreihen datiert; der Zähl-Nenner bleibt die tatsächlich verwendete historische Stichprobe. Die 20 Jahre des Zukunftsfensters und `analog_sample_years` sind getrennte Metadaten.

Manifest und Modellliste enthalten Quellpfade, Git-Blob-SHAs und SHA-256-Prüfsummen. Im Betrieb gibt es **keine** externe Projektionsanfrage pro Klick.

## Temperatur, Taupunkt, Sonne und Regen

| Variable | Berechnung | Annahme / Grenze |
| --- | --- | --- |
| Tmax, Tmin | ERA5-Tageswert + regionale monatliche Medianänderung von `tas` | Beide erhalten denselben Zuschlag. Keine gesonderte Projektion der Tag-/Nachtspanne oder Extremverteilung. |
| Taupunkt | Relative Feuchte aus historischem Taupunkt und `(Tmin+Tmax)/2` über die Magnus-Formel schätzen; bei erwärmter Temperatur denselben Feuchteanteil annehmen und neuen Taupunkt berechnen | Konstante relative Feuchte ist eine **Annahme**, keine aus GERICS abgeleitete Feuchteprojektion. Tagesmitteltemperatur wird angenähert; Tagesmittelbildung ist nicht identisch mit sub-täglicher Berechnung. Die Magnus-Näherung verwendet Sättigung über Wasser, auch an kalten Tagen. |
| Sonnenstunden | Historische ERA5-Sonnenstunden unverändert | **Keine Sonnenstunden-Projektion.** Nicht mit zukünftiger Globalstrahlung verwechseln. |
| Niederschlag | Historische Tagesmenge × regionaler monatlicher Medianfaktor | Intensitäten ändern sich; historische trockene Tage bleiben trocken. Veränderungen der Regenhäufigkeit, Persistenz und Extremvariabilität werden nicht modelliert. |

Die abgeleiteten Temperaturen und Regenmengen bleiben Fließkommazahlen. Sie werden **nicht** wieder in int8 gepresst oder bei hohen Temperaturen abgeschnitten. Filter vergleichen exakte skalierte Schwellen mit den abgeleiteten Werten; die historischen Quantisierungsregeln bleiben unverändert.

Die Wohlfühltage werden aus der gemeinsamen historischen Wetterabfolge mit diesen Änderungen neu gezählt. Der Ensemble-Median der monatlichen Treiber ist **nicht** der Median von täglich modellierten Wohlfühltagen. Die Methode erhält die historischen Zusammenhänge nur näherungsweise und kann sich von täglichen, multivariat bias-adjustierten Zukunftsläufen unterscheiden.

## Transparenz und Datentor

- Jede Zukunftsoption heißt **„Projektion … (Näherung)“** und trägt ein Szenario.
- Die sichtbare Warnung nennt geschätzten Taupunkt, unveränderte Sonne und fehlende neue Extremvariabilität.
- Ortsdetails zeigen die Monatsänderungen und **P10–P90 der regionalen Temperaturänderung**. Das ist ausdrücklich **kein Unsicherheitsintervall der Wohlfühltage**. Eine nicht monotone Veränderung der Wohlfühltage ist möglich: Erwärmung kann je nach Profil sowohl nützen als auch schaden.
- API-Antworten enthalten `projection`, `approximation`, `method`, `baseline`, `analog_sample_years`, `model_count`, `scenario`, Quelle, Lizenz und Annahmen. Der Zukunfts-CSV ergänzt Kennzeichnungs-/Methodik-/Quellspalten.
- Fehlendes oder ungültiges Manifest, falsche ERA5-Basis, unvollständige Monatsfelder, ungeordnete Perzentile oder unplausible Änderungen deaktivieren die Zukunftsauswahl. Die historische ERA5-Qualitätsprüfung bleibt bestehen. Wenn die Basis später aktualisiert wird, müssen die Projektionsdeltas passend neu gebaut werden.
- `KLIMA_PROJECTIONS_FILE` kann ein anderes Manifest wählen; es unterliegt denselben Prüfungen.

## GERICS: Einordnung, nicht erfundene Taupunktquelle

Die vom Nutzer genannte [GERICS-News vom 26.08.2026](https://www.climate-service-center.de/about/news_and_events/news/122448/index.php.de) beschreibt 124 regionale EURO-CORDEX-Simulationen, aktualisierte Beobachtungen, Jahreszeitenauswertungen und CSV-Produkte. Die [Produktseite](https://www.gerics.de/products_and_publications/fact_sheets/landkreise/index.php.de) beschreibt das E-OBS-Beobachtungsfundament und die 400 deutschen Landkreise. Diese Seite liefert **keinen europaweiten Taupunktzuschlag**. Wir verwenden sie als weiterführende Quelle, nicht als Beleg für unsere Feuchteannahme oder als Ersatz für europaweite Temperaturdaten.

## Quellen und Nutzungsrechte

Der [Atlas-Lizenztext](https://github.com/SantanderMetGroup/ATLAS/blob/a1687c6db5590eb9b618fd4e1ebba4b842084031/LICENSE.md) stellt die **regional aggregierten CMIP6-Daten** ausdrücklich unter **CC BY 4.0** bereit, einschließlich kommerzieller Verwendung mit Namensnennung. Nicht pauschal auf alle sonstigen CMIP6-Rohdaten übertragen.

Namensnennung: Iturbide et al. (2021), *Repository supporting the implementation of FAIR principles in the IPCC-WG1 Atlas*, DOI: 10.5281/zenodo.3691645. WCRP und beteiligte CMIP6-Modellgruppen anerkennen. Unsere Änderungen (Monatsdeltas und Übertragung auf ERA5) stehen im Manifest und im Seitenfooter.

Weiterführende Quellen für eine spätere vollwertige Tagesdaten-Version:

- [Copernicus CORDEX](https://cds.climate.copernicus.eu/datasets/projections-cordex-domains-single-levels?tab=overview)
- [Copernicus CMIP6](https://cds.climate.copernicus.eu/datasets/projections-cmip6?tab=overview)
- [C3S-Temperaturstatistiken Europa](https://cds.climate.copernicus.eu/datasets/sis-temperature-statistics?tab=overview)

**Nächster wissenschaftlicher Ausbau:** Bias-adjustierte lokale tägliche Modellläufe und Feuchtevarianten importieren, Modell-für-Modell-Wohlfühltage auswerten und deren Streuung anzeigen. Die jetzt verfügbare Näherung ist keine Empfehlung für Sicherheits-, Gesundheits- oder Immobilienentscheidungen.
