# Fahrplan: was zu tun ist, damit die Seite trägt

Stand **14.10.2026**. Kurzfassung in einer Seite. Ausführliche Begründung:
[monetarisierung.md](monetarisierung.md) · [werbung-und-wachstum.md](werbung-und-wachstum.md).
Alle Beträge sind Rechenannahmen, keine Zusagen.

## Was bereits im Code steckt (nichts davon ist eingeschaltet)

| Baustein | Zustand | Einschalten durch |
|---|---|---|
| ~840 Ortsprofile `/klima/{ort}` mit echten Zahlen, Monatsprofil, 2050-Wert, Vergleich, FAQ, eigener Social-Grafik | **aktiv** | nichts |
| Ratgeber `/ueberwintern-in-europa`, `/sommerhitze-entfliehen` (Tabellen rechnen bei jedem Datenlauf neu) | **aktiv** | nichts |
| `robots.txt`, `sitemap.xml`, `feed.xml`, `/og/home.png`, Kennzahl-Badge `/badge/{ort}.svg` zum Einbetten | **aktiv** | nichts |
| Teilen-Kit (WhatsApp, Telegram, X, Facebook, Bluesky, Reddit, Pinterest, E-Mail) + Teilen-Grafiken | **aktiv** | nichts |
| Funnel-Messung ohne Cookies (Teilen, Partnerklick, Inhaltsseiten) im Dashboard `/analytics` | **aktiv**, braucht Schlüssel | `KLIMA_ANALYTICS_TOKEN` + persistentes Volume |
| **Partner-/Affiliate-Links** (ein Adapter, mehrere Programme möglich) | **aus** | `app/data/affiliate.json` bzw. `KLIMA_AFFILIATE_FILE` |
| **Impressum + Datenschutzerklärung** (aus den echten Datenflüssen erzeugt) | **gesperrt (503)** | `app/data/legal.json` ausfüllen, `configured: true` |
| Sponsorplatz `/api/ads` | **aus** | `app/data/ad_campaigns.json` |
| Automatisches Social-Paket: Grafiken + Beitragstexte, wöchentlich per GitHub-Action | **aktiv**, veröffentlicht nichts | optional Secrets für Mastodon/Telegram/Bluesky |

## Was du einrichten musst (Reihenfolge nach Wirkung pro Aufwand)

1. **Domain + dauerhaft laufender Tarif** (Render „Starter“ statt Free, ~7 $/Monat). Grund: Die
   Inhaltsseiten sind der Hauptkanal; ein schlafender Free-Dienst verliert Suchmaschinen- und
   Kaltstart-Besucher. — 30 Min, laufende Kosten.
2. **`app/data/legal.json` ausfüllen** (Betreiber, Kontakt, Hosting) → Impressum und Datenschutz
   erscheinen, Fußzeilen-Links aktiv. Ohne diesen Schritt ist nichts davon rechtskonform
   betreibbar und Affiliate-/Werbenetzwerke nehmen die Seite nicht an. — 1 h, bei Unsicherheit
   einmal juristisch drüberschauen.
3. **Reichweite messbar machen:** `KLIMA_ANALYTICS_TOKEN` setzen, `KLIMA_ANALYTICS_DB` auf ein
   persistentes Volume legen. — 15 Min.
4. **Search Console** einrichten, `https://deine-domain/sitemap.xml` einreichen, Domain als
   kanonisch setzen (`KLIMA_PUBLIC_URL=https://deine-domain`). — 30 Min, danach nichts mehr.
5. **Genau ein Affiliate-Programm** beantragen (Kandidaten prüfen: Booking.com- bzw.
   Expedia/FeWo-direkt-Programm über ein Netzwerk wie Awin oder Travelpayouts; Kriterium:
   Langzeitaufenthalte 4–12 Wochen und Abdeckung von Algarve, Andalusien, Sizilien, Kreta).
   Nach Freigabe `app/data/affiliate.json`: `enabled: true`, Partnerkennung, erlaubter Host,
   Orts-Platzhalter `{city}`. **Ohne Freigabe passiert nichts** – der Platz bleibt unsichtbar.
   — 1–2 h Arbeit, 1–14 Tage Freigabezeit.
6. **Streuen ohne Handarbeit:** GitHub-Action „Social-Paket bauen“ läuft montags, erzeugt
   Grafiken + Texte für vier Wochen als Artefakt. Für automatische Veröffentlichung Secrets
   `MASTODON_*`, `TELEGRAM_*`, `BLUESKY_*` setzen und Repository-Variable `SOCIAL_POST_ENABLED=true`.
   Instagram/TikTok/Pinterest/Facebook bleiben Upload per Planungswerkzeug (fertige Dateien aus
   `social/`). — 2 h, danach ~15 Min/Monat.
7. **Newsletter** (Buttondown/MailerLite, kostenlose Stufe) an `/feed.xml` hängen:
   automatische Ausgabe „Beste Orte im Monat“ ohne eigene Texte. — 1 h.
8. **Displaywerbung erst später** (AdSense + zertifizierte CMP): erst ab etwa 30.000–50.000
   Seitenaufrufen im Monat; darunter fressen Consent, Technik und Betreuung den Ertrag.
   Der eingebaute Sponsorplatz bleibt für eingehende Anfragen (fester Preis, kein Tracking).

## Realistische Größenordnung (illustrativ, nicht garantiert)

| Monat | Seitenaufrufe | Affiliate (12 % Klicks, 1,5 % Buchung, 35 €, 80 % bestätigt) | Display | Summe |
|---|---|---|---|---|
| 3 | 1.000 | ~50 € | 0 € | ~50 € |
| 12 | 10.000 | ~505 € | ~60 € | ~565 € |
| 24 | 50.000 | ~2.500 € | ~250 € | ~2.750 € |

Das ist keine Prognose. Bei schwacher Performance (3 % Klicks, 0,5 % Buchung) bleiben aus
10.000 Aufrufen **42 €**. Bezahlter Traffic ist in beiden Fällen unwirtschaftlich
(Erlös je Besuch ~0,04–0,06 € gegenüber ~0,30 € Einkaufspreis) – **keine Suchanzeigen kaufen.**
Erste sechs Monate: mit 0–100 € rechnen. Wirkung kommt aus Inhalten und Wiederkehr, nicht aus Budget.

## Ehrliche Grenzen

* **Nicht passiv:** Freigaben, Richtlinien, Linkpflege, Monatsauswertung kosten 30–60 Min/Monat.
* **Kein Massen-SEO:** Die Seiten entstehen nur für Orte im Raster mit echten Zahlen. Tausende
  austauschbare Stadtseiten würden abgewertet und wären Zusatzrisiko.
* **Markt ist klein:** Nische „Wohnen/Überwintern nach Wohlfühlklima“ – weniger Volumen als
  Reiseplanung, aber bessere Absicht und weniger Konkurrenz.
* **Abhängigkeiten:** OSM-Kacheln (bei viel Traffic eigener Tarif), Partnerprogramm-Bedingungen,
  Datenschutz-Anforderungen. Alles im Blick behalten, nichts davon ist „einmal einrichten“.
* **Ohne Domain, Impressum und gemessene Reichweite** ist jede Monetarisierung Theorie.

## Erste 90 Tage

* **Woche 1–2:** Domain, Starter-Tarif, legal.json, Analytics-Token, Search Console.
* **Woche 3–4:** Affiliate-Programm beantragen; Social-Paket einmal von Hand durchsehen und
  zwei Beiträge pro Woche posten, um Reaktionen zu sehen.
* **Monat 2–3:** Affiliate-Programm scharf schalten, Sitemap-Indexierung beobachten, `/analytics`
  monatlich auswerten (Teilen- und Partnerklicks je 1.000 Aufrufe), Newsletter starten.
* **Monat 4+:** Erst wenn Partnerklicks und Buchungen messbar sind, über Displaywerbung,
  zweites Programm oder – ausdrücklich später – bezahlte Reichweite nachdenken.
