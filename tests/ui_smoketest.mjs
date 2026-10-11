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

function makeLeafletStub(handlers) {
  const chain = () => {
    const layer = { addTo: () => layer, setUrl: () => layer, setBounds: () => layer, remove: () => layer };
    return layer;
  };
  const mapObject = {
    setView: () => mapObject,
    on: (evt, fn) => { (handlers[evt] ||= []).push(fn); return mapObject; },
    off: () => mapObject,
    getContainer: () => ({ append: () => {}, style: {} }),
    flyTo: () => mapObject,
    getZoom: () => 4,
    addLayer: () => mapObject,
    invalidateSize: () => mapObject,
  };
  const L = {
    map: () => mapObject,
    tileLayer: () => chain(),
    layerGroup: () => chain(),
    geoJSON: () => chain(),
    imageOverlay: () => chain(),
    latLngBounds: (b) => ({ _bounds: b }),
    control: { attribution: () => ({ addTo: () => ({}) }) },
  };
  return L;
}

const handlers = {};
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => failures.push("jsdom: " + e.message));
virtualConsole.on("error", (m) => console.error("  [browser error]", m));

const html = await (await fetch(BASE + "/")).text();
const dom = new JSDOM(html, {
  url: BASE + "/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;
window.fetch = (url, opts) => fetch(new URL(url, BASE), opts);
window.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
window.L = makeLeafletStub(handlers);
window.navigator.clipboard = { writeText: async () => {} };
window.history.replaceState = () => {};
window.L = window.L;

// Leaflet selbst wird nicht ausgeführt (braucht echtes Browser-Layout) – der
// Stub oben deckt die verwendeten Aufrufe ab. Getestet wird app.js gegen die echte API.
console.log("Lade app.js in jsdom …");
try {
  window.eval(await (await fetch(BASE + "/static/app.js")).text());
} catch (err) {
  failures.push(`app.js warf einen Fehler: ${err.message}`);
  console.log(`  ✗ app.js: ${err.message}`);
}
// app.js registriert boot() auf DOMContentLoaded – in jsdom manuell auslösen
window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));

// boot() läuft asynchron – warten, bis die Oberfläche steht
const deadline = Date.now() + 30_000;
const ready = () => window.document.querySelectorAll("#controls .ctrl").length >= 5 &&
                    window.document.querySelectorAll("#ranking li button").length > 0;
while (Date.now() < deadline && !ready()) await new Promise((r) => setTimeout(r, 250));

const doc = window.document;
console.log("\nOberfläche:");
ok(doc.querySelectorAll("#controls .ctrl").length === 5, `5 Regler aufgebaut (${doc.querySelectorAll("#controls .ctrl").length})`);
ok(doc.querySelectorAll("#presets .preset").length >= 4, `Voreinstellungen vorhanden (${doc.querySelectorAll("#presets .preset").length})`);
ok((doc.querySelector("#dataset-badge").textContent || "").includes("Rasterpunkte"), "Datensatz-Info im Kopfbereich: " + doc.querySelector("#dataset-badge").textContent);
ok(!doc.querySelector("#legend").hidden, "Legende sichtbar");
ok(doc.querySelectorAll("#legend-ticks span").length === 6, `Legenden-Ticks (${doc.querySelectorAll("#legend-ticks span").length})`);
const rank = [...doc.querySelectorAll("#ranking li button")].map((b) => b.textContent.trim());
ok(rank.length > 0, `Top-Orte-Liste (${rank.length}): ${rank.slice(0, 3).join(" | ")}`);
ok(handlers.click && handlers.click.length > 0, "Kartenklick-Handler registriert");

// Klick auf die Karte -> Detailbox
console.log("\nInteraktion:");
handlers.click[0]({ latlng: { lat: 48.85, lng: 2.35 }, containerPoint: { x: 10, y: 10 } });
const d2 = Date.now() + 20_000;
const detailReady = () => { const t = doc.querySelector("#detail").textContent || ""; return t.includes("Tage/Jahr") || t.includes("Fehler"); };
while (Date.now() < d2 && !detailReady()) await new Promise((r) => setTimeout(r, 200));
const detailText = doc.querySelector("#detail").textContent.replace(/\s+/g, " ").slice(0, 120);
ok(detailText.includes("Tage/Jahr"), "Detailbox zeigt Auswertung: " + detailText);
ok(doc.querySelectorAll("#detail .bars .bar").length === 12, "12 Monatsbalken");

// Voreinstellung anklicken -> Parameter ändern sich
const before = doc.querySelector("#controls output").textContent;
doc.querySelectorAll("#presets .preset")[1].click();
await new Promise((r) => setTimeout(r, 3000));
const after = doc.querySelector("#controls output").textContent;
ok(before !== after, `Preset ändert Werte (${before} -> ${after})`);

// Moduswechsel "Monat"
doc.querySelector("#mode-month").click();
await new Promise((r) => setTimeout(r, 3500));
ok(!doc.querySelector("#month-picker").hidden, "Monatsauswahl sichtbar");
ok((doc.querySelector("#legend-title").textContent || "").includes("Tage im "), "Legende auf Monatsmodus: " + doc.querySelector("#legend-title").textContent);

console.log(failures.length ? `\n${failures.length} Fehler` : "\nAlle Prüfungen bestanden");
process.exit(failures.length ? 1 : 0);
