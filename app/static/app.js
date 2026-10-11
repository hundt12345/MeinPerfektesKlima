/* Mein perfektes Klima – Europa
   Karte + Bedienung (Vanilla JS, Leaflet). */
"use strict";

const state = {
  mode: "year",              // "year" | "month"
  month: 7,                  // 1..12
  params: { tmax_min: 18, tmax_max: 30, tmin_min: 5, tmin_max: 20, tdew_max: 18, sun_min: 3, prec_max: 99 },
  fixedScale: false,
  selected: null,            // {lat, lon, name}
};

let META = null;             // /api/meta
let GRID = null;             // letzte /api/map-Antwort
let map = null, overlay = null, countriesLayer = null, placeLayer = null, hoverTip = null, marker = null;
let seq = 0, hoverQueued = false, refreshTimer = null;
const monthNames = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];

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

function fmtDays(v) {
  const r = Math.round(v);
  return r.toLocaleString("de-DE");
}

function fmtTemp(v) {
  const r = Math.round(v * 2) / 2;
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toLocaleString("de-DE")} °C`.replace("−", "−");
}

function activeMonth() { return state.mode === "month" ? state.month : null; }

function queryString() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(state.params)) p.set(k, v);
  const m = activeMonth();
  if (m) p.set("month", m);
  else p.set("month", "all");
  p.set("width", currentWidth());
  if (state.fixedScale) p.set("fixed", "true");
  return p.toString();
}

function currentWidth() {
  const w = $(".mapwrap").clientWidth || 1100;
  return Math.max(420, Math.min(1800, Math.round(w)));
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
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

/* ------------------------------------------------------------------- Karte */
function initMap() {
  const bbox = META.dataset ? (META.dataset.bbox || null) : null;
  map = L.map("map", { zoomControl: true, minZoom: 2, maxZoom: 8, worldCopyJump: false })
    .setView([50.5, 12], 4);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
    attribution: 'Karte: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, &copy; <a href="https://carto.com/attributions">CARTO</a> · Daten: ERA5 via <a href="https://open-meteo.com/">Open-Meteo</a>',
    subdomains: "abcd", maxZoom: 9, opacity: 0.85,
  }).addTo(map);

  placeLayer = L.layerGroup().addTo(map);
  hoverTip = el("div", { class: "leaflet-tooltip klima", style: "position:absolute;display:none;pointer-events:none;z-index:800" });
  map.getContainer().append(hoverTip);

  fetch("/api/countries").then(r => r.json()).then(geo => {
    countriesLayer = L.geoJSON(geo, {
      style: { color: "#7d93ab", weight: 0.7, fillColor: "#1b2c42", fillOpacity: 0.55, opacity: 0.8 },
      interactive: false,
    }).addTo(map);
  }).catch(() => {});

  map.on("mousemove", onHover);
  map.on("mouseout", () => { hoverTip.style.display = "none"; });
  map.on("click", (e) => selectPlace(e.latlng.lat, e.latlng.lng));
  map.on("moveend zoomend", () => {
    const w = currentWidth();
    if (overlay && GRID && GRID.width && Math.abs(w - GRID.width) > 180) refresh();
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
    overlay = L.imageOverlay(url, bounds(), { opacity: 1, interactive: false, className: "klima-overlay" }).addTo(map);
  } else {
    overlay.setUrl(url);
    overlay.setBounds(bounds());
  }
}

/* ----------------------------------------------------------- Nächster Punkt */
function nearestPoint(lat, lon) {
  const { lat: lats, lon: lons } = META.points;
  let best = -1, bestD = Infinity;
  for (let i = 0; i < lats.length; i++) {
    const dLat = lats[i] - lat;
    const dLon = (lons[i] - lon) * Math.cos(lat * Math.PI / 180);
    const d = dLat * dLat + dLon * dLon;
    if (d < bestD) { bestD = d; best = i; }
  }
  return { idx: best, distKm: Math.sqrt(bestD) * 111 };
}

let CITY_CACHE = null;
async function cities() {
  if (!CITY_CACHE) CITY_CACHE = await getJSON("/api/cities").catch(() => []);
  return CITY_CACHE;
}
async function nearestCity(lat, lon) {
  const list = await cities();
  let best = null, bestD = Infinity;
  for (const c of list) {
    const dLat = c.lat - lat, dLon = (c.lon - lon) * Math.cos(lat * Math.PI / 180);
    const d = dLat * dLat + dLon * dLon;
    if (d < bestD) { bestD = d; best = c; }
  }
  return best;
}

function onHover(e) {
  if (!GRID || !GRID.values) return;
  if (hoverQueued) return;
  hoverQueued = true;
  requestAnimationFrame(async () => {
    hoverQueued = false;
    const { idx, distKm } = nearestPoint(e.latlng.lat, e.latlng.lng);
    const days = GRID.values[idx];
    const unit = state.mode === "month" ? `Tage im ${monthNames[state.month - 1]}` : "Tage pro Jahr";
    const city = await nearestCity(e.latlng.lat, e.latlng.lng).catch(() => null);
    const label = city ? `${city.name} (${Math.round(distKm)} km)` : `${e.latlng.lat.toFixed(2)}°N ${e.latlng.lng.toFixed(2)}°E`;
    hoverTip.innerHTML = `<strong>${fmtDays(days)}</strong> ${unit}<br><span style="opacity:.75">${label}</span>`;
    hoverTip.style.display = "block";
    hoverTip.style.left = (e.containerPoint.x + 14) + "px";
    hoverTip.style.top = (e.containerPoint.y - 6) + "px";
  });
}

/* ------------------------------------------------------------------ Details */
async function selectPlace(lat, lon, name) {
  const np = nearestPoint(lat, lon);
  const gridLat = META.points.lat[np.idx], gridLon = META.points.lon[np.idx];
  state.selected = { lat: gridLat, lon: gridLon, name: name || null };
  if (map && L.circleMarker) {
    if (marker) marker.remove();
    marker = L.circleMarker([gridLat, gridLon], {
      radius: 7, color: "#fff", weight: 2.5, fillColor: "#ffb454", fillOpacity: 1,
      className: "city-marker-pick", interactive: false,
    }).addTo(map);
  }
  renderDetail({ loading: true });
  try {
    const q = new URLSearchParams({ lat: gridLat, lon: gridLon });
    for (const [k, v] of Object.entries(state.params)) q.set(k, v);
    const rep = await getJSON("/api/point?" + q);
    const city = name ? { name, name_de: "" } : await nearestCity(gridLat, gridLon);
    state.selected.name = city ? city.name : null;
    state.selected.country = city ? city.name_de : "";
    state.selected.report = rep;
    renderDetail();
  } catch (err) {
    renderDetail({ error: String(err.message || err) });
  }
}

function renderDetail(opts = {}) {
  const box = $("#detail");
  const sel = state.selected;
  if (!sel) { box.hidden = true; return; }
  box.hidden = false;
  if (opts.loading) { box.innerHTML = `<h3>Auswertung …</h3><p class="muted">Tagesdaten werden gezählt.</p>`; return; }
  if (opts.error) { box.innerHTML = `<h3>Fehler</h3><p class="muted">${opts.error}</p>`; return; }

  const rep = sel.report;
  const monthUnit = "Ø Tage je Monat";
  const maxBar = Math.max(1, ...rep.days_per_month);
  const mIdx = state.month - 1;
  const headlineValue = state.mode === "month" ? rep.days_per_month[mIdx] : rep.days_per_year;
  const headlineLabel = state.mode === "month"
    ? `Tage im ${monthNames[mIdx]}`
    : "Tage/Jahr";
  const bars = rep.days_per_month.map((v, i) =>
    el("div", {
      class: "bar" + (i === state.month - 1 ? " sel" : ""),
      style: `height:${Math.max(2, (v / maxBar) * 100)}%`,
      title: `${monthNames[i]}: ${fmtDays(v)} Tage`,
    })).map(n => n);
  const labels = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"].map(t => el("span", { text: t }));

  box.innerHTML = "";
  box.append(
    el("h3", {},
      el("span", { text: `${sel.name || "Ausgewählter Punkt"}${sel.country ? ", " + sel.country : ""}` }),
      el("button", {
        class: "close", title: "schließen",
        onclick: () => { state.selected = null; box.hidden = true; if (marker) { marker.remove(); marker = null; } },
      }, "✕")),
    el("div", { class: "big", text: `${fmtDays(headlineValue)} ${headlineLabel}` }),
    el("div", { class: "sub", text: `${fmtDays(rep.days_per_year)} Tage/Jahr · ${monthUnit}: `
      + monthNames.map((mn, i) => `${mn.slice(0, 3)} ${fmtDays(rep.days_per_month[i])}`).join(" · ") }),
    el("div", { class: "bars" }, bars),
    el("div", { class: "bar-labels" }, labels),
    el("div", { class: "sub", style: "margin-top:.4rem", text: `Gitterpunkt ${rep.lat.toFixed(2)}°N ${rep.lon.toFixed(2)}°E · ERA5-Tagesdaten` }),
  );
}

/* --------------------------------------------------------------- Steuerung */
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

const controls = {};
function buildControls() {
  const host = $("#controls");
  const p = state.params;

  controls.tmax = rangeControl({
    label: "Höchsttemperatur", unit: "°C", min: -20, max: 45, step: 0.5,
    lo: p.tmax_min, hi: p.tmax_max,
    format: (v) => fmtTemp(v),
  });
  controls.tmin = rangeControl({
    label: "Tiefsttemperatur (nachts)", unit: "°C", min: -30, max: 30, step: 0.5,
    lo: p.tmin_min, hi: p.tmin_max, format: (v) => fmtTemp(v),
  });
  controls.tdew = singleControl({
    label: "Taupunkt höchstens", unit: "°C", min: 0, max: 26, step: 0.5,
    value: p.tdew_max, format: (v) => (v >= 26 ? "egal" : fmtTemp(v)),
  });
  controls.sun = singleControl({
    label: "Sonne mindestens", unit: "h/Tag", min: 0, max: 12, step: 0.5,
    value: p.sun_min, format: (v) => (v <= 0 ? "egal" : `${v} h/Tag`),
  });
  controls.prec = singleControl({
    label: "Niederschlag höchstens", unit: "mm/Tag", min: 0, max: 20, step: 0.5,
    value: Math.min(p.prec_max, 20), format: (v) => (v >= 20 ? "egal" : `${v} mm`),
  });

  host.append(controls.tmax, controls.tmin, controls.tdew, controls.sun, controls.prec);
}

/** Reglerwerte -> state.params */
function syncParams(ctrl = controls) {
  const tmax = ctrl.tmax._get(), tmin = ctrl.tmin._get();
  state.params.tmax_min = tmax.lo;
  state.params.tmax_max = tmax.hi;
  state.params.tmin_min = tmin.lo;
  state.params.tmin_max = tmin.hi;
  state.params.tdew_max = ctrl.tdew._get();
  state.params.sun_min = ctrl.sun._get();
  const prec = ctrl.prec._get();
  state.params.prec_max = prec >= 20 ? 99 : prec;
}

function applyParamsToControls() {
  const p = state.params;
  controls.tmax._set(p.tmax_min, p.tmax_max);
  controls.tmin._set(p.tmin_min, p.tmin_max);
  controls.tdew._set(p.tdew_max);
  controls.sun._set(p.sun_min);
  controls.prec._set(p.prec_max >= 90 ? 20 : p.prec_max);
}

function buildPresets() {
  const host = $("#presets");
  host.innerHTML = "";
  for (const preset of META.presets) {
    host.append(el("button", {
      class: "preset", title: preset.desc, text: preset.name,
      onclick: () => {
        state.params = { ...state.params, ...preset.params };
        applyParamsToControls();
        refresh(true);
      },
    }));
  }
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
  if (state.selected) selectPlace(state.selected.lat, state.selected.lon, state.selected.name);
  loadRanking();
}

async function loadRanking() {
  const q = new URLSearchParams({ limit: 12 });
  for (const [k, v] of Object.entries(state.params)) q.set(k, v);
  const m = activeMonth();
  q.set("month", m || "all");
  try {
    const data = await getJSON("/api/ranking?" + q);
    const host = $("#ranking");
    host.innerHTML = "";
    if (!data.cities.length) { host.append(el("li", { class: "muted", text: "keine Treffer" })); return; }
    data.cities.forEach((c, i) => {
      host.append(el("li", {},
        el("button", {
          onclick: () => {
            map.flyTo([c.lat, c.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
            selectPlace(c.lat, c.lon, c.name);
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
  const base = state.mode === "month" ? `Tage im ${monthNames[state.month - 1]}` : "Tage pro Jahr";
  $("#legend-title").textContent = `${base} (Skala bis ${fmtDays(info.vmax)}${info.fixed_scale ? ", fix" : ", auto"})`;
}

/* -------------------------------------------------------------------- State */
function updateHash() {
  const p = new URLSearchParams();
  p.set("mode", state.mode);
  p.set("month", state.month);
  for (const [k, v] of Object.entries(state.params)) p.set(k, v);
  if (state.fixedScale) p.set("fixed", "1");
  history.replaceState(null, "", "#" + p.toString());
}

function readHash() {
  if (!location.hash.length) return;
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("mode")) state.mode = p.get("mode");
  if (p.get("month")) state.month = Number(p.get("month")) || 7;
  for (const k of Object.keys(state.params)) if (p.has(k)) state.params[k] = Number(p.get(k));
  if (p.get("fixed")) state.fixedScale = true;
}

/* ------------------------------------------------------------------- Start */
async function boot() {
  try {
    META = await getJSON("/api/meta");
  } catch (err) {
    document.querySelector(".mapwrap").innerHTML =
      `<div style="padding:2rem;color:#9db0c6"><h3>Klimadaten fehlen noch</h3>
       <p>${err.message || err}</p>
       <p>Der Datensatz wird vom GitHub-Action-Workflow <code>Klimadaten bauen</code> erzeugt
       (<code>scripts/fetch_climate_grid.py</code>). Nach dem Lauf erscheint die Karte hier automatisch.</p></div>`;
    return;
  }
  readHash();
  state.params = { ...META.defaults, ...state.params };
  if (META.dataset.model && META.dataset.model !== "era5") {
    const b = $("#banner");
    b.hidden = false;
    b.innerHTML = "<strong>Hinweis: Testdaten.</strong> Dieses Raster ist synthetisch erzeugt " +
      "(<code>scripts/make_dev_grid.py</code>) und zeigt nur die Mechanik der Karte, keine echten Klimawerte. " +
      "Die echten ERA5-Daten liefert der GitHub-Action-Workflow <em>Klimadaten bauen</em>.";
  }
  $("#dataset-badge").textContent =
    `${META.dataset.n_points} Rasterpunkte · ERA5 ${META.dataset.start_date.slice(0, 4)}–${META.dataset.end_date.slice(0, 4)} · ${META.dataset.n_days} Tage`;
  $("#about-period").textContent = `${META.dataset.start_date.slice(0, 4)}–${META.dataset.end_date.slice(0, 4)}`;
  $("#about-source").textContent = META.dataset.source;
  $("#about-grid").textContent = `${String(META.dataset.grid_step_deg).replace(".", ",")}°`;
  $("#about-years").textContent = META.dataset.n_years;

  const sel = $("#month-select");
  monthNames.forEach((m, i) => sel.append(el("option", { value: i + 1, text: m })));
  sel.value = state.month;

  buildControls();
  applyParamsToControls();
  buildPresets();

  $("#mode-year").addEventListener("click", () => setMode("year"));
  $("#mode-month").addEventListener("click", () => setMode("month"));
  sel.addEventListener("change", () => { state.month = Number(sel.value); refresh(true); });
  $("#fixed-scale").addEventListener("change", (e) => { state.fixedScale = e.target.checked; refresh(true); });
  $("#btn-reset").addEventListener("click", () => {
    state.params = { ...META.defaults };
    applyParamsToControls();
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
  $("#month-picker").hidden = mode !== "month";
  if (!initial) refresh(true);
}

document.addEventListener("DOMContentLoaded", boot);
