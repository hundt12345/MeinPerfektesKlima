# Homepage bewerben und dynamisch Werbung ausspielen

Stand: **14.10.2026**. Zwei verschiedene Ziele: **Reichweite einkaufen/aufbauen** (wir sind Werbetreibender) und **Werbeplätze monetarisieren** (wir sind Publisher). Die folgenden Budgets und Erlöse sind Testannahmen, keine Marktpreis- oder Umsatzversprechen.

> **Aktuelle Priorität: wenig Betreuung, keine laufende Akquise.** Der verbindliche
> Startplan steht in [monetarisierung.md](monetarisierung.md): ein Unterkunfts-Affiliate,
> später ggf. automatische Netzwerkanzeigen. Die unten beschriebenen Sponsor-, PR- und
> Reichweitenkampagnen sind optionale Möglichkeiten, **nicht** der empfohlene Start.
> Bezahlten Traffic erst nach gemessenem Ertrag testen.

## 1. Wie wir die Seite bewerben sollten

### Zuerst: produktnahe Reichweite

| Kanal | Konkreter Ansatz | Warum passend | Messung / Test |
| --- | --- | --- | --- |
| SEO / hilfreiche Ratgeber | **Implementiert:** `/ueberwintern-in-europa`, `/sommerhitze-entfliehen` und rund 840 Ortsprofile `/klima/{ort}` mit echten Zahlen, Methode, Datenstand und Grenzen; dazu `sitemap.xml`, `robots.txt` und `feed.xml`. Orte unterhalb der Einwohnerschwelle bekommen bewusst keine Seite. | Suchintention passt direkt zur Wohnort-/Quartiersuche. Keine tausenden nahezu identischen Stadtseiten. | Organischer Einstieg → abgeschlossene Suche; Domain in der Search Console anmelden und die Sitemap einreichen. `/analytics` zeigt Aufrufe der Inhaltsseiten und Starts der Klimasuche. |
| Google-Suchanzeigen | Begrenzter Test für „Winterquartier Europa“, „Überwintern mildes Klima“, „Sommerurlaub ohne Hitze“. Auf passende Quartiersuche mit Anleitung verlinken. | Bereits vorhandener Bedarf statt allgemeinem Wetter-Traffic. | Kosten pro abgeschlossener Suche, später pro tatsächlich gekauftem Report. Negative Keywords wie „Wetter morgen“, „kostenlose Wohnung“ und andere unpassende Anfragen ergänzen. |
| Partnerschaften | Überwinterer-Newsletter, Remote-Work-Communities, Relocation-Blogs und Reiseberater: nachvollziehbaren Stadtvergleich anbieten. | Glaubwürdige Empfehlungen; ggf. später B2B-Kunden. | Individuelle Kampagnenlinks, nachweisbare Ergebnisse; bezahlte Beiträge klar kennzeichnen. |
| Instagram / Pinterest / kurze Videos | Beispiel „Ich wohne in Köln: Wohin im Winter?“ oder „Berlin + Sommerquartier“. Karte erklären, Annahmen zeigen, Link zur App. **Halbautomatisch:** `python scripts/social_pack.py --weeks 4` erzeugt vier Wochen Grafiken und fertige Beitragstexte (montags auch per GitHub-Action als Artefakt). | Anschauliches Produkt, gut für Reiseplanung. | Qualifizierte Besuche und Suchabschlüsse, nicht nur Views. Kartendaten-/OSM-Namensnennung auch bei veröffentlichten Screenshots beachten. |
| Automatisches Veröffentlichen (ohne Bezahl-API) | Mastodon, Telegram und Bluesky lassen sich kostenlos per Schnittstelle bespielen (`scripts/social_post.py`, nur mit `--post` und hinterlegten Zugangsdaten). Instagram/TikTok/Pinterest haben keine freie Automatik – dort die fertigen Dateien einplanen. | Kein täglicher Aufwand, kein Werbenetzwerk, keine Fremdskripte in der Seite. | Beobachtete Reichweite je Kanal gegen die Ereignisse in `/analytics` stellen und unrentable Kanäle wieder abschalten. |
| PR / Fachmedien | Kleine Datenstory mit reproduzierbarer Methode, nicht „Der sicherste Wohnort 2050“. | Vertrauensaufbau ohne Anzeigenbudget. | Verweise, Besucher und fachliches Feedback; keine Aussage über wissenschaftlich abgesicherte tägliche Zukunftsdaten. |

### Kleiner bezahlter Pilot statt breiter Kampagne

- Erst Nutzerfluss und Ladezeit auf dem echten Deployment prüfen. Ein schlafender Render-Free-Service kann gekaufte Einstiege verlieren; vor größerem Budget einen dauerhaft aktiven Tarif kalkulieren.
- Testbudget-Vorschlag: **150–300 € über 14 Tage**, zunächst ein Kanal, tägliche Limits. Das ist kein empfohlenes garantiertes Mindestbudget eines Anbieters.
- Zwei Botschaften testen: „Dein Winterquartier passend zu deinem Wohlfühlwetter“ und „Im Sommer der Hitze ausweichen – von deinem Wohnort aus“.
- UTM-Kampagnenparameter nur als allgemeine Kampagnenkennung; keine Namen, Wohnortkoordinaten oder Wunschprofile in Werbepartner-URLs. Teilen der App selbst nutzt URL-Hashes; nicht für personenbezogenes Retargeting auswerten.
- Ohne Reporting zunächst keine automatischen optimierten Gebotsziele vortäuschen. Conversion-Messung/Analytics erst mit überprüfter Rechtsgrundlage und ggf. Einwilligung aufsetzen.
- Vorab Stop-Regel wählen: z. B. nach dem Pilot nur weitermachen, wenn die Kosten je abgeschlossener Suche unter dem aus realen Verkäufen abgeleiteten Wert liegen.

**Beispiel für die Wirtschaftlichkeit (rein hypothetisch):** Bei 12 € Reportpreis und 1 % Kaufquote unter Werbe-Besuchen entstehen nur 0,12 € Brutto-Kundenzahlungen je Besuch – vor Steuer, Gebühren und Support. Teurer Traffic wäre damit nicht wirtschaftlich. Erst echte Kaufquoten messen; Anzeigenumsätze allein finanzieren bezahlten Traffic in diesem Beispiel nicht.

## 2. Dynamische Anzeigen: jetzt technisch vorbereitet

Die App unterstützt **selbst verwaltete kontextuelle Sponsoranzeigen**:

- Endpoint: `GET /api/ads?placement=sidebar&context=year|month|wq`.
- Kriterien: allgemeiner Seitenmodus, Zeitfenster der Kampagne, Aktivierungsstatus, Platzierung und Gewicht. Bei mehreren passenden Kampagnen erfolgt eine gewichtete Zufallsauswahl, keine personenbezogene Profilbildung.
- Anzeigen werden beim Einstieg bzw. Moduswechsel geladen. Kein aggressives Auto-Refresh, kein Popup und keine Werbung auf der Klimakarte.
- Auslieferung aus der eigenen API, nur Text und direkter HTTPS-Link. Keine Werbenetzwerk-Skripte, externen Werbebilder, Pixel, Cookies, localStorage oder eigenen Impression-/Click-Tracking-Endpunkte.
- Im Browser sichtbar **„Anzeige · [Werbetreibender]“**; Links tragen `sponsored nofollow noopener noreferrer`. Werbetexte werden als Text gerendert, nicht als HTML.
- Keine Beeinflussung von Klimaranking oder Quartier-Vorschlägen. Das Werbesystem erhält weder Wohnort noch Profil noch Projektionsauswahl.
- Fehlende, leere, ausgeschaltete, abgelaufene oder ungültige Konfigurationen verbergen den Platz vollständig. Unsichere Links und unzulässige Script-Felder werden abgelehnt. Eine ungültige Kampagne deaktiviert vorsichtshalber die gesamte Konfiguration.
- **Standardmäßig aus:** `app/data/ad_campaigns.json` enthält `enabled: false` und keine Kampagnen. Es gibt noch keinen tatsächlichen Sponsor oder Partnervertrag.

### Aktivieren durch den Betreiber

Die Datei ist eine serverseitige Betreiber-Konfiguration, **kein** öffentliches Admin-Formular. Entweder geprüfte Kampagnen im Deployment bereitstellen oder `KLIMA_AD_CAMPAIGNS_FILE` auf eine verwaltete JSON-Datei setzen. Die Konfiguration wird bei jeder Anzeigenanfrage neu gelesen; Änderungen brauchen bei einer beschreibbaren externen Datei keinen App-Neustart. Auf Render-Free ist das Dateisystem nicht dauerhaft: dort in der Regel über einen neuen Deploy aktualisieren oder persistenten Speicher einrichten. Es wurde kein Remote-CMS angebunden.

Schema-Beispiel, **nur Illustration** – `example.com` durch einen real vertraglich freigegebenen Sponsor ersetzen, Daten anpassen und erst nach rechtlicher Prüfung einschalten:

```json
{
  "schema_version": 1,
  "enabled": false,
  "campaigns": [
    {
      "id": "winter-sponsor-2026",
      "enabled": true,
      "advertiser": "Name des tatsächlichen Sponsors",
      "title": "Langzeitquartiere für die Wintersaison",
      "body": "Nachprüfbare Beschreibung des echten Angebots, keine erfundenen Klimaversprechen.",
      "cta": "Angebot ansehen",
      "url": "https://example.com/winterquartiere",
      "contexts": ["wq"],
      "placements": ["sidebar"],
      "start": "2026-11-01",
      "end": "2027-03-31",
      "weight": 2
    }
  ]
}
```

Datumsgrenzen sind inklusiv und werden nach UTC ausgewertet. Gewicht 2 gegenüber Gewicht 1 bedeutet im Mittel doppelte Auswahlwahrscheinlichkeit, keine garantierten Impressionen. Ohne Tracking keine impressionbasierte Abrechnung versprechen; für den Einstieg einen festen Sponsoringpreis vereinbaren. Normale Infrastruktur-/Serverzugriffslogs können weiterhin IP-Adressen/URLs enthalten und benötigen eigene Datenschutz-/Aufbewahrungsregeln. „Kein Werbetracking“ ist keine pauschale Datenschutz-Zertifizierung.

## 3. AdSense / Ad Manager: möglich, aber bewusst noch nicht eingeschaltet

AdSense ist prinzipiell ein möglicher Ausbau für programmatic Anzeigen; eine Freigabe der konkreten Website ist **nicht zugesichert**. Die Anforderung „ausreichender eigenständiger Inhalt“ ist jetzt erfüllbar: Die Ortsprofile und Ratgeber werden serverseitig aus dem Datensatz gerendert, es gibt Impressum/Datenschutz (sobald die Betreiberangaben stehen), Canonical-URLs und eine Sitemap. Vor Einbindung braucht es weiterhin:

1. Echte Domain, überprüfte Publisher-Anmeldung, Website-Freigabe, erforderliche rechtliche Seiten und ausreichenden eigenständigen Inhalt. Es wurde kein Publisher-Konto erstellt und kein Werbebudget ausgegeben.
2. Publisher-ID und echte Anzeigenplatz-IDs. Keine Platzhalter-Publisher-IDs im Code.
3. Passende Consent-Management-Platform. [Google verlangt für personalisierte Anzeigen im EWR, Vereinigten Königreich und in der Schweiz eine Google-zertifizierte TCF-CMP](https://support.google.com/adsense/answer/13554116?hl=de). Ein selbst gebauter „Cookies akzeptieren“-Button ist dafür kein Ersatz. Nicht personalisierte oder eingeschränkte Anzeigen sind nicht automatisch ohne jede rechtliche Prüfung/Einwilligung zulässig; Anbieter-Konfiguration und konkrete Datennutzung prüfen.
4. Drittanbieter-Ad-Tags nur nach geeigneter freigegebener Einwilligung/CMP-Signal laden; Ablehnung und Widerruf testen. Nicht allein auf Consent Mode als Rechtsgrundlage verlassen.
5. `ads.txt` mit den tatsächlich zugewiesenen Publisher-Verkäuferdaten, falls vom Anbieter benötigt; keine erfundenen IDs. CSP, Performance, Layout-Verschiebungen, Adblocker und mobile Bedienbarkeit prüfen.
6. Quellen-/Lizenzkennzeichnung sowie Projektion-Warnungen nie mit Werbung verdecken. Keine irreführenden Klickflächen oder Aufforderung, Anzeigen anzuklicken.

**Bewertung:** Für das Ziel „wenig Betreuung“ zuerst passende Affiliate-Angebote, keine direkte Sponsorenakquise. AdSense erst testen, wenn echte Reichweite und ein tragfähiges Consent-Setup vorhanden sind. Die jetzt implementierte First-Party-Lösung ist kein AdSense-Adapter und keine CMP.

Hypothetische Display-Rechnung: 20.000 Seitenaufrufe × 2–8 € angenommener Seiten-RPM / 1.000 = **40–160 €** monatlich. Diese Bandbreite ist **keine recherchierte marktübliche Vergütung**, sondern eine Sensitivitätsrechnung. Tatsächliche Erträge hängen vom Anbieter, Publikum, Consent, Adblockern und Werbedruck ab.

## 4. Optionaler Freigabecheck für einen direkt gewonnenen Sponsor

- [ ] Betreiberangaben, Impressum und Datenschutzerklärung auf dem echten Deployment prüfen; Werbekennzeichnung und Vertrag abstimmen.
- [ ] Einen tatsächlichen Sponsor gewinnen, keine unbestätigten Partnerschaften veröffentlichen.
- [ ] Eine textbasierte WQ-Kampagne in obiger Konfiguration prüfen und freischalten.
- [ ] Kleine Traffic-Kampagne mit Tagesbudget erst im Werbekonto des Betreibers anlegen; Zugangsdaten nicht ins Repository.
- [ ] Kosten pro qualifiziertem Suchabschluss und Reportkauf erheben, bevor größere Budgets oder Netzwerkanzeigen starten.

Den priorisierten Startplan ohne manuelle Reports oder Sponsorenakquise beschreibt [monetarisierung.md](monetarisierung.md). Die Anzeigenfunktion wird mit Kontext-/Zeitfilter-, Abschaltungs-, URL-/HTML-Sicherheits- und UI-Tests geprüft.
