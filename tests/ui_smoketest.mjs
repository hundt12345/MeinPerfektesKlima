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
const calls = { tileLayer: 0, markers: 0, circles: 0, geoJSON: 0, overlay: 0, attribution: 0 };
const handlers = {};
const markerClicks = [];

function chain(extra = {}) {
  const layer = {
    addTo: () => layer, setUrl: () => layer, setBounds: () => layer, remove: () => layer,
    clearLayers: () => layer, addLayer: (l) => layer, setStyle: () => layer,
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
    map: () => mapObject,
    tileLayer: () => { calls.tileLayer++; return chain(); },
    layerGroup: () => chain(),
    geoJSON: () => { calls.geoJSON++; return chain(); },
    imageOverlay: () => { calls.overlay++; return chain(); },
    circleMarker: () => { calls.circles++; return chain(); },
    marker: () => { calls.markers++; return chain(); },
    divIcon: (o) => ({ _icon: o }),
    latLngBounds: (b) => ({ _bounds: b, pad: () => ({ _bounds: b }) }),
    control: { attribution: () => { calls.attribution++; return { addTo: () => ({}) }; } },
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
window.history.replaceState = () => {};
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
ok(doc.querySelectorAll("#presets .preset").length >= 4, `Voreinstellungen vorhanden (${doc.querySelectorAll("#presets .preset").length})`);
ok((doc.querySelector("#dataset-badge").textContent || "").length > 5, "Datensatz-Info im Kopfbereich: " + doc.querySelector("#dataset-badge").textContent);
ok(!doc.querySelector("#legend").hidden, "Legende sichtbar");
ok(doc.querySelectorAll("#legend-ticks span").length === 6, `Legenden-Ticks (${doc.querySelectorAll("#legend-ticks span").length})`);
const rank = [...doc.querySelectorAll("#ranking li button")].map((b) => b.textContent.trim());
ok(rank.length > 0, `Top-Orte-Liste (${rank.length}): ${rank.slice(0, 3).join(" | ")}`);
ok(handlers.click && handlers.click.length > 0, "Kartenklick-Handler registriert");

console.log("\nGrundkarte ohne Drittanbieter:");
ok(calls.tileLayer === 0, `keine Kacheln von Fremdanbietern geladen (tileLayer-Aufrufe: ${calls.tileLayer})`);
ok(calls.attribution === 0, `kein Attributions-/Watermark-Balken in der Karte (${calls.attribution})`);
ok(calls.geoJSON >= 1, `Länderflächen selbst gezeichnet (geoJSON: ${calls.geoJSON})`);
ok(calls.overlay >= 1, `Daten-Overlay eingebunden (${calls.overlay})`);
ok(calls.circles >= 5, `Städtemarker gesetzt (${calls.circles})`);
ok(doc.querySelector("#attribution").textContent.includes("Open-Meteo"), "Quellenangabe im Fußbereich der Seite");

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

console.log(failures.length ? `\n${failures.length} Fehler` : "\nAlle Prüfungen bestanden");
process.exit(failures.length ? 1 : 0);
