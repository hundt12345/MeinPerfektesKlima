/* Mein perfektes Klima – Europa
   Karte + Bedienung (Vanilla JS, Leaflet).

   Die Grundkarte wird selbst gezeichnet (Länderflächen aus Natural Earth) – es
   werden bewusst **keine** Kacheln von Drittanbietern geladen: kein Watermark,
   keine Attribution im Kartenbild, keine externe Abhängigkeit zur Laufzeit.
*/
"use strict";

const state = {
  mode: "year",              // "year" | "month" | "wq" (Winterquartier)
  month: 7,                  // 1..12
  params: { tmax_min: 18, tmax_max: 30, tmin_min: 5, tmin_max: 20, tdew_max: 18, sun_min: 3, prec_max: 99 },
  winter: { tmax_min: 14, tmax_max: 26, tmin_min: 6, tmin_max: 18, tdew_max: 17, sun_min: 4, prec_max: 99 },
  wq: {                      // Winterquartier-Modus
    view: "combo",           // "combo" | "summer" | "winter" – was die Karte zeigt
    sfrom: 5, sto: 9,        // Sommerfenster (1..12); Winter = die übrigen Monate
  },
  pair: { role: "summer", maxKm: 0, data: null },   // Gegenstück-Suche
  fixedScale: false,
  showCities: true,
  selected: null,            // {lat, lon, name, country, report}
};

let META = null;             // /api/meta
let WQ_BOUNDS = {};          // META.winterquarters (Sommerfenster, Saisonlänge)
let GRID = null;             // letzte /api/map-Antwort
let map = null, overlay = null, landLayer = null, borderLayer = null;
let cityLayer = null, cityData = [], markerIndex = new Map();
let hoverTip = null, marker = null;
let seq = 0, hoverQueued = false, refreshTimer = null;

const monthNames = ["Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember"];
const monthLetters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

/* ------------------------------------------------------------------ Helfer */
const $ = (sel) => document.querySelector(sel);

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) node.append(kid);
  return node;
}

const num = (v, digits = 0) => Number(v).toLocaleString("de-DE", {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});
function fmtDays(v) { return num(Math.round(v)); }
function fmtTemp(v, digits = 1) {
  const r = Math.round(v * (digits === 0 ? 1 : 2)) / (digits === 0 ? 1 : 2);
  return `${r > 0 ? "" : r < 0 ? "−" : ""}${Math.abs(r).toLocaleString("de-DE", {
    minimumFractionDigits: digits, maximumFractionDigits: digits })} °C`;
}
function activeMonth() { return state.mode === "month" ? state.month : null; }
function isWinterquarters() { return state.mode === "wq"; }

/** Einheit der Kartenwerte – am liebsten die des Servers (Legende/Hover). */
function monthUnit() {
  if (GRID && GRID.unit_label) return GRID.unit_label;
  if (isWinterquarters()) return "Tage/Jahr mit Wohlfühlklima";
  return state.mode === "month" ? `Tage im ${monthNames[state.month - 1]}` : "Tage pro Jahr";
}

/**
 * Winterquartier-Parameter in eine Abfrage schreiben.
 * Die Wetterparameter (`tmax_min` …) gelten dabei als **Sommer**-Profil,
 * `w`+Name als Winterquartier-Profil; `sfrom`/`sto` sind das Sommerfenster.
 */
function wqQuery(q = new URLSearchParams()) {
  q.set("view", "wq");
  q.set("wview", state.wq.view);
  q.set("sfrom", state.wq.sfrom);
  q.set("sto", state.wq.sto);
  for (const [k, v] of Object.entries(state.params)) q.set(k, v);
  for (const [k, v] of Object.entries(state.winter)) q.set("w" + k, v);
  return q;
}

/** Filterparameter der aktuellen Ansicht – für Karte, Orte, Listen und Suche. */
function filterQuery(q = new URLSearchParams()) {
  if (isWinterquarters()) return wqQuery(q);
  for (const [k, v] of Object.entries(state.params)) q.set(k, v);
  q.set("month", activeMonth() || "all");
  return q;
}

function queryString() {
  const p = filterQuery();
  p.set("width", currentWidth());
  if (state.fixedScale) p.set("fixed", "true");
  return p.toString();
}

/** Monate des Sommerfensters (1-basiert, Jahreswechsel inklusive). */
function summerMonths() {
  const out = [];
  let m = state.wq.sfrom;
  while (out.length < 12) {
    out.push(m);
    if (m === state.wq.sto) break;
    m = (m % 12) + 1;
  }
  return out;
}

/** „Mai – September“ bzw. „Januar, Februar, März“ für eine Monatsliste. */
function monthsLabel(list) {
  if (!list || !list.length) return "ganzes Jahr";
  if (list.length === 1) return monthNames[list[0] - 1];
  const sorted = [...list].sort((a, b) => a - b);
  const starts = sorted.filter((m) => !list.includes(((m + 10) % 12) + 1));
  if (starts.length === 1 && sorted.length < 12) {
    const start = starts[0];
    const end = list[(list.indexOf(start) + list.length - 1) % list.length];
    return `${monthNames[start - 1]} – ${monthNames[end - 1]}`;
  }
  return sorted.map((m) => monthNames[m - 1]).join(", ");
}

function winterMonths() {
  const summer = summerMonths();
  const rest = [];
  for (let m = 1; m <= 12; m++) if (!summer.includes(m)) rest.push(m);
  return rest;
}

function currentWidth() {
  const w = $(".mapwrap").clientWidth || 1000;
  // Das Feld wird serverseitig auf 640 px interpoliert und dann skaliert:
  // größere Breiten kosten nur Rechenzeit, bringen aber kaum Schärfe.
  return Math.max(420, Math.min(1200, Math.round(w)));
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, ms);
}

async function getJSON(url) {
  const r = await fetch(url);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = body.detail || body;
    const text = typeof detail === "string"
      ? detail
      : ((detail.reasons && detail.reasons[0]) || body.error || r.statusText);
    const err = new Error(text);
    err.status = r.status;
    err.payload = detail;
    throw err;
  }
  return body;
}

/* Farbskala der Karte (aus META.color_stops) – auch für Städtemarker benutzt */
function colorFor(fraction) {
  const stops = (META && META.color_stops) || [[0, 16, 38, 92], [1, 200, 40, 30]];
  const f = Math.max(0, Math.min(1, fraction));
  for (let i = 1; i < stops.length; i++) {
    if (f <= stops[i][0]) {
      const [f0, r0, g0, b0] = stops[i - 1], [f1, r1, g1, b1] = stops[i];
      const t = f1 === f0 ? 0 : (f - f0) / (f1 - f0);
      return `rgb(${Math.round(r0 + (r1 - r0) * t)},${Math.round(g0 + (g1 - g0) * t)},${Math.round(b0 + (b1 - b0) * t)})`;
    }
  }
  const last = stops[stops.length - 1];
  return `rgb(${last[1]},${last[2]},${last[3]})`;
}

/* Temperaturfarbe für das Klimadiagramm */
function tempColor(t) {
  const stops = [[-20, [120, 170, 235]], [-5, [150, 200, 240]], [5, [120, 205, 200]],
    [15, [190, 220, 120]], [22, [245, 190, 90]], [30, [235, 120, 60]], [40, [190, 45, 35]]];
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
      const k = (t - t0) / (t1 - t0);
      return `rgb(${c0.map((v, j) => Math.round(v + (c1[j] - v) * k)).join(",")})`;
    }
  }
  return `rgb(190,45,35)`;
}

/* ------------------------------------------------------------------- Karte */
function initMap() {
  const bbox = (META.dataset && META.dataset.bbox) || { lat_min: 34, lat_max: 72, lon_min: -25, lon_max: 45 };
  map = L.map("map", {
    zoomControl: true, minZoom: 3, maxZoom: 8, worldCopyJump: false,
    attributionControl: false,                 // kein Attributions-/Watermark-Balken im Kartenbild
    maxBounds: L.latLngBounds([[bbox.lat_min - 6, bbox.lon_min - 8], [bbox.lat_max + 4, bbox.lon_max + 8]]),
    maxBoundsViscosity: 0.7,
  }).setView([52.5, 11], 4);

  // Ebenen-Reihenfolge: Landfläche < Datentoverlay < Grenzen < Städtemarker
  const landPane = map.createPane("land"); landPane.style.zIndex = 350;
  const borderPane = map.createPane("borders"); borderPane.style.zIndex = 450;
  map.createPane("cities").style.zIndex = 550;

  fetch("/api/countries").then(r => r.json()).then(geo => {
    landLayer = L.geoJSON(geo, {
      pane: "land", interactive: false,
      style: { stroke: false, fillColor: "#16283c", fillOpacity: 1 },
    }).addTo(map);
    borderLayer = L.geoJSON(geo, {
      pane: "borders", interactive: false,
      style: { color: "#8fa9c4", weight: 0.8, opacity: 0.55, fill: false },
    }).addTo(map);
  }).catch(() => {});

  cityLayer = L.layerGroup([], { pane: "cities" }).addTo(map);

  hoverTip = el("div", { class: "leaflet-tooltip klima", style: "position:absolute;display:none;pointer-events:none;z-index:800" });
  map.getContainer().append(hoverTip);

  map.on("mousemove", onHover);
  map.on("mouseout", () => { hoverTip.style.display = "none"; });
  map.on("click", (e) => selectPlace(e.latlng.lat, e.latlng.lng));
  map.on("zoomend", () => paintCities());
  map.on("moveend zoomend", () => {
    const w = currentWidth();
    if (overlay && GRID && GRID.width && Math.abs(w - GRID.width) > 160) refresh();
  });
  window.addEventListener("resize", () => { if (overlay && GRID) overlay.setBounds(bounds()); });
}

function bounds() {
  const b = GRID ? GRID.bounds : [[34, -25], [72, 45]];
  return L.latLngBounds([[b[0][0], b[0][1]], [b[1][0], b[1][1]]]);
}

function updateOverlay() {
  const url = "/api/map.png?" + queryString();
  if (!overlay) {
    overlay = L.imageOverlay(url, bounds(), {
      opacity: 1, interactive: false, className: "klima-overlay", pane: "overlayPane",
    }).addTo(map);
  } else {
    overlay.setUrl(url);
    overlay.setBounds(bounds());
  }
}

/* ------------------------------------------------------------- Städtemarker */
function cityZoomLimit() {
  const z = map ? map.getZoom() : 4;
  if (z <= 3) return 3_000_000;
  if (z === 4) return 900_000;
  if (z === 5) return 350_000;
  if (z === 6) return 120_000;
  return 45_000;
}

async function loadCities() {
  try {
    const q = filterQuery(new URLSearchParams({ limit: 900 }));
    const data = await getJSON("/api/cities/map?" + q);
    cityData = data.cities || [];
    paintCities();
  } catch { cityData = []; }
}

function paintCities() {
  if (!cityLayer || !map) return;
  cityLayer.clearLayers();
  if (!state.showCities || !cityData.length) return;
  const vmax = Math.max(1, (GRID && GRID.vmax) || 1);
  const limit = cityZoomLimit();
  const zoom = map.getZoom();
  const unit = isWinterquarters() ? "Tage/Jahr (Sommer + Winter)"
    : (state.mode === "month" ? `Tage im ${monthNames[state.month - 1]}` : "Tage/Jahr");
  let added = 0;
  for (const c of cityData) {
    if (c.pop < limit || added >= 420) continue;
    added++;
    const dot = L.circleMarker([c.lat, c.lon], {
      pane: "cities", radius: c.pop > 1_000_000 ? 5 : 3.6,
      color: "#0b1420", weight: 1.2,
      fillColor: colorFor(c.days / vmax), fillOpacity: 0.95,
      className: "city-marker",
    });
    dot.on("click", (e) => {
      if (e && e.originalEvent) e.originalEvent.stopPropagation();
      selectPlace(c.lat, c.lon, c.name, c.country);
    });
    dot.bindTooltip(
      `<strong>${c.name}</strong>${c.country ? ` · ${c.country}` : ""}<br>${fmtDays(c.days)} ${unit}`,
      { direction: "top", offset: [0, -4], className: "klima city-tip", opacity: 1 });
    cityLayer.addLayer(dot);

    // Dauerhafte Beschriftung nur für große Orte (sonst wird die Karte unleserlich)
    const wantLabel = c.pop > 2_000_000 || (zoom >= 5 && c.pop > 900_000) || (zoom >= 6 && c.pop > 300_000);
    if (wantLabel) {
      cityLayer.addLayer(L.marker([c.lat, c.lon], {
        pane: "cities", interactive: false, keyboard: false,
        icon: L.divIcon({ className: "city-label", html: `<span>${c.name}</span>`, iconSize: null }),
      }));
    }
  }
}

/* ----------------------------------------------------------- Nächster Punkt */
function nearestPoint(lat, lon) {
  const { lat: lats, lon: lons } = META.points;
  let best = -1, bestD = Infinity;
  const cosLat = Math.cos(lat * Math.PI / 180);
  for (let i = 0; i < lats.length; i++) {
    const dLat = lats[i] - lat, dLon = (lons[i] - lon) * cosLat;
    const d = dLat * dLat + dLon * dLon;
    if (d < bestD) { bestD = d; best = i; }
  }
  return { idx: best, distKm: Math.sqrt(bestD) * 111 };
}

const cityByGridPoint = new Map();
function nearestCityCached(lat, lon) {
  const { idx } = nearestPoint(lat, lon);
  if (cityByGridPoint.has(idx)) return cityByGridPoint.get(idx);
  let best = null, bestD = Infinity;
  for (const c of cityData) {
    const dLat = c.lat - lat, dLon = (c.lon - lon) * Math.cos(lat * Math.PI / 180);
    const d = dLat * dLat + dLon * dLon;
    if (d < bestD) { bestD = d; best = c; }
  }
  const value = best ? { name: best.name, country: best.country, km: Math.sqrt(bestD) * 111 } : null;
  cityByGridPoint.set(idx, value);
  return value;
}

function onHover(e) {
  if (!GRID || !GRID.values) return;
  if (hoverQueued) return;
  hoverQueued = true;
  requestAnimationFrame(() => {
    hoverQueued = false;
    const { idx, distKm } = nearestPoint(e.latlng.lat, e.latlng.lng);
    const days = GRID.values[idx];
    const city = nearestCityCached(e.latlng.lat, e.latlng.lng);
    const label = city && city.km < 60
      ? `${city.name}${city.country ? " · " + city.country : ""}`
      : `${e.latlng.lat.toFixed(2)}° N ${e.latlng.lng.toFixed(2)}° E · Datenpunkt ${Math.round(distKm)} km`;
    hoverTip.innerHTML = `<strong>${fmtDays(days)}</strong> ${monthUnit()}<br><span style="opacity:.75">${label}</span>`;
    hoverTip.style.display = "block";
    hoverTip.style.left = (e.containerPoint.x + 14) + "px";
    hoverTip.style.top = (e.containerPoint.y - 6) + "px";
  });
}

/* ------------------------------------------------------------------ Details */
async function selectPlace(lat, lon, name, country) {
  const np = nearestPoint(lat, lon);
  const gridLat = META.points.lat[np.idx], gridLon = META.points.lon[np.idx];
  state.selected = { lat, lon, gridLat, gridLon, name: name || null, country: country || "" };
  if (map && L.circleMarker) {
    if (marker) marker.remove();
    marker = L.circleMarker([gridLat, gridLon], {
      radius: 7, color: "#fff", weight: 2.5, fillColor: "#ffb454", fillOpacity: 1,
      pane: "cities", className: "city-marker-pick", interactive: false,
    }).addTo(map);
  }
  const note = $("#mapnote"); if (note) note.hidden = true;
  renderDetail({ loading: true });
  if (isWinterquarters()) loadPair();          // Gegenstück-Suche parallel anstoßen
  try {
    const q = filterQuery(new URLSearchParams({ lat, lon }));
    const rep = await getJSON("/api/point?" + q);
    const city = rep.city || nearestCityCached(lat, lon);
    state.selected.report = rep;
    state.selected.name = name || (city && city.dist_km < 40 ? city.name : null);
    state.selected.country = country || (city ? (city.name_de || "") : "");
    state.selected.cityInfo = city || null;
    renderDetail();
  } catch (err) {
    renderDetail({ error: String(err.message || err) });
  }
}

/* ------------------------------------------ Gegenstück (Zwei-Orte-Kombination) */
let pairSeq = 0;

async function loadPair() {
  const list = $("#pair-list");
  if (!list) return;
  const sel = state.selected;
  if (!sel) {
    list.innerHTML = "";
    list.append(el("li", { class: "muted", text: "noch kein Ort gewählt" }));
    return;
  }
  list.innerHTML = "";
  list.append(el("li", { class: "muted", text: "wird gesucht …" }));
  const q = filterQuery(new URLSearchParams({ lat: sel.lat, lon: sel.lon, limit: 8 }));
  q.set("role", state.pair.role);
  if (state.pair.maxKm) q.set("max_km", state.pair.maxKm);
  const token = ++pairSeq;
  try {
    const data = await getJSON("/api/winterquarters/pair?" + q);
    if (token !== pairSeq) return;
    state.pair.data = data;
    renderPair(data);
  } catch (err) {
    if (token !== pairSeq) return;
    list.innerHTML = "";
    list.append(el("li", { class: "muted", text: "Fehler: " + (err.message || err) }));
  }
}

function renderPair(data) {
  const list = $("#pair-list");
  if (!list) return;
  list.innerHTML = "";
  const anchor = data.anchor || {};
  const name = (anchor.city && anchor.city.name) || "gewählter Ort";
  list.append(el("li", {
    class: "muted",
    text: `${data.wanted_label || "Gegenstück"} zu ${name} · hier ${fmtDays(anchor.own_days)} Tage`
      + (data.max_km ? ` · bis ${fmtDays(data.max_km)} km` : "")
      + " · rechts die Summe beider Quartiere",
  }));
  const cands = data.candidates || [];
  if (!cands.length) {
    list.append(el("li", { class: "muted", text: "Keine Vorschläge im gewählten Radius." }));
    return;
  }
  cands.forEach((c, i) => {
    list.append(el("li", {},
      el("button", {
        title: `${c.name}: ${fmtDays(c.counterpart_days)} Tage im ${data.wanted_label}`
          + ` · Sommer ${c.summer_label} (dort ${fmtDays(c.own_days)} Tage)`,
        onclick: () => {
          map.flyTo([c.lat, c.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
          selectPlace(c.lat, c.lon, c.name, c.country);
        },
      },
        el("span", {},
          el("span", { class: "place", text: `${i + 1}. ${c.name}` }),
          el("span", { class: "region", text: ` · ${c.country} · ${fmtDays(c.km)} km` }),
          el("span", { class: "region", text: ` · ${fmtDays(c.counterpart_days)} Tage dort` })),
        el("span", { class: "days", text: `Σ ${fmtDays(c.total_days)}` }))));
  });
}

function setPairRole(role) {
  state.pair.role = role === "winter" ? "winter" : "summer";
  $("#pair-role-summer").setAttribute("aria-selected", String(state.pair.role === "summer"));
  $("#pair-role-winter").setAttribute("aria-selected", String(state.pair.role === "winter"));
  loadPair();
}

function monthBars(rep) {
  const maxBar = Math.max(1, ...rep.days_per_month);
  const wrap = el("div", { class: "bars" });
  rep.days_per_month.forEach((v, i) => {
    const bar = el("div", {
      class: "bar" + (i === state.month - 1 ? " sel" : ""),
      style: `height:${Math.max(2, (v / maxBar) * 100)}%`,
      title: `${monthNames[i]}: ${fmtDays(v)} Tage`,
      onclick: () => { state.month = i + 1; setMode("month"); },
    });
    wrap.append(bar);
  });
  const labels = el("div", { class: "bar-labels" },
    monthLetters.map(t => el("span", { text: t })));
  return [wrap, labels];
}

function climateDiagram(rep) {
  const c = (rep.climate && rep.climate.months) || null;
  if (!c) return null;
  const W = 306, H = 104, padL = 3, padR = 3, padT = 8, padB = 22;
  const lo = Math.floor(Math.min(0, ...c.tmin) / 5) * 5;
  const hi = Math.max(10, Math.ceil(Math.max(...c.tmax) / 5) * 5);
  const sunMax = Math.max(12, ...c.sun);
  const y = (t) => padT + (hi - t) / (hi - lo) * (H - padT - padB);
  const ys = (s) => padT + (1 - s / sunMax) * (H - padT - padB);
  const bw = (W - padL - padR) / 12;
  let parts = [`<svg viewBox="0 0 ${W} ${H}" class="climsvg" role="img" aria-label="Klimadiagramm">`];
  // Null-Grad-Linie
  if (lo < 0) parts.push(`<line x1="${padL}" x2="${W - padR}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" class="zero"/>`);
  for (let i = 0; i < 12; i++) {
    const x = padL + i * bw;
    const yTop = y(c.tmax[i]), yBot = y(c.tmin[i]);
    const sel = i === state.month - 1 ? " sel" : "";
    parts.push(
      `<g class="mcol${sel}"><title>${monthNames[i]}: Ø max ${num(c.tmax[i], 1)} °C, Ø min ${num(c.tmin[i], 1)} °C, `
      + `Taupunkt ${num(c.tdew[i], 1)} °C, Sonne ${num(c.sun[i], 1)} h/Tag, Regen ${num(c.prec[i], 0)} mm</title>`
      + `<rect x="${(x + 1).toFixed(1)}" y="${Math.min(yTop, yBot).toFixed(1)}" width="${(bw - 2).toFixed(1)}" `
      + `height="${Math.max(2, Math.abs(yBot - yTop)).toFixed(1)}" rx="2" fill="${tempColor((c.tmax[i] + c.tmin[i]) / 2)}"/>`
      + `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 8}" class="mlab">${monthLetters[i]}</text></g>`);
  }
  const sunPath = c.sun.map((s, i) =>
    `${i === 0 ? "M" : "L"}${(padL + i * bw + bw / 2).toFixed(1)},${ys(s).toFixed(1)}`).join(" ");
  parts.push(`<path d="${sunPath}" class="sunline"/>`);
  c.sun.forEach((s, i) => parts.push(
    `<circle cx="${(padL + i * bw + bw / 2).toFixed(1)}" cy="${ys(s).toFixed(1)}" r="1.7" class="sundot"/>`));
  parts.push(`</svg>`);
  return el("div", { class: "climwrap", html: parts.join("") });
}

/** Sommer-/Winterprofil je Monat – zwei Balken pro Monat. */
function quartierBars(w) {
  if (!w || !w.summer_monthly) return null;
  const summer = new Set(w.summer_months || []);
  const maxBar = Math.max(1, ...w.summer_monthly, ...w.winter_monthly);
  const wrap = el("div", { class: "bars qbars" });
  for (let i = 0; i < 12; i++) {
    const col = el("div", {
      class: "qcol " + (summer.has(i) ? "in-summer" : "in-winter"),
      title: `${monthNames[i]}: Sommerprofil ${fmtDays(w.summer_monthly[i])} Tage, `
        + `Winterprofil ${fmtDays(w.winter_monthly[i])} Tage`,
    });
    col.append(el("div", { class: "qbar summer", style: `height:${Math.max(1, (w.summer_monthly[i] / maxBar) * 100)}%` }));
    col.append(el("div", { class: "qbar winter", style: `height:${Math.max(1, (w.winter_monthly[i] / maxBar) * 100)}%` }));
    wrap.append(col);
  }
  const labels = el("div", { class: "bar-labels" }, monthLetters.map(t => el("span", { text: t })));
  return el("div", {}, wrap, labels,
    el("div", {
      class: "wqlegend",
      html: '<span><i class="summer"></i>Sommer-Wohlfühlwetter</span>'
        + '<span><i class="winter"></i>Winterquartier-Wetter</span>',
    }));
}

/** Block „Quartier-Kombination“ in der Ortsauswertung (nur Winterquartier-Modus). */
function quartierBlock(rep) {
  const w = rep.wq;
  if (!w) return [];
  const nodes = [
    el("div", { class: "detail-sep" }),
    el("div", {
      class: "bars-head",
      text: `Quartier-Kombination · Sommer ${w.summer_label}, Winter ${w.winter_label}`,
    }),
    el("div", { class: "big", text: `${fmtDays(w.total_days)} Tage/Jahr` }),
    el("div", {
      class: "sub",
      text: `Sommerquartier: ${fmtDays(w.summer_days)} Tage · Winterquartier: ${fmtDays(w.winter_days)} Tage`
        + (w.europe_best ? ` · Bestwert in Europa: ${fmtDays(w.europe_best.days)} Tage`
          + (w.europe_best.place ? ` (${w.europe_best.place})` : "") : ""),
    }),
  ];
  const bars = quartierBars(w);
  if (bars) nodes.push(bars);
  nodes.push(el("div", { class: "climnums", html:
    `Übers ganze Jahr gerechnet: <strong>${fmtDays(w.summer_year_days)}</strong> Tage nach Sommerprofil, `
    + `<strong>${fmtDays(w.winter_year_days)}</strong> nach Winterquartier-Profil. `
    + `Die Kombination zählt jeden Tag nur einmal – Sommer- und Winterfenster überschneiden sich nicht.` }));
  nodes.push(el("div", { class: "sub", style: "margin-top:.35rem",
    text: "Gegenstücke für die andere Jahreshälfte: Liste „Gegenstück finden“ links." }));
  return nodes;
}

function renderDetail(opts = {}) {
  const box = $("#detail");
  const sel = state.selected;
  if (!sel) { box.hidden = true; return; }
  box.hidden = false;
  if (opts.loading) {
    box.innerHTML = "";
    box.append(el("h3", {}, el("span", { text: "Auswertung …" }), closeBtn()),
      el("p", { class: "muted", text: "Tagesdaten werden gezählt." }));
    return;
  }
  if (opts.error) {
    box.innerHTML = "";
    box.append(el("h3", {}, el("span", { text: "Fehler" }), closeBtn()),
      el("p", { class: "muted", text: opts.error }));
    return;
  }

  const rep = sel.report;
  const mIdx = state.month - 1;
  const isMonth = state.mode === "month";
  const isWq = isWinterquarters() && rep.wq;
  const headline = isWq ? rep.wq.total_days : (isMonth ? rep.days_per_month[mIdx] : rep.days_per_year);
  const headlineLabel = isWq ? "Tage/Jahr mit Wohlfühlklima"
    : (isMonth ? `Tage im ${monthNames[mIdx]}` : "Tage pro Jahr");
  const isWqAfter = isWinterquarters();
  const clim = rep.climate || {};
  const year = clim.year || {};
  const cityLine = sel.cityInfo && sel.cityInfo.dist_km > 1.5
    ? `nächster Ort: ${sel.cityInfo.name} (${num(sel.cityInfo.dist_km, 1)} km)`
    : `ERA5-Gitterpunkt ${num(rep.lat, 2)}° N ${num(rep.lon, 2)}° E`
      + (rep.grid_distance_km > 1 ? ` · ${Math.round(rep.grid_distance_km)} km vom Klick` : "");

  const [bars, barLabels] = monthBars(rep);
  box.innerHTML = "";
  box.append(
    el("h3", {},
      el("span", {
        text: (sel.name || "Ausgewählter Punkt") + (sel.country ? ", " + sel.country : ""),
        class: "title",
      }), closeBtn()),
    el("div", { class: "big", text: `${fmtDays(headline)} ${headlineLabel}` }),
    el("div", { class: "sub", text: isWqAfter
      ? `${rep.wq.summer_label} nach Sommerprofil · besser als ${num(rep.wq.percentile, 0)} % aller Rasterpunkte`
      : `mit deiner Einstellung · besser als ${num(rep.percentile, 0)} % aller Rasterpunkte` }),
    el("div", { class: "sub best", text: (!isWqAfter && rep.europe_best)
      ? `Bestwert in Europa: ${fmtDays(rep.europe_best.days)} Tage${rep.europe_best.place ? ` (${rep.europe_best.place})` : ""}`
      : "" }),
    el("div", { class: "bars-head", text: isWqAfter
      ? `Sommer-Wohlfühlwetter je Monat · Ø ${fmtDays(rep.days_per_year)}/Jahr`
      : `Wohlfühl-Tage je Monat · Ø ${fmtDays(rep.days_per_year)}/Jahr` }),
    bars, barLabels,
    el("div", { class: "detail-sep" }),
    el("div", { class: "bars-head", html: `Klima hier <span class="muted">Ø min/max °C, gelb: Sonnenstunden</span>` }),
    climateDiagram(rep) || el("p", { class: "muted", text: "keine Klimadaten" }),
    el("div", {
      class: "climnums",
      text: `Jahr: Ø max ${num(year.tmax, 1)} °C · Ø min ${num(year.tmin, 1)} °C · Taupunkt ${num(year.tdew, 1)} °C · `
        + `Sonne ${num(year.sun, 1)} h/Tag · Regen ${num(year.prec, 0)} mm`,
    }),
    el("div", {
      class: "climnums",
      text: `${fmtDays(clim.summer_days || 0)} Sommertage (≥ 25 °C) · ${fmtDays(clim.hot_days || 0)} heiße Tage (≥ 30 °C) · `
        + `${fmtDays(clim.frost_days || 0)} Frosttage · ${fmtDays(clim.wet_days || 0)} Regentage`,
    }),
    ...(isWqAfter ? quartierBlock(rep) : []),
    el("div", { class: "sub", style: "margin-top:.35rem", text: cityLine }),
  );
}

function closeBtn() {
  return el("button", {
    class: "close", title: "schließen", "aria-label": "schließen",
    onclick: () => {
      state.selected = null; $("#detail").hidden = true;
      if (marker) { marker.remove(); marker = null; }
      const note = $("#mapnote"); if (note) note.hidden = false;
    },
  }, "✕");
}

/* ---------------------------------------------------------------- Ortssuche */
let searchTimer = null;
function initSearch() {
  const input = $("#city-search"), results = $("#search-results");
  if (!input) return;
  const close = () => { results.hidden = true; };
  input.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    searchTimer = setTimeout(async () => {
      try {
        const data = await getJSON("/api/cities/search?q=" + encodeURIComponent(q) + "&limit=8");
        results.innerHTML = "";
        if (!data.cities.length) { results.append(el("button", { class: "nohit", text: "kein Treffer" })); }
        for (const c of data.cities) {
          results.append(el("button", {
            onclick: () => {
              close(); input.value = c.name;
              map.flyTo([c.lat, c.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
              selectPlace(c.lat, c.lon, c.name, c.country);
            },
          }, el("span", { class: "place", text: c.name }),
            el("span", { class: "region", text: ` ${c.country}${c.pop ? " · " + num(c.pop) : ""}` })));
        }
        results.hidden = false;
      } catch { close(); }
    }, 180);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Enter") { const first = results.querySelector("button:not(.nohit)"); if (first) first.click(); }
  });
  document.addEventListener("click", (e) => {
    if (!$("#searchbox").contains(e.target)) close();
  });
}

/* ------------------------------------------------------------------ Steuerung */
function rangeControl(cfg) {
  const wrap = el("div", { class: "ctrl" });
  const out = el("output");
  const head = el("div", { class: "ctrl-head" }, el("label", { text: cfg.label }), out);
  const track = el("div", { class: "track" }, el("div", { class: "fill" }));
  const lo = el("input", { type: "range", min: cfg.min, max: cfg.max, step: cfg.step, value: cfg.lo, "aria-label": `${cfg.label} von` });
  const hi = el("input", { type: "range", min: cfg.min, max: cfg.max, step: cfg.step, value: cfg.hi, "aria-label": `${cfg.label} bis` });
  const box = el("div", { class: "dual" }, track, lo, hi);
  wrap.append(head, box);

  function paint() {
    const span = cfg.max - cfg.min;
    const a = ((lo.value - cfg.min) / span) * 100;
    const b = ((hi.value - cfg.min) / span) * 100;
    track.firstChild.style.left = Math.min(a, b) + "%";
    track.firstChild.style.width = Math.abs(b - a) + "%";
    out.textContent = `${cfg.format(Number(lo.value))} bis ${cfg.format(Number(hi.value))}`;
  }
  lo.addEventListener("input", () => { if (Number(lo.value) > Number(hi.value)) lo.value = hi.value; paint(); queueRefresh(); });
  hi.addEventListener("input", () => { if (Number(hi.value) < Number(lo.value)) hi.value = lo.value; paint(); queueRefresh(); });
  paint();
  wrap._set = (a, b) => { lo.value = a; hi.value = b; paint(); };
  wrap._get = () => ({ lo: Number(lo.value), hi: Number(hi.value) });
  return wrap;
}

function singleControl(cfg) {
  const wrap = el("div", { class: "ctrl" });
  const out = el("output");
  const track = el("div", { class: "track" }, el("div", { class: "fill" }));
  const input = el("input", { type: "range", min: cfg.min, max: cfg.max, step: cfg.step, value: cfg.value, "aria-label": cfg.label });
  const box = el("div", { class: "single" }, track, input);
  wrap.append(el("div", { class: "ctrl-head" }, el("label", { text: cfg.label }), out), box);
  function paint() {
    const pct = ((input.value - cfg.min) / (cfg.max - cfg.min)) * 100;
    track.firstChild.style.width = pct + "%";
    out.textContent = cfg.format(Number(input.value));
  }
  input.addEventListener("input", () => { paint(); queueRefresh(); });
  paint();
  wrap._set = (v) => { input.value = v; paint(); };
  wrap._get = () => Number(input.value);
  return wrap;
}

const controls = {};         // Sommer-Wohlfühlwetter (im Jahres-/Monatsmodus: das einzige Profil)
const winterControls = {};   // Winterquartier-Wetter

/** Fünf Regler für ein Wetterprofil in ``host`` aufbauen. */
function buildProfile(host, values, target) {
  const set = {};
  set.tmax = rangeControl({
    label: "Höchsttemperatur", unit: "°C", min: -20, max: 45, step: 0.5,
    lo: values.tmax_min, hi: values.tmax_max, format: (v) => fmtTemp(v),
  });
  set.tmin = rangeControl({
    label: "Tiefsttemperatur (nachts)", unit: "°C", min: -30, max: 30, step: 0.5,
    lo: values.tmin_min, hi: values.tmin_max, format: (v) => fmtTemp(v),
  });
  set.tdew = singleControl({
    label: "Taupunkt höchstens", unit: "°C", min: 0, max: 26, step: 0.5,
    value: values.tdew_max, format: (v) => (v >= 26 ? "egal" : fmtTemp(v)),
  });
  set.sun = singleControl({
    label: "Sonne mindestens", unit: "h/Tag", min: 0, max: 12, step: 0.5,
    value: values.sun_min, format: (v) => (v <= 0 ? "egal" : `${num(v, 1)} h/Tag`),
  });
  set.prec = singleControl({
    label: "Niederschlag höchstens", unit: "mm/Tag", min: 0, max: 20, step: 0.5,
    value: Math.min(values.prec_max, 20), format: (v) => (v >= 20 ? "egal" : `${num(v, 1)} mm`),
  });
  host.append(set.tmax, set.tmin, set.tdew, set.sun, set.prec);
  set._target = target;
  return set;
}

function buildControls() {
  Object.assign(controls, buildProfile($("#controls"), state.params, "params"));
  Object.assign(winterControls, buildProfile($("#winter-controls"), state.winter, "winter"));
}

/** Reglerwerte eines Profils in den Zustand übernehmen. */
function syncProfile(set, target) {
  const tmax = set.tmax._get(), tmin = set.tmin._get();
  target.tmax_min = tmax.lo;
  target.tmax_max = tmax.hi;
  target.tmin_min = tmin.lo;
  target.tmin_max = tmin.hi;
  target.tdew_max = set.tdew._get();
  target.sun_min = set.sun._get();
  const prec = set.prec._get();
  target.prec_max = prec >= 20 ? 99 : prec;
}

function syncParams() {
  syncProfile(controls, state.params);
  syncProfile(winterControls, state.winter);
}

/** Zustand -> Regler (nach Presets, Zurücksetzen, Link oder Optimierung). */
function writeProfile(set, values) {
  set.tmax._set(values.tmax_min, values.tmax_max);
  set.tmin._set(values.tmin_min, values.tmin_max);
  set.tdew._set(values.tdew_max);
  set.sun._set(values.sun_min);
  set.prec._set(values.prec_max >= 90 ? 20 : values.prec_max);
}

function applyParamsToControls() {
  writeProfile(controls, state.params);
  writeProfile(winterControls, state.winter);
}

/**
 * Preset-Knöpfe für ein Profil. ``apply`` übernimmt die Werte ins Zielobjekt,
 * ``write`` schiebt sie anschließend in die Regler.
 */
function buildPresets(hostSel, presets, apply, write) {
  const host = $(hostSel);
  if (!host) return;
  host.innerHTML = "";
  for (const preset of presets) {
    host.append(el("button", {
      class: "preset", title: preset.desc, text: preset.name,
      onclick: (ev) => {
        apply(preset.params);
        write();
        host.querySelectorAll(".preset").forEach(b => b.classList.remove("active"));
        ev.currentTarget.classList.add("active");
        refresh(true);
      },
    }));
  }
}

function buildAllPresets() {
  buildPresets("#presets", META.presets,
    (params) => { state.params = { ...state.params, ...params }; },
    () => writeProfile(controls, state.params));
  buildPresets("#winter-presets", META.winter_presets || [],
    (params) => { state.winter = { ...state.winter, ...params }; },
    () => writeProfile(winterControls, state.winter));
}

/* ------------------------------------------------------------------ Refresh */
function queueRefresh(ms = 220) {
  syncParams();
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => refresh(), ms);
}

async function refresh(immediate = false) {
  if (immediate) { clearTimeout(refreshTimer); refresh(); return; }
  const mySeq = ++seq;
  document.body.classList.add("loading");
  const bar = $("#progress");
  clearTimeout(bar._hide);
  bar.hidden = false;
  try {
    const info = await getJSON("/api/map?" + queryString());
    if (mySeq !== seq) return;
    GRID = info;
    updateOverlay();
    renderLegend(info);
    updateHash();
  } catch (err) {
    toast("Fehler: " + (err.message || err));
  } finally {
    if (mySeq === seq) {
      document.body.classList.remove("loading");
      bar._hide = setTimeout(() => { bar.hidden = true; }, 250);
    }
  }
  if (mySeq !== seq) return;
  loadCities();                                  // Markerwerte aktuell halten
  loadRanking();
  if (state.selected) selectPlace(state.selected.lat, state.selected.lon, state.selected.name, state.selected.country);
}

async function loadRanking() {
  const q = filterQuery(new URLSearchParams({ limit: 12 }));
  try {
    const data = await getJSON("/api/ranking?" + q);
    const host = $("#ranking");
    host.innerHTML = "";
    if (!data.cities.length) { host.append(el("li", { class: "muted", text: "keine Treffer" })); return; }
    data.cities.forEach((c, i) => {
      host.append(el("li", {},
        el("button", {
          title: `${c.name} – Datenpunkt ${c.dist_km} km entfernt`,
          onclick: () => {
            map.flyTo([c.lat, c.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
            selectPlace(c.lat, c.lon, c.name, c.country);
          },
        },
          el("span", {}, el("span", { class: "place", text: `${i + 1}. ${c.name}` }),
            el("span", { class: "region", text: " · " + c.country })),
          el("span", { class: "days", text: fmtDays(c.days) }))));
    });
  } catch (err) {
    $("#ranking").innerHTML = `<li class="muted">Fehler: ${err.message || err}</li>`;
  }
}

function renderLegend(info) {
  const legend = $("#legend");
  legend.hidden = false;
  const stops = META.color_stops.map(([f, r, g, b]) => `rgb(${r},${g},${b}) ${Math.round(f * 100)}%`).join(", ");
  $("#legend-bar").style.background = `linear-gradient(90deg, ${stops})`;
  $("#legend-ticks").innerHTML = info.legend_ticks.map(t => `<span>${fmtDays(t)}</span>`).join("");
  $("#legend-title").textContent =
    `${monthUnit()} (Skala bis ${fmtDays(info.vmax)}${info.fixed_scale ? ", fix" : ", auto"})`;
}

/* -------------------------------------------------------------------- State */
function updateHash() {
  const p = new URLSearchParams();
  p.set("mode", state.mode);
  p.set("month", state.month);
  for (const [k, v] of Object.entries(state.params)) p.set(k, v);
  // Winterquartier-Einstellungen gehören in den teilbaren Link
  p.set("wview", state.wq.view);
  p.set("sfrom", state.wq.sfrom);
  p.set("sto", state.wq.sto);
  for (const [k, v] of Object.entries(state.winter)) p.set("w" + k, v);
  p.set("role", state.pair.role);
  if (state.pair.maxKm) p.set("maxkm", state.pair.maxKm);
  if (state.fixedScale) p.set("fixed", "1");
  history.replaceState(null, "", "#" + p.toString());
}

function readHash() {
  if (!location.hash.length) return;
  const p = new URLSearchParams(location.hash.slice(1));
  if (["year", "month", "wq"].includes(p.get("mode"))) state.mode = p.get("mode");
  if (p.get("month")) state.month = Number(p.get("month")) || 7;
  for (const k of Object.keys(state.params)) if (p.has(k)) state.params[k] = Number(p.get(k));
  if (["combo", "summer", "winter"].includes(p.get("wview"))) state.wq.view = p.get("wview");
  if (p.get("sfrom")) state.wq.sfrom = Number(p.get("sfrom")) || state.wq.sfrom;
  if (p.get("sto")) state.wq.sto = Number(p.get("sto")) || state.wq.sto;
  for (const k of Object.keys(state.winter)) if (p.has("w" + k)) state.winter[k] = Number(p.get("w" + k));
  if (["summer", "winter"].includes(p.get("role"))) state.pair.role = p.get("role");
  if (p.get("maxkm")) state.pair.maxKm = Number(p.get("maxkm")) || 0;
  if (p.get("fixed")) state.fixedScale = true;
}

/* ------------------------------------------------------- Datenbasis anzeigen */
function showDataStatus() {
  const badge = $("#dataset-badge");
  const ds = META.dataset || {};
  const period = `${String(ds.start_date || "").slice(0, 4)}–${String(ds.end_date || "").slice(0, 4)}`;
  $("#about-period").textContent = period;
  $("#about-source").textContent = ds.source || "";
  $("#about-grid").textContent = `${String(ds.grid_step_deg).replace(".", ",")}°`;
  $("#about-years").textContent = ds.n_years;

  const problems = [];
  if (META.synthetic) problems.push("synthetische Testdaten statt ERA5");
  if (META.coverage < 0.999) problems.push(`nur ${num(META.coverage * 100, 1)} % der Punkt-Tage vorhanden`);
  const qerrors = (META.quality && META.quality.errors) || [];
  if (META.quality && META.quality.ok === false) {
    problems.push(...(qerrors.length ? qerrors : ["Plausibilitätsprüfung nicht bestanden"]));
  }

  badge.textContent = META.synthetic
    ? `⚠ Testdaten · ${ds.n_points} Punkte · ${period}`
    : `${ds.n_points} Rasterpunkte · ERA5 ${period} · ${ds.n_days} Tage`;
  badge.classList.toggle("warn", Boolean(problems.length));

  const banner = $("#banner");
  if (problems.length) {
    banner.hidden = false;
    banner.innerHTML = "";
    banner.append(
      el("strong", { text: "Diese Karte zeigt gerade keine belastbaren Klimadaten: " }),
      el("span", { text: problems.join(" · ") + ". " }),
      el("span", {
        text: META.synthetic
          ? "Das Raster stammt aus scripts/make_dev_grid.py (Demo). Die echten ERA5-Daten erzeugt der GitHub-Action-Workflow „Klimadaten bauen“ – danach verschwindet dieser Hinweis automatisch."
          : "Bitte den Workflow „Klimadaten bauen“ erneut ausführen (Details in app/data/quality_report.json).",
      }));
  } else {
    banner.hidden = true;
  }

  const q = $("#about-quality");
  if (q) {
    if (META.quality && META.quality.warnings && META.quality.warnings.length) {
      q.textContent = "Hinweise zur Datenqualität: " + META.quality.warnings.join("; ");
    } else if (META.repaired && META.repaired.missing_point_days) {
      q.textContent = `Im Datensatz fehlten ${num(META.repaired.missing_point_days)} Punkt-Tage; `
        + "sie wurden aus Nachbarpunkten ergänzt.";
    } else if (!problems.length) {
      q.textContent = "Datenqualität automatisch geprüft (scripts/check_grid.py): ohne Befund.";
    } else {
      q.textContent = "";
    }
  }
}

/* ------------------------------- Statusseite: Daten fehlen / werden gebaut */
const BUILD_POLL_MS = 15000;
let buildTimer = null;
let buildReasons = [];
let gateState = "";        // Zustand des Datentors aus der 503-Antwort (missing/unusable)

/**
 * Welcher Zustand ist für die Besucherin die beste Auskunft?
 * Ein laufender, stehengebliebener oder fehlgeschlagener Datenlauf sagt mehr als
 * der statische Tor-Zustand; liegt aber ein *abgelehnter* Datensatz und kein
 * aktiver Lauf vor, muss die Seite „Datensatz abgelehnt" heißen – sonst wirkt es,
 * als sei noch nie etwas gebaut worden.
 */
function effectiveState(info) {
  const build = info.state || "not_started";
  if (info.data_ready) return "finished";
  if (["building", "failed", "stalled"].includes(build)) return build;
  if (gateState === "unusable") return "unusable";
  return build;
}

const BUILD_TEXT = {
  building: {
    title: "Klimadaten werden aufgebaut",
    lead: "Die echten ERA5-Werte für Europa werden gerade geladen. Die Karte erscheint automatisch, "
      + "sobald der Datenlauf fertig und geprüft ist – diese Seite bleibt offen.",
  },
  finished: {
    title: "Datenlauf fertig – Website wartet auf das Deployment",
    lead: "Der Datensatz ist gebaut und geprüft. Render übernimmt den neuen Stand automatisch; "
      + "nach dem Deployment erscheint die Karte von selbst.",
  },
  failed: {
    title: "Datenlauf fehlgeschlagen",
    lead: "Der letzte Aufbau der Klimadaten ist nicht durchgelaufen. Ohne geprüfte ERA5-Daten "
      + "bleibt die Karte leer – bitte den Workflow erneut starten.",
  },
  stalled: {
    title: "Datenlauf unterbrochen",
    lead: "Der Aufbau hat sich seit längerem nicht mehr gemeldet (Zeitbudget oder Abbruch). "
      + "Ein erneuter Start macht am letzten Kontrollpunkt weiter.",
  },
  not_started: {
    title: "Noch keine Klimadaten",
    lead: "Für diese Website liegen bisher keine echten ERA5-Klimadaten vor. Der Aufbau läuft "
      + "als GitHub-Action „Klimadaten bauen“ – danach verschwindet diese Seite automatisch.",
  },
  unusable: {
    title: "Datensatz abgelehnt",
    lead: "Es liegt ein Datensatz vor, der das Qualitätstor nicht besteht. Die Website zeigt "
      + "bewusst keine Zahlen daraus – bitte den Datenlauf wiederholen.",
  },
};

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "–";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 90) return `vor ${s} s`;
  const m = Math.round(s / 60);
  if (m < 90) return `vor ${m} min`;
  return `vor ${num(m / 60, 1)} h`;
}

function fmtDuration(minutes) {
  const m = Math.round(Number(minutes));
  if (!Number.isFinite(m) || m <= 0) return "–";
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

function renderBuild(info) {
  const state = effectiveState(info);
  const texts = BUILD_TEXT[state] || BUILD_TEXT.not_started;
  $("#build-title").textContent = texts.title;
  $("#build-lead").textContent = texts.lead;
  $("#dataset-badge").textContent = state === "building"
    ? "⏳ Daten im Aufbau" : "⚠ keine belastbaren Daten";
  $("#dataset-badge").classList.add("warn");

  const bar = $("#build-bar"), wrap = $("#build-bar-wrap"), label = $("#build-percent");
  const pct = Number(info.percent);
  const known = Number.isFinite(pct) && pct > 0;
  wrap.classList.toggle("indeterminate", !known);
  wrap.classList.toggle("warn", state === "failed" || state === "stalled" || state === "unusable");
  wrap.classList.toggle("done", state === "finished");
  if (known) {
    const clamped = Math.max(0, Math.min(100, pct));
    bar.style.width = `${clamped}%`;
    label.textContent = `${num(clamped, 1)} %`;
    wrap.setAttribute("aria-valuenow", String(Math.round(clamped)));
  } else if (state === "finished") {
    bar.style.width = "100%";
    label.textContent = "fertig";
    wrap.setAttribute("aria-valuenow", "100");
  } else {
    bar.style.width = "0%";
    label.textContent = "warten auf Fortschritt";
    wrap.setAttribute("aria-valuenow", "0");
  }

  const rows = [];
  if (info.phase) rows.push(["Phase", info.phase]);
  if (info.message) rows.push(["Stand", info.message]);
  if (info.blocks_done !== undefined && info.blocks_total) {
    rows.push(["Teilabschnitte", `${num(info.blocks_done)} von ${num(info.blocks_total)}`]);
  }
  if (info.rate) rows.push(["Tempo", String(info.rate)]);
  if (state === "building" && info.eta_minutes) rows.push(["Restzeit (ca.)", fmtDuration(info.eta_minutes)]);
  if (info.updated_at) rows.push(["Letztes Lebenszeichen", fmtAge(info.age_seconds)]);
  if (info.source) rows.push(["Datenquelle", String(info.source)]);
  if (info.progress_source) rows.push(["Fortschritt von", String(info.progress_source)]);
  $("#build-meta").innerHTML = "";
  for (const [k, v] of rows) $("#build-meta").append(el("li", {}, el("b", { text: k }), el("span", { text: v })));

  const allReasons = buildReasons.length ? buildReasons : (info.reasons || []);
  const reasons = allReasons.slice(0, 5);
  const box = $("#build-reasons");
  if (reasons.length) {
    box.hidden = false;
    box.innerHTML = "";
    const list = el("ul", {}, reasons.map(r => el("li", { text: r })));
    if (allReasons.length > reasons.length) {
      list.append(el("li", { text: `… und ${allReasons.length - reasons.length} weitere Prüfungen` }));
    }
    box.append(el("strong", { text: "Warum die Karte leer bleibt:" }), list);
  } else {
    box.hidden = true;
  }

  const log = $("#build-log");
  const tail = info.log_tail || [];
  if (tail.length) {
    log.hidden = false;
    log.textContent = tail.join("\n");
  } else {
    log.hidden = true;
  }

  const link = info.workflow_url || "https://github.com/";
  $("#build-hint").innerHTML = "";
  $("#build-hint").append(
    el("span", { text: "Diese Seite zeigt ausschließlich echte, automatisch geprüfte ERA5-Klimadaten. " }),
    el("a", { href: link, target: "_blank", rel: "noopener", text: "Workflow „Klimadaten bauen“" }),
    el("span", { text: " · der Fortschritt wird alle 15 s aktualisiert. " }),
    el("button", { class: "mini", text: "Jetzt prüfen", onclick: () => pollBuild(true) }));
}

async function pollBuild(manual = false) {
  let info = {};
  try {
    info = await getJSON("/api/build" + (manual ? "?refresh=true" : ""));
  } catch (err) {
    info = { state: "not_started", progress_source: `nicht erreichbar (${err.message})` };
  }
  renderBuild(info);
  if (info.data_ready) {           // Daten sind da: Seite neu laden, Karte zeigen
    setTimeout(() => location.reload(), 800);
    return;
  }
  if (buildTimer) clearTimeout(buildTimer);
  buildTimer = setTimeout(() => pollBuild(false), BUILD_POLL_MS);
}

async function showStatusScreen(payload) {
  buildReasons = (payload && payload.reasons) || [];
  gateState = (payload && payload.state) || "";
  document.querySelector("main").hidden = true;
  $("#building").hidden = false;
  if (payload && payload.state === "unusable") buildReasons = buildReasons.length
    ? buildReasons : ["Datensatz nicht verwendbar"];
  await pollBuild(true);
}

/* ------------------------------------------------------------------- Start */
async function boot() {
  try {
    META = await getJSON("/api/meta");
  } catch (err) {
    await showStatusScreen(err.payload || null);
    return;
  }
  if (!META.data_ready) {
    await showStatusScreen(META);
    return;
  }
  readHash();
  state.params = { ...META.defaults, ...state.params };
  state.winter = { ...(META.winter_defaults || {}), ...state.winter };
  WQ_BOUNDS = META.winterquarters || {};
  showDataStatus();

  const sel = $("#month-select");
  monthNames.forEach((m, i) => sel.append(el("option", { value: i + 1, text: m })));
  sel.value = state.month;

  // Monatsauswahl des Sommerfensters (Winterquartier-Modus)
  for (const id of ["#wq-sfrom", "#wq-sto"]) {
    const box = $(id);
    if (box) monthNames.forEach((m, i) => box.append(el("option", { value: i + 1, text: m })));
  }
  const maxKm = $("#pair-max-km");
  if (maxKm) {
    for (const [v, label] of [[0, "egal"], [500, "500 km"], [1000, "1.000 km"],
      [1500, "1.500 km"], [2500, "2.500 km"], [4000, "4.000 km"]]) {
      maxKm.append(el("option", { value: v, text: label }));
    }
    maxKm.value = String(state.pair.maxKm || 0);
  }

  buildControls();
  applyParamsToControls();
  buildAllPresets();
  initSearch();

  $("#mode-year").addEventListener("click", () => setMode("year"));
  $("#mode-month").addEventListener("click", () => setMode("month"));
  $("#mode-wq").addEventListener("click", () => setMode("wq"));
  sel.addEventListener("change", () => { state.month = Number(sel.value); refresh(true); });
  $("#wq-sfrom").addEventListener("change", setSummerWindow);
  $("#wq-sto").addEventListener("change", setSummerWindow);
  $("#wq-view-combo").addEventListener("click", () => setWqView("combo"));
  $("#wq-view-summer").addEventListener("click", () => setWqView("summer"));
  $("#wq-view-winter").addEventListener("click", () => setWqView("winter"));
  $("#btn-wq-optimize").addEventListener("click", optimizeWq);
  $("#pair-role-summer").addEventListener("click", () => setPairRole("summer"));
  $("#pair-role-winter").addEventListener("click", () => setPairRole("winter"));
  if (maxKm) maxKm.addEventListener("change", () => {
    state.pair.maxKm = Number(maxKm.value) || 0;
    updateHash();
    loadPair();
  });
  $("#fixed-scale").addEventListener("change", (e) => { state.fixedScale = e.target.checked; refresh(true); });
  $("#show-cities").addEventListener("change", (e) => { state.showCities = e.target.checked; paintCities(); });
  $("#btn-reset").addEventListener("click", () => {
    state.params = { ...META.defaults };
    state.winter = { ...(META.winter_defaults || {}) };
    const summer = (WQ_BOUNDS.summer_months || [5, 6, 7, 8, 9]);
    state.wq.view = "combo";
    state.wq.sfrom = summer[0];
    state.wq.sto = summer[summer.length - 1];
    applyParamsToControls();
    syncWqInputs();
    document.querySelectorAll("#presets .preset, #winter-presets .preset")
      .forEach(b => b.classList.remove("active"));
    refresh(true);
  });
  $("#btn-link").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); toast("Link kopiert"); }
    catch { toast(location.href); }
  });
  $("#btn-rank-refresh").addEventListener("click", loadRanking);
  setMode(state.mode, true);

  initMap();
  await refresh(true);
}

function setMode(mode, initial = false) {
  state.mode = mode;
  $("#mode-year").setAttribute("aria-selected", String(mode === "year"));
  $("#mode-month").setAttribute("aria-selected", String(mode === "month"));
  $("#mode-wq").setAttribute("aria-selected", String(mode === "wq"));
  // Die Monatsauswahl gehört ausschließlich zum Urlaubsmonat.
  $("#month-picker").hidden = mode !== "month";
  // Winterquartier-Karten erscheinen nur in diesem Modus.
  document.querySelectorAll("[data-wq-only]").forEach(node => { node.hidden = mode !== "wq"; });
  $("#feel-title").textContent = mode === "wq" ? "Dein Sommer-Wohlfühlwetter" : "Dein Wohlfühlwetter";
  $("#presets-title").textContent = mode === "wq" ? "Sommer-Voreinstellungen" : "Voreinstellungen";
  const rankTitle = $("#rank-title");
  if (rankTitle) rankTitle.textContent = mode === "wq"
    ? "Beste Orte (Quartier-Kombination)" : "Top-Orte für diese Einstellung";
  if (mode === "month") $("#month-select").value = state.month;
  if (mode === "wq") syncWqInputs();
  if (!initial) refresh(true);
  else if (state.selected) renderDetail();
}

/* ------------------------------------------------------- Winterquartier-Modus */
/** Monatsauswahl, Ansichts-Umschalter und Hinweistexte des Modus setzen. */
function syncWqInputs() {
  const sfrom = $("#wq-sfrom"), sto = $("#wq-sto");
  if (sfrom) sfrom.value = state.wq.sfrom;
  if (sto) sto.value = state.wq.sto;
  for (const view of ["combo", "summer", "winter"]) {
    const btn = $("#wq-view-" + view);
    if (btn) btn.setAttribute("aria-selected", String(state.wq.view === view));
  }
  const summer = summerMonths();
  const winter = winterMonths();
  const hint = $("#wq-window");
  if (hint) {
    hint.textContent = summer.length >= 12
      ? "Achtung: Ohne Wintermonate gibt es kein Winterquartier – bitte ein Sommerfenster von 1 bis 11 Monaten wählen."
      : `Sommer: ${monthsLabel(summer)} (${summer.length} Monate) · Winterquartier: `
        + `${monthsLabel(winter)} (${winter.length} Monate)`;
  }
}

function setWqView(view) {
  state.wq.view = ["summer", "winter"].includes(view) ? view : "combo";
  syncWqInputs();
  refresh(true);
}

function setSummerWindow() {
  state.wq.sfrom = Number($("#wq-sfrom").value);
  state.wq.sto = Number($("#wq-sto").value);
  syncWqInputs();
  refresh(true);
}

/**
 * „Optimale Kombination finden“: Der Server probiert alle zusammenhängenden
 * Aufteilungen des Jahres durch (Sommerfenster 3–9 Monate) und meldet Aufteilung
 * + Ort mit den meisten Wohlfühl-Tagen. Das Ergebnis wird gleich angewendet.
 */
async function optimizeWq() {
  const btn = $("#btn-wq-optimize");
  const out = $("#wq-result"), alt = $("#wq-alternatives");
  btn.disabled = true;
  out.hidden = false;
  out.textContent = "rechnet …";
  alt.innerHTML = "";
  try {
    const q = filterQuery();
    const bounds = (META.winterquarters || {});
    q.set("min_months", bounds.min_months || 3);
    q.set("max_months", bounds.max_months || 9);
    q.set("limit", 5);
    q.set("width", currentWidth());
    const data = await getJSON("/api/winterquarters/optimize?" + q);
    const best = data.best;
    if (!best) {
      out.textContent = "Keine Aufteilung gefunden.";
      return;
    }
    state.wq.sfrom = best.start + 1;
    state.wq.sto = ((best.start + best.length - 1) % 12) + 1;
    syncWqInputs();
    const place = best.best.place ? ` bei ${best.best.place}${best.best.country ? " (" + best.best.country + ")" : ""}` : "";
    out.innerHTML = `Beste Kombination: <strong>Sommer ${best.summer_label}</strong> · Winter `
      + `<strong>${best.winter_label}</strong> → <strong>${fmtDays(best.best.days)} Tage/Jahr</strong>${place}.`;
    if (best.best_city) {
      out.append(el("span", {
        text: ` Beste Stadt: ${best.best_city.name} (${best.best_city.country}) mit `
          + `${fmtDays(best.best_city.days)} Tagen.`,
      }));
    }
    if (data.point_optimum) {
      out.append(el("span", {
        text: ` Mit individuell gewählten Monaten sind an besten Punkten bis `
          + `${fmtDays(data.point_optimum.days)} Tage möglich.`,
      }));
    }
    (data.candidates || []).slice(1).forEach((cand) => {
      alt.append(el("button", {
        class: "preset",
        title: `Im Mittel ${fmtDays(cand.mean_days)} Tage/Jahr über Europa; Bestwert `
          + `${fmtDays(cand.best.days)} Tage${cand.best.place ? " bei " + cand.best.place : ""}`,
        text: `${cand.summer_label} (${fmtDays(cand.best.days)})`,
        onclick: () => {
          state.wq.sfrom = cand.start + 1;
          state.wq.sto = ((cand.start + cand.length - 1) % 12) + 1;
          syncWqInputs();
          refresh(true);
        },
      }));
    });
    refresh(true);
  } catch (err) {
    out.textContent = "Fehler: " + (err.message || err);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", boot);
