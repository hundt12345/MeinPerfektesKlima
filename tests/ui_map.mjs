/** Offline regression with the real vendored Leaflet (no tile/API downloads).
 * Verifies actual panes, projection, layer events and opacity while images load.
 * jsdom cannot verify raster appearance: see docs/karte.md for the visual check.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { JSDOM } from "jsdom";

const read = path => readFile(new URL(`../${path}`, import.meta.url), "utf8");
const dom = new JSDOM(await read("app/static/index.html"), {
  url: "https://klima.example/", runScripts: "outside-only", pretendToBeVisual: true,
});
const { window } = dom;
const doc = window.document;
let width = 1100, height = 700;
const host = doc.querySelector("#map");
Object.defineProperties(host, {
  clientWidth: { get: () => width }, clientHeight: { get: () => height },
});
window.SVGSVGElement.prototype.createSVGRect = () => ({});
const requests = [];
window.fetch = async url => {
  requests.push(url);
  assert.equal(url, "/api/countries", "No external API/label requests");
  return { json: async () => ({ type: "FeatureCollection", features: [] }) };
};
window.eval(await read("app/static/vendor/leaflet/leaflet.js"));
window.eval(await read("app/static/app.js") + `
  document.removeEventListener("DOMContentLoaded", boot);
  window.testMap = {
    initMap, updateOverlay, setClimateOpacity, paintPlaceLabels, paintCities,
    get map() { return map; }, get overlay() { return overlay; },
    get osm() { return osmLayer; }, get labels() { return placeLabelLayer; },
    get cities() { return cityLayer; },
    setCities(cities) { cityData = cities; },
    hideCities() { state.showCities = false; paintCities(); },
    setup() {
      META = { dataset: {}, color_stops: [[0, 0, 0, 0], [1, 255, 255, 255]] };
      GRID = { bounds: [[34, -25], [72, 45]], vmax: 365 };
    }
  };
`);
const t = window.testMap;
const labelText = () => Array.from(t.labels.getLayers(), layer => layer.getElement()?.textContent).filter(Boolean);
const cityAt = (name, x, y, pop) => {
  const ll = t.map.containerPointToLatLng([x, y]);
  return { name, lat: ll.lat, lon: ll.lng, pop, days: 100 };
};
try {
  t.setup();
  t.initMap();
  t.map.options.zoomAnimation = false;
  t.updateOverlay();
  t.setCities([
    cityAt("Nachbar", 400, 300, 2_000_000),
    cityAt("Großstadt", 400, 300, 4_000_000),
    cityAt('<img src=x onerror="alert(1)">', 650, 300, 1_500_000),
    cityAt("Kleinstadt", 550, 450, 70_000),
    cityAt("Außerhalb", -400, 200, 5_000_000),
  ]);
  t.paintPlaceLabels();
  assert.equal(t.osm._url, "https://tile.openstreetmap.org/{z}/{x}/{y}.png");
  assert.equal(doc.querySelector('meta[name="referrer"]').content, "no-referrer",
    "Page-wide privacy policy stays restrictive");
  assert.equal(t.osm.options.referrerPolicy, "strict-origin-when-cross-origin");
  assert.equal(t.map.getMaxZoom(), 19);
  assert.equal(doc.querySelectorAll(".leaflet-tile-pane img").length > 0, true);
  assert.equal([...doc.querySelectorAll(".leaflet-tile-pane img")].every(img =>
    new URL(img.src).hostname === "tile.openstreetmap.org" && !new URL(img.src).search), true);
  assert([...doc.querySelectorAll(".leaflet-tile-pane img")].every(img =>
    img.referrerPolicy === "strict-origin-when-cross-origin"),
    "Every actual tile image overrides no-referrer with an origin-only external Referer");
  assert.match(doc.querySelector(".leaflet-control-attribution").textContent, /OpenStreetMap/);
  assert.match(doc.querySelector(".leaflet-control-attribution").textContent, /GeoNames/);
  assert.equal(t.map.getPane("placeLabels").style.zIndex, "500");
  assert.equal(t.map.getPane("placeLabels").style.pointerEvents, "none");
  assert.equal(t.overlay.options.pane, "overlayPane");
  assert.equal(t.overlay.options.interactive, false);
  assert.deepEqual(labelText(), ["Großstadt", '<img src=x onerror="alert(1)">']);
  assert.equal(doc.querySelector(".city-label img"), null, "City names must be escaped");
  assert(t.labels.getLayers().every(l => l.options.interactive === false && l.options.keyboard === false));
  console.log("✓ Keyless OSM, local labels above overlay, collision priority, clipping and escaping");

  const url = t.overlay.getElement().src;
  t.setClimateOpacity(0.3);
  t.overlay.getElement().dispatchEvent(new window.Event("load"));
  assert.equal(t.overlay.getElement().style.opacity, "0.3", "Late image load preserves current opacity");
  assert.equal(t.overlay.getElement().src, url, "Opacity does not request a new PNG");
  t.updateOverlay();
  assert.equal(t.overlay.options.opacity, 0.3, "Refresh preserves opacity");
  t.setClimateOpacity(0);
  t.overlay.getElement().dispatchEvent(new window.Event("load"));
  assert.equal(t.overlay.getElement().style.opacity, "0");
  assert.equal(t.labels.getLayers().length, 0);
  t.setClimateOpacity(0.9);
  assert.equal(labelText().length, 2);
  t.setClimateOpacity(99);
  assert.equal(t.overlay.options.opacity, 0.9);
  t.setClimateOpacity("bad");
  assert.equal(t.overlay.options.opacity, 0.9);
  console.log("✓ Opacity 0–90 %, loading/refresh races, labels hidden at 0 %");

  t.map.removeLayer(t.overlay);
  assert.equal(t.labels.getLayers().length, 0, "Layer control event hides redundant labels");
  t.map.addLayer(t.overlay);
  assert.equal(labelText().length, 2);
  t.map.removeLayer(t.labels);
  t.updateOverlay();
  assert.equal(t.map.hasLayer(t.labels), false, "Refresh must not re-enable disabled labels");
  assert.equal(doc.querySelectorAll(".city-label").length, 0);
  t.map.addLayer(t.labels);
  t.hideCities();
  assert.equal(t.cities.getLayers().length, 0);
  assert.equal(labelText().length, 2, "Labels are independent of climate dots");
  console.log("✓ Real Leaflet layer-control events and independent city/label visibility");

  t.map.panBy([200, 0], { animate: false });
  assert.equal(labelText().length, 2);
  width = 420; height = 500;
  t.map.invalidateSize({ animate: false, pan: false });
  assert(labelText().length < 2, "Resize recomputes visible labels");
  t.map.setView([52.5, 11], 7, { animate: false });
  t.setCities([cityAt("Kleinstadt", 100, 100, 70_000)]);
  t.paintPlaceLabels();
  assert.deepEqual(labelText(), ["Kleinstadt"], "Higher zoom includes smaller cities");
  t.map.setZoom(19, { animate: false });
  assert.equal(t.map.getZoom(), 19);
  assert.equal(t.osm.options.maxZoom, 19);
  assert.equal(requests.length, 1, "No secondary label service requests");
  console.log("✓ Pan, mobile resize and zoom to street level");
} finally {
  t.map?.remove();
  window.close();
}
