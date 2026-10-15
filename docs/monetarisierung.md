# Monetarisierung mit wenig Betreuung und ohne laufende Akquise

Stand: **14.10.2026**. Strategie, noch keine Partnerintegration. Programmverfügbarkeit,
Annahmebedingungen, Provisionen und Netzwerkanforderungen wurden hier nicht live verifiziert.
Alle Zahlen unten sind **Rechenannahmen, keine Marktpreise oder Umsatzprognosen**.

## Umsetzungsstand im Code (14.10.2026)

Alles unten Beschriebene ist technisch vorbereitet, aber **nichts ist eingeschaltet** – es gibt
keinen Partnervertrag, kein Werbekonto, keine Domain und keine Betreiberangaben. Konkret gebaut:

* **Reichweite:** ~840 serverseitig gerenderte Ortsprofile (`/klima/{ort}`) mit echten Zahlen,
  Monatsprofil, Wintereignung, 2050-Vergleich, FAQ, eigener 1200×630-Grafik und Deep-Link in die
  App; zwei Ratgeber (`/ueberwintern-in-europa`, `/sommerhitze-entfliehen`), `sitemap.xml`,
  `robots.txt`, `feed.xml`, Startseiten-Grafik `/og/home.png` und ein einbettbares
  Kennzahl-Badge `/badge/{ort}.svg` (verlinkt zurück).
* **Teilen:** Netzwerk-Links (WhatsApp, Telegram, X, Facebook, Bluesky, Reddit, Pinterest,
  E-Mail) plus Kopieren und Bild-Download – normale Links, kein Fremd-Skript.
* **Affiliate:** `app/affiliate.py` mit Betreiber-Konfiguration (`app/data/affiliate.json` oder
  `KLIMA_AFFILIATE_FILE`), HTTPS-Pflicht, Host-Allowlist, Orts-Platzhaltern, Laufzeitfenstern und
  Länderfilter. Fehlt oder widerspricht etwas, erscheint **kein** Call-to-Action.
* **Messung:** `/analytics` zeigt zusätzlich die Funnel-Ereignisse (Teilen, Partnerklick,
  Inhaltsseiten, Sponsorplatz) als Tages-Summen – ohne Cookies, ohne Nutzerkennung.
* **Social-Automatik:** `scripts/social_pack.py` erzeugt wöchentlich Grafiken und Beitragstexte
  (`.github/workflows/social-pack.yml`, Artefakt); `scripts/social_post.py` veröffentlicht nur
  mit ausdrücklicher Freigabe und nur mit gesetzter öffentlicher Domain.
* **Rechtstexte:** Impressum und Datenschutzerklärung werden aus den echten Datenflüssen
  erzeugt; ohne ausgefüllte Betreiberangaben antworten beide Seiten mit 503.

Der einzige verbleibende Schritt für den Start steht in
[monetarisierung-fahrplan.md](monetarisierung-fahrplan.md).

## Klare Empfehlung

**Kostenlose Klimasuche → passendes Ziel → freiwilliger Unterkunftslink → Provision.**
Ein Partner übernimmt Angebot, Verfügbarkeit, Buchung, Zahlung und Kundendienst. Später
kann ein Werbenetzwerk unverkaufte Werbeplätze automatisch füllen. **Keine eigene
Quartierdatenbank, keine Vermieterakquise, keine persönliche Reisevermittlung.**

Das ersetzt die frühere Empfehlung, zuerst manuelle Reports, Concierge-Leistungen und
B2B-Piloten zu verkaufen. Diese Angebote sind für das gewünschte geringe Betreuungspensum
nicht der richtige Start. Vollständig passiv ist auch Affiliate-Marketing nicht:
Programmfreigabe, rechtliche Einrichtung, Qualitätskontrolle und Abrechnung bleiben nötig.

| Weg | Priorität | Einmalige Arbeit | Laufender Aufwand / Entscheidung |
| --- | --- | --- | --- |
| Unterkunfts-Affiliate über ein bestehendes Programm | **1 – starten** | Ein Programm auswählen, Freigabe, geprüfte Deep-Links, Kennzeichnung | Keine Anbieterakquise; Links und tatsächlich ausgezahlte Provisionen monatlich prüfen |
| Automatische Displaywerbung, z. B. AdSense als Kandidat | **2 – später testen** | Website-Freigabe, echte Anzeigen-IDs, Consent/CMP, Rechtsprüfung, ggf. ads.txt | Kein Anzeigenverkauf nötig; Richtlinien, Technik und Ertrag kontrollieren. Erst wenn Reichweite die laufenden Kosten rechtfertigt |
| Bezahlte Suchanzeigen **für die eigene Website** | **3 – erst bei bewiesenem Ertrag** | Kleiner, begrenzter Test mit passenden Suchbegriffen | Das sind **Kosten**, keine Einnahmen. Ohne positiven Deckungsbeitrag nicht skalieren |
| Direkte Sponsoren / Verkauf von Werbeplätzen | Nicht priorisieren | Vermieter oder Veranstalter gewinnen, Verträge und Rechnungen | Laufende Akquise; widerspricht dem Ziel. Nur bei eingehenden Anfragen nutzen |
| Concierge, manuelle Reports, eigenes Vermietungsportal, B2B-Vertrieb | Vorläufig weglassen | Hoher Produkt-/Vertriebsaufwand | Zu viel Support oder individuelle Betreuung |

## 1. Unterkunftsvermittlung ohne eigenes Inventar

### Ein Programm, nicht zehn Integrationen

Als **Prüfkandidaten**, nicht als bestätigte Partnerschaften: Unterkunftsprogramme rund um
Booking.com oder Expedia/FeWo-direkt sowie passende Reiseprogramme in Affiliate-Netzwerken
wie Awin oder Travelpayouts. Beim jeweiligen offiziellen Programm prüfen, ob es für Betreiber,
Region und Website offen ist und ob die gewünschte Verlinkung zugelassen ist. Ein Netzwerk
spart Einzelverhandlungen, garantiert aber weder Aufnahme noch Zugang zu jedem Anbieter.

Auswahlkriterien, in dieser Reihenfolge:

1. **Echte Eignung für Winterquartiere:** Gibt es möblierte Unterkünfte für 4–12 Wochen
   bzw. den gewünschten Aufenthalt? Sind z. B. Málaga, Algarve und Sizilien abgedeckt?
   Ein normales Hotelprogramm ist nicht automatisch ein Langzeitwohnungsprogramm.
2. Freigegebene Ziel-/Suchlinks statt einer API-Integration; Reiseanbieter betreibt
   Suche, Inventar und Checkout. Keine Preise oder Verfügbarkeiten selbst pflegen.
3. Klare Provision, Zuordnung/Attributionsfenster, Stornoregeln, Auszahlungsgrenze und
   Zahlungsverzug. Entscheidend ist **bestätigte/ausgezahlte**, nicht vorgemerkte Provision.
4. Datenschutz, Tracking, erlaubte Traffic-Quellen und Deep-Link-Regeln; insbesondere
   bezahlte Suchanzeigen und Marken-Keywords ausdrücklich prüfen.
5. Möglichst ein Dashboard und ein Vertrag. Erst bei nachweisbaren Lücken einen zweiten
   Partner ergänzen; keine Vielzahl von Feeds, Skripten oder Konten aufbauen.

### Nutzerführung in dieser App

- In den **Details eines gewählten Ziels**, vor allem nach erfolgreicher Winterquartiersuche:
  **„Unterkünfte in Málaga ansehen · Werbung / Affiliate-Link“**.
- Direkt dabei: „Bei einer Buchung können wir eine Provision erhalten. Das Klimaranking
  bleibt unabhängig. Preise und Verfügbarkeit beim Anbieter prüfen.“ Keine unbelegte
  Zusage „gleicher Preis“ oder „beste/günstigste Unterkunft“.
- **Keine automatische Weiterleitung**, keine Overlay-Anzeige auf der Karte. Nur ein
  bewusst angeklickter Link; Zielanbieter vorher erkennbar. Kein Login in der Klima-App.
- Die App kennt eine Saison, aber nicht zwingend ein konkretes Reisejahr oder
  Buchungsdaten. Zunächst nur den **Zielort** übergeben. Optional später An-/Abreise und
  Gästezahl ausdrücklich bestätigen lassen. Oktober–April nicht stillschweigend in
  Buchungsdaten umwandeln; Aufenthaltslimits des Partners beachten.
- Unterstützt der Partner die lange Aufenthaltsdauer nicht, offen darauf hinweisen und
  die Suche beim Anbieter anbieten. Keine 7-Nächte-Suche als verfügbares Winterquartier ausgeben.
- Ausschließlich freigegebene Parameter (Ziel, ggf. bestätigte Reisedaten, eigene Partnerkennung)
  übertragen. **Nie Wohnort, vollständiges Klimaprofil oder persönliche Suchhistorie**.
- Keine Unterkunfts- oder Preislisten scrapen. Keine Provision durch bloße Verlinkung
  behaupten: Ohne freigeschaltetes Programm und korrekten Affiliate-Link gibt es keinen
  eingerichteten Provisionsfluss.

### Kleine technische Umsetzung nach Programmfreigabe

Ein serverseitig konfigurierter Link-Adapter reicht: `enabled`, Anbietername, erlaubter
HTTPS-Host, echte Partnerkennung und dokumentierte Orts-/Datumsparameter. Partner-URL nach
dessen Vorgaben erzeugen (manche Programme erlauben nur unveränderte/signierte Links).
Keine frei wählbare Weiterleitungs-URL aus Besuchereingaben und keine API-Geheimnisse im
Browser. Bei fehlender/ungültiger Konfiguration den Affiliate-CTA vollständig verbergen.

Zunächst direkte Links mit `rel="sponsored nofollow noopener noreferrer"`, keine Pixel,
keine eingebetteten Partnerwidgets und kein eigener Redirect-/Trackingdienst. Das ist
technisch einfacher, aber **keine automatische Einwilligungs- oder Datenschutzbefreiung**.
Programmkonformität und Rechtsgrundlage einschließlich Attribution/Referrer prüfen.

## 2. Werbung automatisch verkaufen, nicht selbst akquirieren

Ein Publisher-Netzwerk kann die Nachfrage beschaffen. Ein unaufdringlicher Platz **unter
Ergebnissen oder einem Ratgeber**, nicht vor Suchstart und nicht über der Karte, genügt
für den ersten Test. Auf Mobilgeräten Platz reservieren, damit nichts springt.

Voraussetzungen: Publisher- und Website-Freigabe, echte IDs, erforderliche Betreiber- und
Datenschutzangaben, geeignete Consent-Lösung gemäß Anbieter und Rechtslage, ggf. ads.txt.
Ablehnen und Widerrufen testen; die Klimasuche muss ohne Werbeeinwilligung funktionieren.
Nicht personalisierte Anzeigen sind kein pauschaler Ausweg aus diesen Anforderungen.

**Der vorhandene `/api/ads`-Platz ist kein Werbenetzwerk.** Er zeigt nur selbst konfigurierte
Sponsor-Kampagnen; er sucht keine zahlenden Werbekunden. Er bleibt standardmäßig aus und
ist lediglich für spätere eingehende Sponsorenanfragen nützlich. Eine Netzwerk-Anzeige
braucht eine separate Integration; keine fremden Skripte in die Kampagnen-JSON schreiben.
Technische Details: [werbung-und-wachstum.md](werbung-und-wachstum.md).

## 3. Reichweite: wenige gute Einstiegsseiten statt täglicher Produktion

Diese Seiten **existieren jetzt** und rechnen sich bei jedem Datenlauf neu:

- `/ueberwintern-in-europa` – Rangliste im Winterfenster Oktober–April, zwei Profile
  („milder“ und „warmer Winter“), Vergleich mit deutschen Städten, Methode, FAQ.
- `/sommerhitze-entfliehen` – Juli/August nach dem Profil „tagsüber 12–28 °C, Taupunkt ≤ 15 °C“,
  ebenfalls mit Vergleichsstädten.
- `/klima` und `/klima/{ort}` – rund 840 Ortsprofile (Orte ≥ 100.000 Einwohner plus kuratierte
  Winter-/Küstenorte; Schwelle über `KLIMA_LANDING_MIN_POP` einstellbar). Jede Seite hat eigene
  Canonical-URL, eigene Social-Grafik, echte Zahlen und einen Deep-Link in die App.

Keine tausenden austauschbaren Stadtseiten: Orte unterhalb der Schwelle bekommen bewusst keine
Seite. Keine tägliche Social-Media-Pflicht: `scripts/social_pack.py` erzeugt vier Wochen Beiträge
auf einmal. Kein Aufbau einer gepflegten Unterkunftsliste. Suchreichweite braucht trotzdem Zeit.

## 4. Erst Ertrag messen, dann eventuell Traffic einkaufen

**Illustration mit frei angenommenen Werten:**

| Schritt pro Monat | Annahme / Rechnung |
| --- | --- |
| Besuche | 10.000 |
| Unterkunftsklicks | 12 % der Besuche = 1.200 |
| Zugeordnete Buchungen | 1,5 % der Partnerklicks = 18 |
| Durchschnittliche Provision | 35 € vor Stornokorrektur |
| Bestätigter Anteil nach Stornos | 80 % |
| Affiliate-Ertrag | 10.000 × 0,12 × 0,015 × 35 € × 0,8 = **504 €** |
| Display, separat | 20.000 Seitenaufrufe × angenommene 3 € Seiten-RPM / 1.000 = **60 €** |
| Zusammen | **564 € vor Betrieb, Steuern und eigener Arbeit** |

Das sind keine erwartbaren Ergebnisse. Bei nur 3 % Unterkunftsklicks und 0,5 % Buchungen
wären es bei sonst gleichen Annahmen nur **42 € Affiliate-Ertrag**. Programmquoten,
Stornos, Auszahlungsverzug, Consent und Adblocker können das Ergebnis stark verändern.

Im ersten Beispiel: 564 € / 10.000 Besuche = **0,0564 € Erlös je Besuch**. Schon ein
angenommener Einkaufspreis von 0,30 € pro Anzeigenklick wäre unrentabel. Die eigene Zeit,
Hosting, CMP und sonstige Kosten senken die tragbare Grenze weiter. Gekaufte Besucher
können sich anders verhalten als organische; diese Erträge nicht ungeprüft übertragen.

**Regel:** Keine laufenden Such-/Social-Anzeigen, bevor für diese Zielgruppe belastbare,
nach Stornos bestätigte Erträge vorliegen. Danach höchstens ein ausdrücklich freigegebenes
Testbudget (z. B. 100 € Gesamtkappe, ein Kanal, kein Retargeting). Suchbegriffe mit
Quartierabsicht statt „Wetter morgen“. Pausieren, wenn der Preis pro qualifiziertem Besuch
über dem gemessenen Erlös abzüglich Kosten und Sicherheitspuffer liegt. Nicht auf
Buchungsklicks als angebliche Käufe optimieren. Es wurde kein Budget ausgegeben.

## 5. Schlanker Start- und Betriebsplan

### Einrichtung (einmalig, Freigabezeiten sind nicht planbar)

1. Betreiberangaben, Datenschutz, Werbekennzeichnung sowie kommerzielle Daten-/Kartenrechte
   prüfen. Standard-OSM hat Nutzungsregeln und keine garantierte Lieferfähigkeit; bei
   wachsendem Traffic einen geeigneten Kartentarif mitkalkulieren.
2. Ein Affiliate-Programm anhand obiger Kriterien beantragen. Bis zur Aufnahme keine
   vermeintlichen Buchungsangebote oder Platzhalter-Partnerlinks veröffentlichen.
3. Nach Freigabe einen Ziel-CTA integrieren und mit fünf Orten testen: Mobil/Desktop,
   Sonderzeichen, richtige Destination, langer Aufenthalt und Jahreswechsel. Nur gemäß
   Programmbedingungen testen; keine provisionspflichtigen Eigenbuchungen erzeugen.
4. Drei hilfreiche Einstiegsseiten veröffentlichen. Automatische Link-Checks nur im
   Rahmen der Programmregeln; keine Bots durch Affiliate-Trackinglinks schicken, die
   dadurch Klicks vortäuschen. Ein zentraler Abschalter muss Links sofort verbergen können.
5. Nach einem ausreichend langen Zuordnungs-/Stornozeitraum Ertrag prüfen. Displaywerbung
   nur ergänzen, wenn erwarteter Mehrertrag die Consent-/Technik-/Betreuungskosten deckt.

### Wiederkehrend (Ziel: 30–60 Minuten monatlich, keine Garantie)

- Partner-Dashboard: Klicks, vorgemerkte **und bestätigte** Provisionen, Auszahlungen,
  Stornos, Warnungen. Drei bis fünf Ziel-Links stichprobenartig prüfen.
- Einnahmen minus Hosting/CMP/sonstige Kosten protokollieren. Nach Einrichtung
  Betreuungszeit mitschreiben; unrentable Kanäle oder solche mit zu viel Aufwand abschalten.
- Freigabe-/Richtlinienänderungen und Ausfälle erfordern zeitnahe Reaktion, nicht erst
  am Monatsende. Inhalte mindestens vor der Wintersaison auf Aktualität prüfen.

**Messgrenze des jetzigen Codes (aktualisiert):** `/analytics` zählt Seitenaufrufe als
Tages-Summen (keine eindeutigen Besucher) und zusätzlich erlaubte Funnel-Ereignisse:
Inhaltsseite geöffnet, App aus einer Inhaltsseite gestartet, Ergebnis geteilt (Dialog,
Netzwerk-Link, kopiert), Partnerlink geöffnet, Sponsorplatz geklickt, Einbettungs-Code kopiert.
Damit sind die ersten Stufen der Funnelrechnung belegbar – **nicht** die Buchung selbst: Die
Erfolgsquote und die bestätigten Provisionen stehen nur im Partner-Dashboard. Seitenaufrufe
niemals als Besucher ausgeben, Partnerzahlen getrennt auswerten. Eine weitergehende
Conversion-Messung (etwa Kampagnenkennungen oder Partner-Postbacks) erst mit überprüfter
Rechtsgrundlage und nötiger Einwilligung.

## Nicht umgesetzt / nächster Entscheidungspunkt

Keine Affiliate-Anmeldung, Publisher-Konten, Werbetags, CMP, Weiterleitungslinks,
Zahlungsintegration oder Akquisekampagnen wurden aktiviert. Der vorhandene Sponsorplatz bleibt
unverändert aus, ebenso die Partnerlinks: `app/affiliate.py` ist fertig, aber
`app/data/affiliate.json` enthält `enabled: false` und keine Partner. Impressum und
Datenschutz sind gesperrt, bis Betreiberangaben in `app/data/legal.json` stehen.

**Nächster Schritt bleibt organisatorisch, nicht technisch:** Domain und Tarif wählen,
Betreiberangaben eintragen, *ein* langzeitgeeignetes Unterkunftsprogramm auswählen und dessen
Kennung in die Konfiguration schreiben. Reihung, Aufwand und Grenzen:
[monetarisierung-fahrplan.md](monetarisierung-fahrplan.md).
