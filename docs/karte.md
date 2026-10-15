# OSM-Karte und lesbare Klimabeschriftungen

## Regression und Reparatur

PR #11 (`565a719`) führte die direkte, bis Zoom 19 zoombare OSM-Karte ein.
Später wurde sie durch CARTO-Grund- und Beschriftungskacheln ersetzt. Aktuell ist
wieder ausschließlich `https://tile.openstreetmap.org/{z}/{x}/{y}.png` eingebunden:
kein API-Key, kein CARTO-Account, keine zusätzliche externe Labels-API.
Die konkrete Ursache einer beim Nutzer angezeigten API-Key-Meldung wurde hier
nicht am betroffenen Deployment reproduziert.

**Ebenen von unten nach oben:**

1. OSM-Standardkacheln inklusive Original-Straßen-/Ortsnamen (`tilePane`, 200).
2. Klimaraster-PNG (`overlayPane`, 400), standardmäßig 55 % Deckkraft.
3. Ländergrenzen bis Zoom 9 (`borders`, 450).
4. Zusätzliche kontrastreiche Ortsnamen aus vorhandenen GeoNames-Daten
   (`placeLabels`, 500, nicht interaktiv).
5. Klimapunkte der Städte (`cities`, 550), Auswahlen, Tooltips und Popups darüber.

Die Ortsnamen benötigen keine zweite Kachelanfrage. Sie werden aus der vorhandenen
`/api/cities/map`-Antwort aufgebaut. Größere Orte haben Vorrang; nach Verschieben,
Zoom und Größenänderung wird die sichtbare Auswahl neu berechnet. Rechtecktests
mit Abstand vermeiden Kollisionen zwischen diesen Zusatzlabels, maximal 80
werden zugleich angezeigt. Kleine Städte erscheinen erst bei höherem Zoom.
Diese Auswahl ist **keine vollständige Straßen-/Ortsbeschriftung**; dafür bleibt OSM zuständig.

Im Ebenenmenü kann „Ortsnamen über Klimadaten“ unabhängig von Klimapunkten geschaltet
werden. Labels fangen weder Klicks noch Ziehen/Hover ab. Ohne Klimafläche oder bei
0 % Deckkraft verschwinden die Zusatzlabels, damit OSM allein sichtbar ist.

Der Deckkraftregler ändert nur die Anzeige, nicht Klimawerte, Ranking, Legende
oder PNG-URL. Für Straßenbeschriftungen bei hohem Zoom ggf. reduzieren. Auch eine
spät eintreffende PNG-Antwort darf die gewählte Deckkraft nicht zurücksetzen.
„Zurücksetzen“ stellt wieder 55 % ein. Die Deckkraft ist eine lokale
Darstellungseinstellung, kein Bestandteil geteilter Klimaprofile.

## „Access blocked“ durch unterdrückten Referer

Die direkte OSM-Einbindung kollidierte mit dem seitenweiten
`<meta name="referrer" content="no-referrer">`: Kachelanfragen hatten keinen
Referer, entgegen der OSM-Tile-Usage-Policy. Die Leaflet-Kachelebene setzt deshalb
gezielt `referrerPolicy: "strict-origin-when-cross-origin"`. Leaflet überträgt diese
Einstellung auf jedes Kachelbild. OSM erhält nur die Website-Herkunft, keine
Pfade, Suchparameter oder Klimaprofile. Die seitenweite Datenschutzregel bleibt
`no-referrer`. Keine Umgehung durch andere Hosts, Proxys oder Cache-Busting.
Die tatsächliche Entsperrung muss nach Deployment im Browser geprüft werden;
weitere providerseitige Sperrgründe sind damit nicht ausgeschlossen.

## Betrieb

Leaflet und Schriften bleiben lokal. Der Browser lädt sichtbare OSM-Kacheln direkt;
kein App-Proxy, kein Massendownload/Offline-Prefetch, kein Unterdrücken von Referer
oder Browser-Caching. Die Attribution bleibt sichtbar. Nutzungsregeln prüfen:
<https://operations.osmfoundation.org/policies/tiles/>.

**Ohne Key bedeutet nicht unbegrenzter kostenloser Kartendienst.** OSM bietet keine
Liefergarantie; bei höherem Traffic oder anderen Anforderungen einen passenden
Provider/Hosting-Vertrag planen. Kein automatischer Fallback auf einen Key-pflichtigen
Provider ist eingebaut. Die Klimadatenauflösung bleibt trotz Straßenzoom 0,75°.

## Regressionstests

- `npm run test:map`: echtes lokal gebündeltes Leaflet in jsdom, ohne laufende API
  oder externe Downloads. Prüft Kachelhost, Attribution, Zoom 19, Ebenenreihenfolge,
  sichere Ortsnamen, Kollisionen, Bildschirmgrenzen, Pan/Resize, Layerwechsel und
  Deckkraft während Bildladen/Refresh. Läuft in CI.
- `npm run smoke -- http://127.0.0.1:8000`: vollständiger UI/API-Ablauf mit
  Leaflet-Stub; Startdeckkraft, Regler, Kartenquelle und bestehende Klimafunktionen.
- `node tests/ui_statusseite.mjs`: Statusseite ohne freigegebene Daten.

### Visuelle Abnahme im normalen Browser

Die externe OSM-Auslieferung ist in der eingeschränkten Entwicklungsumgebung nicht
prüfbar; jsdom testet nicht das tatsächliche Rasterbild. Vor Veröffentlichung:

1. Startseite nach Deployment neu laden; Netzwerk zeigt OSM-Kacheln, keine CARTO-/Key-Anfragen.
   Bei Kachelanfragen muss `Referer` nur die Website-Herkunft enthalten (keine
   Pfade/Query); die Bilder dürfen nicht mehr „Access blocked“ zeigen.
2. Europa → Stadt → Straße zoomen und ziehen; Klimafläche bleibt georeferenziert.
3. Dunkle und helle Klimaregionen prüfen: zusätzliche Ortsnamen lesbar, keine
   gegenseitigen Überlappungen; Kartenausschnitt verschieben.
4. Deckkraft 0 / 30 / 55 / 90 % und ausgeschaltete Klimadaten prüfen. Bei Bedarf
   Zusatzlabels ausschalten und reine OSM-Details ansehen. Labels dürfen keine
   Kartenklicks, Mausbewegungen oder Ziehgesten blockieren.
5. Zeitraum/Preset wechseln, während das PNG lädt; Deckkraft bleibt gewählt.
6. Desktop bei 125/150 % Skalierung sowie schmales Mobilfenster prüfen; Suchfeld,
   Zoom, Ebenensteuerung und Attribution bleiben erreichbar.
