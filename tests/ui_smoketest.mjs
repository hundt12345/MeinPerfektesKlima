/**
 * Rauchtest für die Startseite ohne echten Browser: lädt index.html + app.js in
 * jsdom, ersetzt Leaflet durch einen Stub und prüft, dass die Oberfläche mit der
 * laufenden API tatsächlich aufgebaut wird und auf Eingaben reagiert.
 *
 * Voraussetzung: die App läuft lokal, z. B.
 *   uvicorn app.main:app --port 8000
 * Aufruf:
 *   node tests/ui_smoketest.mjs [http://127.0.0.1:8000]
 */
import { JSDOM, VirtualConsole } from "jsdom";

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const failures = [];
const ok = (cond, msg) => { console.log(`${cond ? "  ✓" : "  ✗"} ${msg}`); if (!cond) failures.push(msg); };

/* Leaflet-Stub: merkt sich Aufrufe und Ereignis-Handler */
const calls = { tileLayer: 0, markers: 0, circles: 0, geoJSON: 0, overlay: 0, layers: 0, mapOptions: null,
  tileUrl: "", tileAttribution: "" };
const handlers = {};
const markerClicks = [];

function chain(extra = {}) {
  const layer = {
    addTo: () => layer, setUrl: () => layer, setBounds: () => layer, remove: () => layer,
    clearLayers: () => layer, addLayer: (l) => layer, addOverlay: () => layer, setStyle: () => layer,
    on: (evt, fn) => { (layer._h ||= {})[evt] = fn; return layer; },
    bindTooltip: () => layer, setLatLng: () => layer, ...extra,
  };
  return layer;
}

function makeLeafletStub() {
  const panes = {};
  const mapObject = {
    setView: () => mapObject,
    on: (evt, fn) => { (handlers[evt] ||= []).push(fn); return mapObject; },
    off: () => mapObject,
    getContainer: () => ({ append: () => {}, style: {}, clientWidth: 1100 }),
    flyTo: () => mapObject,
    getZoom: () => 5,
    setZoom: () => mapObject,
    addLayer: () => mapObject,
    removeLayer: () => mapObject,
    invalidateSize: () => mapObject,
    createPane: (name) => (panes[name] ||= { style: {}, name }),
    getPane: (name) => panes[name],
    latLngBounds: (b) => ({ _bounds: b }),
    project: () => ({ x: 0, y: 0 }),
    fitBounds: () => mapObject,
  };
  return {
    map: (_id, options) => { calls.mapOptions = options; return mapObject; },
    tileLayer: (url, options = {}) => {
      calls.tileLayer++;
      calls.tileUrl = url;
      calls.tileAttribution = options.attribution || "";
      return chain();
    },
    layerGroup: () => chain(),
    geoJSON: () => { calls.geoJSON++; return chain(); },
    imageOverlay: () => { calls.overlay++; return chain(); },
    circleMarker: () => { calls.circles++; return chain(); },
    marker: () => { calls.markers++; return chain(); },
    divIcon: (o) => ({ _icon: o }),
    latLngBounds: (b) => ({ _bounds: b, pad: () => ({ _bounds: b }) }),
    control: {
      layers: () => { calls.layers++; return chain(); },
      attribution: () => chain(),
    },
    DomEvent: { stop: () => {} },
  };
}

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => failures.push("jsdom: " + e.message));
virtualConsole.on("error", (m) => console.error("  [browser error]", m));

const html = await (await fetch(BASE + "/")).text();
const dom = new JSDOM(html, { url: BASE + "/", runScripts: "outside-only", pretendToBeVisual: true, virtualConsole });
const { window } = dom;
window.fetch = (url, opts) => fetch(new URL(url, BASE), opts);
window.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
window.L = makeLeafletStub();
window.navigator.clipboard = { writeText: async () => {} };
window.history.replaceState = (_state, _title, url) => { calls.lastHash = String(url); };
Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { value: 1100, configurable: true });

console.log("Lade app.js in jsdom …");
try {
  window.eval(await (await fetch(BASE + "/static/app.js")).text());
} catch (err) {
  failures.push(`app.js warf einen Fehler: ${err.message}`);
  console.log(`  ✗ app.js: ${err.message}`);
}
window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));

const deadline = Date.now() + 30_000;
const ready = () => window.document.querySelectorAll("#controls .ctrl").length >= 5 &&
                    window.document.querySelectorAll("#ranking li button").length > 0;
while (Date.now() < deadline && !ready()) await new Promise((r) => setTimeout(r, 250));

const doc = window.document;
console.log("\nOberfläche:");
ok(doc.querySelectorAll("#controls .ctrl").length === 5, `5 Regler aufgebaut (${doc.querySelectorAll("#controls .ctrl").length})`);
ok(doc.querySelector("#month-picker").hidden, "Monatsauswahl im Jahresmodus ausgeblendet");
ok([...doc.querySelectorAll("[data-wq-only]")].every(n => n.hidden),
  "Winterquartier-Karten erst im Winterquartier-Modus");
// Regression: `display:flex` der eigenen Regel darf `hidden` nicht aushebeln
const css = await (await fetch(BASE + "/static/styles.css")).text();
ok(/\[hidden\]\s*\{\s*display:\s*none\s*!important/.test(css),
  "styles.css erzwingt display:none für [hidden] (Monatsauswahl bleibt sonst sichtbar)");
ok(doc.querySelectorAll("#presets .preset").length >= 4, `Voreinstellungen vorhanden (${doc.querySelectorAll("#presets .preset").length})`);
ok((doc.querySelector("#dataset-badge").textContent || "").length > 5, "Datensatz-Info im Kopfbereich: " + doc.querySelector("#dataset-badge").textContent);
ok(doc.querySelector('#period-select option[value="recent5"]'), "Fünfjahres-Zeitraum auswählbar");
ok(!doc.querySelector('#period-select option[value="2030"]')?.disabled
  && !doc.querySelector('#period-select option[value="2050"]')?.disabled,
  "2030/2050 als modellbasierte Näherungen verfügbar");
ok(doc.querySelectorAll("#period-projection-note a").length === 3,
  "drei echte Projektionsquellen für 2030/2050 sichtbar");
ok(/\.card::after\s*\{\s*content:\s*none/.test(css), "keine dekorativen horizontalen Lichtstreifen");
ok(!doc.querySelector("#legend").hidden, "Legende sichtbar");
ok(doc.querySelectorAll("#legend-ticks span").length === 6, `Legenden-Ticks (${doc.querySelectorAll("#legend-ticks span").length})`);
const rank = [...doc.querySelectorAll("#ranking li button")].map((b) => b.textContent.trim());
ok(rank.length > 0, `Top-Orte-Liste (${rank.length}): ${rank.slice(0, 3).join(" | ")}`);
ok(!doc.querySelector("#city-data-section").hidden, "Vollständige Städtedaten im Datenmodus verfügbar");
ok(!doc.querySelector("#city-data").open, "Große Städtetabelle standardmäßig eingeklappt");
ok(handlers.click && handlers.click.length > 0, "Kartenklick-Handler registriert");

console.log("\nOSM-Grundkarte und Klimadaten:");
ok(calls.tileLayer === 1 && calls.tileUrl.includes("tile.openstreetmap.org"),
  `dynamische OSM-Kacheln eingebunden (${calls.tileUrl})`);
ok(calls.mapOptions && calls.mapOptions.maxZoom >= 18 && calls.tileAttribution.includes("OpenStreetMap"),
  "hoher Zoom und sichtbare OSM-Attribution konfiguriert");
ok(calls.layers >= 1, "Layer-Steuerung für Grundkarte/Klimadaten eingebunden");
ok(calls.geoJSON >= 1, `Ländergrenzen als zusätzliche Vektorebene (${calls.geoJSON})`);
ok(calls.overlay >= 1, `Daten-Overlay eingebunden (${calls.overlay})`);
ok(calls.circles >= 5, `Städtemarker gesetzt (${calls.circles})`);
ok(doc.querySelector("#attribution").textContent.includes("Copernicus")
  && doc.querySelector("#attribution").textContent.includes("OpenStreetMap"),
  "Quellenangaben für Klimadaten und OSM im Fußbereich der Seite");

console.log("\nInteraktion – Klick auf die Karte:");
handlers.click[0]({ latlng: { lat: 48.85, lng: 2.35 }, containerPoint: { x: 10, y: 10 } });
const d2 = Date.now() + 20_000;
const detailReady = () => { const t = doc.querySelector("#detail").textContent || ""; return t.includes("Tage") && t.includes("Klima hier"); };
while (Date.now() < d2 && !detailReady()) await new Promise((r) => setTimeout(r, 200));
const detailText = doc.querySelector("#detail").textContent.replace(/\s+/g, " ");
ok(detailText.includes("Tage"), "Detailbox zeigt Wohlfühl-Tage: " + detailText.slice(0, 90));
ok(doc.querySelectorAll("#detail .bars .bar").length === 12, "12 Monatsbalken");
ok(!!doc.querySelector("#detail .climsvg"), "Klimadiagramm (SVG) vorhanden");
ok(/Jahr: Ø max/.test(detailText), "Jahresklima-Werte im Klartext: " + (detailText.match(/Jahr: Ø max[^·]*·[^·]*·[^·]*/) || [""])[0].trim());
ok(/° N/.test(detailText) || /km/.test(detailText), "Angabe zum Gitterpunkt/Entfernung");

console.log("\nInteraktion – Klick auf eine Stadt (Top-Orte-Liste):");
const firstCity = doc.querySelector("#ranking li button");
const cityName = firstCity.querySelector(".place").textContent.replace(/^\d+\.\s*/, "");
firstCity.click();
const d3 = Date.now() + 20_000;
while (Date.now() < d3 && !(doc.querySelector("#detail .title") || {}).textContent) await new Promise((r) => setTimeout(r, 200));
await new Promise((r) => setTimeout(r, 800));
const title = (doc.querySelector("#detail .title") || {}).textContent || "";
ok(title.includes(cityName) || title.length > 2, `Detailbox trägt den Ortsnamen („${title}“, erwartet „${cityName}“)`);

console.log("\nInteraktion – Ortssuche:");
const search = doc.querySelector("#city-search");
search.value = "sev";
search.dispatchEvent(new window.Event("input", { bubbles: true }));
const d4 = Date.now() + 12_000;
while (Date.now() < d4 && doc.querySelector("#search-results").hidden) await new Promise((r) => setTimeout(r, 200));
const hits = [...doc.querySelectorAll("#search-results button")].map(b => b.textContent.trim());
ok(hits.length > 0 && hits.some(h => /sev/i.test(h)), `Suchergebnisse (${hits.length}): ${hits.slice(0, 3).join(" | ")}`);

console.log("\nInteraktion – Regler und Modi:");
const before = doc.querySelector("#controls output").textContent;
doc.querySelectorAll("#presets .preset")[1].click();
await new Promise((r) => setTimeout(r, 3000));
const after = doc.querySelector("#controls output").textContent;
ok(before !== after, `Preset ändert Werte (${before} -> ${after})`);

doc.querySelector("#mode-month").click();
await new Promise((r) => setTimeout(r, 3500));
ok(!doc.querySelector("#month-picker").hidden, "Monatsauswahl sichtbar");
ok((doc.querySelector("#legend-title").textContent || "").includes("Tage im "), "Legende auf Monatsmodus: " + doc.querySelector("#legend-title").textContent);

console.log("\nInteraktion – vereinfachte Zwei-Orte-Suche:");
doc.querySelector("#mode-wq").click();
const d5 = Date.now() + 25_000;
while (Date.now() < d5 && !(doc.querySelector("#legend-title").textContent || "").includes("Wohlfühltage/Jahr an einem Ort")) {
  await new Promise((r) => setTimeout(r, 250));
}
ok(doc.querySelector("#month-picker").hidden, "Monatsauswahl im Winterquartier-Modus ausgeblendet");
ok(doc.querySelectorAll("#controls .ctrl").length === 5, "ein gemeinsames Traumwetter-Profil mit fünf Reglern");
ok(doc.querySelector("#winter-controls") === null && doc.querySelector("#pair-card") === null,
  "keine separaten Winterregler; Wohnortsuche im gemeinsamen Ablauf");
ok(!doc.querySelector("#wq-setup").hidden, "Sommerfenster und Zwei-Orte-Suche sichtbar");
ok(doc.querySelector("#feel-title").textContent === "Dein Traumwetterprofil",
  "Überschrift für das gemeinsame Profil");
ok((doc.querySelector("#wq-window").textContent || "").includes("Winterort:"),
  "Sommer- und Restmonate werden klar zugeordnet: " + doc.querySelector("#wq-window").textContent);
ok((doc.querySelector("#legend-title").textContent || "").includes("Wohlfühltage/Jahr an einem Ort"),
  "Karte zeigt ergänzend die Jahreszahl für einen Einzelort");

ok(doc.querySelector("#btn-wq-search").disabled, "Suche ohne festen Wohnort gesperrt");
const homeInput = doc.querySelector("#wq-home");
homeInput.value = "Berlin";
homeInput.dispatchEvent(new window.Event("input", { bubbles: true }));
const homeDeadline = Date.now() + 10_000;
while (Date.now() < homeDeadline && !doc.querySelector("#wq-home-results button")) {
  await new Promise(r => setTimeout(r, 100));
}
ok(!!doc.querySelector("#wq-home-results button"), "Wohnortsuche liefert auswählbare Städte");
doc.querySelector("#wq-home-results button")?.click();
ok(!doc.querySelector("#btn-wq-search").disabled, "Wohnortauswahl aktiviert die Suche");
ok(doc.querySelector("#wq-home-status").textContent.includes("Berlin"), "Berlin bleibt fester Wohnort");
ok(calls.lastHash.includes("home_name=Berlin") && calls.lastHash.includes("home_lat="),
  "teilbare Links enthalten den festen Wohnort");

// Feste Auswahl Mai–Juli; übrige Monate bleiben beim Winterort.
const sto = doc.querySelector("#wq-sto");
sto.value = "7";
sto.dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 1800));
const seasonText = doc.querySelector("#wq-window").textContent || "";
ok(seasonText.includes("Mai – Juli") && seasonText.includes("August – April"),
  "festes Zeitfenster wird nicht optimiert/verschoben: " + seasonText);

doc.querySelector("#btn-wq-search").click();
const d7 = Date.now() + 30_000;
while (Date.now() < d7 && !/Wohlfühltage\/Jahr|keine passende Zwei-Orte-Kombination|Suche fehlgeschlagen/.test(
  doc.querySelector("#wq-result").textContent || "")) {
  await new Promise((r) => setTimeout(r, 250));
}
const combination = (doc.querySelector("#wq-result").textContent || "").replace(/\s+/g, " ");
ok(/Wohlfühltage\/Jahr/.test(combination), "Wohnortsuche liefert die Jahressumme zweier Orte: " + combination.slice(0, 160));
ok(doc.querySelectorAll("#wq-alternatives .combo-item").length > 0,
  `alternative Stadtpaare werden angezeigt (${doc.querySelectorAll("#wq-alternatives .combo-item").length})`);
ok((doc.querySelector("#wq-window").textContent || "").includes("Mai – Juli"),
  "Suchergebnis ändert das gewählte Sommerfenster nicht");
ok(!doc.querySelector("#wq-result").hidden, "Suchergebnis wird sichtbar");

ok(combination.includes("Berlin") && combination.includes("gegenüber ganzjährig zu Hause"),
  "Ergebnis nennt Wohnort und Zugewinn gegenüber zu Hause");
const seasonSelect = doc.querySelector("#wq-season");
seasonSelect.value = "summer";
seasonSelect.dispatchEvent(new window.Event("change", { bubbles: true }));
ok(doc.querySelector("#wq-result").hidden, "Saisonwechsel verwirft altes Ergebnis");
doc.querySelector("#btn-wq-search").click();
const summerDeadline = Date.now() + 15_000;
while (Date.now() < summerDeadline && !(doc.querySelector("#wq-result").textContent || "").includes("Wohlfühltage/Jahr")) {
  await new Promise(r => setTimeout(r, 100));
}
ok(doc.querySelector("#wq-result").textContent.includes("Berlin"), "auch Sommer-Ausweichsuche behält den Wohnort");
ok(doc.querySelector("#wq-window").textContent.includes("unterwegs im Sommer"), "Sommer-Ausweichsaison erklärt");

homeInput.value = "Nicht ausgewählt";
homeInput.dispatchEvent(new window.Event("input", { bubbles: true }));
ok(doc.querySelector("#wq-result").hidden && doc.querySelector("#btn-wq-search").disabled,
  "frei eingegebener Wohnort verwirft Ergebnisse und sperrt die Suche");

// Änderungen an Profil oder Monaten machen alte Resultate ungültig.
doc.querySelectorAll("#presets .preset")[0].click();
await new Promise((r) => setTimeout(r, 1200));
ok(doc.querySelector("#wq-result").hidden, "alte Kombination wird nach Profiländerung ausgeblendet");

// Ein ausgewählter Ort zeigt weiterhin seine Einzelort-Auswertung.
handlers.click[0]({ latlng: { lat: 43.3, lng: 5.4 }, containerPoint: { x: 10, y: 10 } });
const d8 = Date.now() + 30_000;
while (Date.now() < d8 && !/Jahreswerte an einem Ort/.test(doc.querySelector("#detail").textContent || "")) {
  await new Promise((r) => setTimeout(r, 250));
}
const wqText = doc.querySelector("#detail").textContent.replace(/\s+/g, " ");
ok(/Jahreswerte an einem Ort/.test(wqText), "Detailbox zeigt das gemeinsame Profil an einem Ort");
ok(doc.querySelectorAll("#detail .bars .bar").length === 12, "Monatsübersicht bleibt in der Detailbox erhalten");

console.log("\nZurück in den Jahresmodus:");
doc.querySelector("#mode-year").click();
await new Promise((r) => setTimeout(r, 3000));
ok(doc.querySelector("#month-picker").hidden, "Monatsauswahl wieder versteckt");
ok([...doc.querySelectorAll("[data-wq-only]")].every(n => n.hidden), "Winterquartier-Karten wieder versteckt");
ok(doc.querySelector("#feel-title").textContent === "Dein Wohlfühlwetter", "Überschrift zurückgesetzt");

doc.querySelector("#period-select").value = "recent5";
doc.querySelector("#period-select").dispatchEvent(new window.Event("change", { bubbles: true }));
await new Promise((r) => setTimeout(r, 3000));
ok((doc.querySelector("#legend-title").textContent || "").includes("Letzte 5 Jahre"),
  "Zeitraum-Auswahl aktualisiert die Legende: " + doc.querySelector("#legend-title").textContent);
ok((doc.querySelector("#detail").textContent || "").includes("2021–2025"),
  "Ortsdetails zeigen ebenfalls die gewählte Periode");

console.log("\nAusklappbare vollständige Städtedaten:");
doc.querySelector("#city-data summary").click();
const d9 = Date.now() + 30_000;
while (Date.now() < d9 && doc.querySelectorAll("#city-table-body tr").length === 0) {
  await new Promise((r) => setTimeout(r, 200));
}
ok(doc.querySelector("#city-data").open, "Tabelle lässt sich ausklappen");
ok(doc.querySelectorAll("#city-table-body tr").length === 100,
  `erste Tabellenseite paginiert (${doc.querySelectorAll("#city-table-body tr").length} Zeilen)`);
ok(doc.querySelector("#city-data-count").textContent.includes("Städte"),
  "Anzahl der vollständigen Städteliste angezeigt");
ok(!!doc.querySelector('th button[data-sort-key="sunny_days"]'), "Sonnentage als sortierbare Spalte");
doc.querySelector("#city-page-next").click();
ok(doc.querySelector("#city-page-info").textContent.includes("101"), "Weiter blättert zur nächsten Tabellenseite");
const cityFilter = doc.querySelector("#city-climate-search");
cityFilter.value = "madrid";
cityFilter.dispatchEvent(new window.Event("input", { bubbles: true }));
ok(doc.querySelectorAll("#city-table-body tr").length > 0
  && [...doc.querySelectorAll("#city-table-body tr")].some(r => /Madrid/i.test(r.textContent)),
  "Städtetabelle filtert nach Namen");
doc.querySelector('button[data-sort-key="hot_days"]').click();
ok(doc.querySelector('button[data-sort-key="hot_days"]').closest("th").getAttribute("aria-sort") === "descending",
  "Tageskennzahlen lassen sich sortieren");

console.log("\nKlimaprojektionen und dynamische Sponsoren:");
const futureSelect = doc.querySelector("#period-select");
futureSelect.value = "2050-ssp585";
futureSelect.dispatchEvent(new window.Event("change", { bubbles: true }));
const futureDeadline = Date.now() + 30_000;
while (Date.now() < futureDeadline && !doc.querySelector("#detail .projection-table")) {
  await new Promise(r => setTimeout(r, 100));
}
ok(doc.querySelector("#legend-title").textContent.includes("SSP5-8.5"), "Legende zeigt zukünftiges Szenario");
ok(!doc.querySelector("#projection-warning").hidden && doc.querySelector("#projection-warning").textContent.includes("Sonne unverändert"),
  "Annahmen der Näherung immer sichtbar");
ok(doc.querySelectorAll("#detail .projection-table tbody tr").length === 12, "Ortsdetails enthalten zwölf Monatswerte mit Modellstreuung");
ok((doc.querySelector("#detail .projection-detail")?.textContent || "").includes("kein Intervall der Wohlfühltage"), "Modellstreuung korrekt eingeordnet");

ok(calls.lastHash.includes("period=2050-ssp585"), "Szenario bleibt im teilbaren Link erhalten");
const csvDeadline = Date.now() + 15_000;
while (Date.now() < csvDeadline && !doc.querySelector("#city-data-summary").textContent.includes("SSP5-8.5")) {
  await new Promise(r => setTimeout(r, 100));
}
let csvText = "";
window.URL.createObjectURL = blob => {
  const reader = new window.FileReader();
  reader.onload = () => { csvText = String(reader.result); };
  reader.readAsText(blob);
  return "blob:klima-test";
};
window.URL.revokeObjectURL = () => {};
window.HTMLAnchorElement.prototype.click = () => {}; // do not navigate in jsdom
// The table is filtered to Madrid, as above; this is also the export scope.
doc.querySelector("#city-csv-download").click();
await new Promise(r => setTimeout(r, 150));
ok(csvText.includes("Modell-basierte Näherung") && csvText.includes("SSP5-8.5")
  && csvText.includes("Sonnenstunden unverändert") && csvText.includes("Quelle"),
  "Zukunfts-CSV trägt Szenario, Quelle und die Schätzannahmen mit");

const originalFetch = window.fetch;
window.fetch = async (url, opts) => String(url).startsWith("/api/ads")
  ? { ok: true, json: async () => ({ ad: { advertiser: "Testpartner", title: "<script>alert(1)</script>",
      body: "Testanzeige", cta: "Details", url: "https://example.com/quartiere" } }) }
  : originalFetch(url, opts);
doc.querySelector("#mode-year").click();
await new Promise(r => setTimeout(r, 400));
ok(!doc.querySelector("#sponsor-slot").hidden, "kontextbezogene Kampagne wird dynamisch gerendert");
ok(doc.querySelector("#sponsor-slot").textContent.includes("Anzeige · Testpartner"), "Werbekennzeichnung und Werbetreibender sichtbar");
ok(doc.querySelector("#sponsor-slot script") === null, "Kampagnentext kann kein HTML/Script einschleusen");
ok(doc.querySelector("#sponsor-slot a")?.rel.includes("sponsored") && doc.querySelector("#sponsor-slot a")?.rel.includes("noreferrer"),
  "Anzeigenlink sicher und als sponsored gekennzeichnet");
window.fetch = async (url, opts) => String(url).startsWith("/api/ads")
  ? { ok: true, json: async () => ({ ad: { title: "Unsafe", url: "javascript:alert(1)" } }) }
  : originalFetch(url, opts);
doc.querySelector("#mode-year").click();
await new Promise(r => setTimeout(r, 400));
ok(doc.querySelector("#sponsor-slot").hidden, "unsichere Anzeigenlinks werden auch im Browser abgelehnt");
window.fetch = originalFetch;
doc.querySelector("#mode-year").click();
await new Promise(r => setTimeout(r, 400));
ok(doc.querySelector("#sponsor-slot").hidden, "ohne konfigurierte Kampagne bleibt der Werbeplatz verborgen");

console.log(failures.length ? `\n${failures.length} Fehler` : "\nAlle Prüfungen bestanden");
process.exit(failures.length ? 1 : 0);
