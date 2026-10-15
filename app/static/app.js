/* Mein perfektes Klima – Europa
   Karte + Bedienung (Vanilla JS, Leaflet).

   Die Standard-OSM-Karte benötigt keinen API-Key. Zusätzliche Ortsnamen liegen
   über dem Klimaraster und stammen wie die Klimapunkte aus der eigenen API
   (GeoNames). Keine zweite externe Kachelquelle für Beschriftungen.
*/
"use strict";

const state = {
  mode: "year",              // "year" | "month" | "wq" (Winterquartier)
  period: "all",             // "all" | "recent5" | "2030" | "2050" (+ "-ssp126"/"-ssp585")
  month: 7,                  // 1..12
  params: { tmax_min: 18, tmax_max: 30, tmin_min: 5, tmin_max: 20, tdew_max: 18, sun_min: 3, prec_max: 99 },
  wq: {                      // Winterquartier: fester Wohnort + Ziel für die andere Jahreszeit
    season: "winter", home: null, // Wohnort {name, country, lat, lon}; Ausweichsaison
    sfrom: 5, sto: 9,        // Sommermonate (1..12); Winter = die übrigen Monate
  },
  fixedScale: false,
  showCities: true,
  selected: null,            // {lat, lon, name, country, report}
};

let META = null;             // /api/meta
let WQ_BOUNDS = {};          // META.winterquarters (Sommerfenster, Saisonlänge)
let GRID = null;             // letzte /api/map-Antwort
let map = null, overlay = null, borderLayer = null;
let osmLayer = null, placeLabelLayer = null, layerControl = null;
let cityLayer = null, cityData = [], markerIndex = new Map();
let wqLayer = null;          // Wohnort + vorgeschlagene Ziele auf der Karte
const CLIMATE_OVERLAY_OPACITY = 0.55;
let climateOpacity = CLIMATE_OVERLAY_OPACITY;
// Ab dieser Zoomstufe zeigt die OSM-Karte genauere Grenzen als unsere Linien.
const BORDER_MAX_ZOOM = 9;
let hoverTip = null, marker = null;
let pendingShareSelection = null;
let seq = 0, hoverQueued = false, refreshTimer = null;

const monthNames = ["Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember"];
const monthLetters = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

let CITY_CLIMATE_DATA = null;
let CITY_CLIMATE_LOADING = false;
let CITY_CLIMATE_LOADING_PERIOD = null;
let CITY_CLIMATE_REQUEST_SEQ = 0;
let cityPage = 0;
let cityPageSize = 100;
let citySort = { key: "name", direction: 1 };

const CITY_TABLE_COLUMNS = [
  { key: "name", label: "Stadt", type: "text", digits: 0, value: c => c.name },
  { key: "country", label: "Land", type: "text", digits: 0, value: c => c.country },
  { key: "pop", label: "Einwohner", digits: 0, sub: "GeoNames", value: c => c.pop },
  { key: "lat", label: "Breite", digits: 3, sub: "° N", value: c => c.lat },
  { key: "lon", label: "Länge", digits: 3, sub: "° E", value: c => c.lon },
  { key: "grid_distance_km", label: "Gitterabstand", digits: 1, sub: "km", value: c => c.grid_distance_km },
  { key: "tmax", label: "Ø Tmax", digits: 1, sub: "°C", value: c => c.climate.year.tmax },
  { key: "tmin", label: "Ø Tmin", digits: 1, sub: "°C", value: c => c.climate.year.tmin },
  { key: "tdew", label: "Ø Taupunkt", digits: 1, sub: "°C", value: c => c.climate.year.tdew },
  { key: "sun", label: "Sonne", digits: 1, sub: "h/Tag", value: c => c.climate.year.sun },
  { key: "prec", label: "Niederschlag", digits: 0, sub: "mm/Jahr", value: c => c.climate.year.prec },
  { key: "hot_days", label: "Hitzetage", indicator: "hot_days", shortThreshold: "Tmax >30 °C", digits: 1, value: c => c.climate.hot_days },
  { key: "very_hot_days", label: "Sehr heiße Tage", indicator: "very_hot_days", shortThreshold: "Tmax ≥35 °C", digits: 1, value: c => c.climate.very_hot_days },
  { key: "summer_days", label: "Sommertage", indicator: "summer_days", shortThreshold: "Tmax ≥25 °C", digits: 1, value: c => c.climate.summer_days },
  { key: "winter_days", label: "Wintertage", indicator: "winter_days", shortThreshold: "Tmin <0 °C", digits: 1, value: c => c.climate.winter_days },
  { key: "ice_days", label: "Eistage", indicator: "ice_days", shortThreshold: "Tmax <0 °C", digits: 1, value: c => c.climate.ice_days },
  { key: "tropical_nights", label: "Tropennächte", indicator: "tropical_nights", shortThreshold: "Tmin ≥20 °C", digits: 1, value: c => c.climate.tropical_nights },
  { key: "sultry_days", label: "Schwüle Tage", indicator: "sultry_days", shortThreshold: "Taupunkt >15 °C", digits: 1, value: c => c.climate.sultry_days },
  { key: "wet_days", label: "Regentage", indicator: "wet_days", shortThreshold: "≥1 mm/Tag", digits: 1, value: c => c.climate.wet_days },
  { key: "heavy_rain_days", label: "Starkregentage", indicator: "heavy_rain_days", shortThreshold: "≥10 mm/Tag", digits: 1, value: c => c.climate.heavy_rain_days },
  { key: "sunny_days", label: "Sonnentage", indicator: "sunny_days", shortThreshold: ">4 h/Tag", digits: 1, value: c => c.climate.sunny_days },
];

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
function activeMonth() { return state.mode === "month" ? state.month : null; }
function isWinterquarters() { return state.mode === "wq"; }

/** Zahl ohne überflüssige Nachkommastelle: 18 → „18“, 18,5 → „18,5“. */
function fmtNumber(v, digits = 1) {
  const r = Math.round(Number(v) * 10 ** digits) / 10 ** digits;
  return `${r < 0 ? "−" : ""}${Math.abs(r).toLocaleString("de-DE", { maximumFractionDigits: digits })}`;
}
function fmtTempRange(lo, hi) {
  return lo < 0 || hi < 0 ? `${fmtNumber(lo)} bis ${fmtNumber(hi)} °C` : `${fmtNumber(lo)}–${fmtNumber(hi)} °C`;
}
function fmtDelta(v, digits = 1) {
  return `${v >= 0 ? "+" : "−"}${num(Math.abs(v), digits)} °C`;
}
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g,
  ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

function encodeShareState(payload) {
  const encoded = encodeURIComponent(JSON.stringify(payload)).replace(/%([0-9A-F]{2})/g,
    (_match, byte) => String.fromCharCode(parseInt(byte, 16)));
  return btoa(encoded).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function currentSharePayload() {
  const selected = state.selected;
  if (!selected || !selected.report) return null;
  const city = selected.cityInfo || selected.report.city || {};
  const nearbyCity = Number(city.dist_km) < 40;
  return {
    v: 1,
    mode: state.mode,
    period: state.period,
    month: state.month,
    params: { ...state.params },
    selected: {
      lat: selected.lat,
      lon: selected.lon,
      name: selected.name || (nearbyCity ? city.name : "") || "",
      country: selected.country || (nearbyCity ? city.name_de : "") || "",
    },
  };
}

function currentShareUrl() {
  const payload = currentSharePayload();
  if (!payload) return null;
  const url = new URL("/share", location.origin);
  url.searchParams.set("state", encodeShareState(payload));
  return url.toString();
}

function currentShareCardUrl() {
  const link = currentShareUrl();
  if (!link) return null;
  const url = new URL(link);
  url.pathname = "/share/card.png";
  return url.toString();
}

function readSharedState() {
  const token = new URLSearchParams(location.search).get("state");
  if (!token) return;
  try {
    const binary = atob(token.replace(/-/g, "+").replace(/_/g, "/"));
    const encoded = Array.from(binary, char => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`).join("");
    const shared = JSON.parse(decodeURIComponent(encoded));
    if (shared.v !== 1 || !["year", "month", "wq"].includes(shared.mode)) throw new Error("ungültiger Modus");
    const period = periodInfo(shared.period);
    if (period && period.available) state.period = period.id;
    if (shared.mode === "month" && Number.isInteger(Number(shared.month))
        && Number(shared.month) >= 1 && Number(shared.month) <= 12) state.month = Number(shared.month);
    const limits = {
      tmax_min: [-40, 45], tmax_max: [-40, 45], tmin_min: [-45, 40], tmin_max: [-45, 40],
      tdew_max: [-20, 30], sun_min: [0, 16], prec_max: [0, 99],
    };
    for (const [key, [min, max]] of Object.entries(limits)) {
      const value = Number(shared.params && shared.params[key]);
      if (Number.isFinite(value) && value >= min && value <= max) state.params[key] = value;
    }
    const place = shared.selected || {};
    const lat = Number(place.lat), lon = Number(place.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      throw new Error("ungültiger Ort");
    }
    state.mode = shared.mode;
    pendingShareSelection = {
      lat, lon,
      name: String(place.name || "").slice(0, 100),
      country: String(place.country || "").slice(0, 100),
    };
  } catch {
    pendingShareSelection = null;
    setTimeout(() => toast("Dieser Ergebnis-Link ist leider ungültig."), 0);
  }
}

function trackPageView() {
  const path = location.pathname === "/share" ? "/share" : "/";
  fetch(`/api/analytics/pageview?path=${encodeURIComponent(path)}`, {
    method: "POST", credentials: "omit", cache: "no-store", keepalive: true, referrerPolicy: "no-referrer",
  }).catch(() => {});
}

/* Funnel-Ereignisse: nur eine feste Namensliste, keine Parameter, keine
   Nutzerkennung. Der Server verwirft unbekannte Namen (siehe app/analytics.py). */
function trackEvent(name) {
  if (!name) return;
  fetch(`/api/analytics/event?name=${encodeURIComponent(name)}`, {
    method: "POST", credentials: "omit", cache: "no-store", keepalive: true, referrerPolicy: "no-referrer",
  }).catch(() => {});
}

/** Rechtslinks in der Fußzeile nur zeigen, wenn Betreiberangaben hinterlegt sind. */
async function injectLegalLinks() {
  const host = $("#legal-links");
  if (!host) return;
  try {
    const site = await getJSON("/api/site");
    if (!site.legal_configured) return;
    host.replaceChildren(
      el("a", { href: "/impressum", text: "Impressum" }), " · ",
      el("a", { href: "/datenschutz", text: "Datenschutz" }), " · ");
    host.hidden = false;
  } catch { /* Ohne Statusauskunft bleibt die Fußzeile unverändert. */ }
}

function sharePayload() {
  const url = currentShareUrl();
  const selected = state.selected;
  if (!url || !selected) return null;
  const place = selected.name || selected.cityInfo?.name || selected.report?.city?.name || "Europa";
  const monthMode = state.mode === "month";
  const days = monthMode ? selected.report.days_per_month[state.month - 1] : selected.report.days_per_year;
  const unit = monthMode ? `im ${monthNames[state.month - 1]}` : "pro Jahr";
  return {
    url,
    place,
    title: `${fmtDays(days)} schöne Tage ${unit} in ${place}`,
    text: `Mein Wohlfühlwetter gibt es in ${place} an ${fmtDays(days)} Tagen ${unit}.`,
  };
}

async function shareCurrentResult() {
  const payload = sharePayload();
  if (!payload) return;
  trackEvent("share_click");
  if (typeof navigator.share === "function") {
    try { await navigator.share(payload); return; }
    catch (error) { if (error && error.name === "AbortError") return; }
  }
  try {
    await navigator.clipboard.writeText(payload.url);
    toast("Ergebnis-Link kopiert – mit Vorschau für Social Media.");
  } catch {
    toast(payload.url, 6500);
  }
}

/** Teilen ohne Umwege: normale Links auf die Netzwerk-Intents, kein Fremd-JS. */
function shareMenu() {
  const payload = sharePayload();
  if (!payload) return document.createDocumentFragment();
  const url = encodeURIComponent(payload.url);
  const text = encodeURIComponent(payload.text);
  const targets = [
    ["WhatsApp", `https://wa.me/?text=${text}%20${url}`],
    ["Telegram", `https://t.me/share/url?url=${url}&text=${text}`],
    ["X", `https://twitter.com/intent/tweet?text=${text}&url=${url}`],
    ["Facebook", `https://www.facebook.com/sharer/sharer.php?u=${url}`],
    ["Bluesky", `https://bsky.app/intent/compose?text=${text}%20${url}`],
    ["E-Mail", `mailto:?subject=${encodeURIComponent(payload.title)}&body=${text}%20${url}`],
  ];
  const links = targets.map(([label, href]) => el("a", {
    class: "ghost", href, target: "_blank", rel: "noopener noreferrer", text: label,
    onclick: () => trackEvent("share_target"),
  }));
  links.push(el("button", {
    type: "button", class: "ghost", text: "Link kopieren",
    onclick: async () => {
      trackEvent("share_copy");
      try { await navigator.clipboard.writeText(payload.url); toast("Ergebnis-Link kopiert."); }
      catch { toast(payload.url, 6500); }
    },
  }));
  return el("details", { class: "share-more" },
    el("summary", { text: "Weiter teilen" }),
    el("div", { class: "share-actions" }, links));
}

/** Optionaler Partner-Link: nur wenn serverseitig ein Vertrag konfiguriert ist. */
async function loadAffiliate(host, selected) {
  if (!host || !selected || !selected.report) return;
  const city = selected.cityInfo || selected.report.city || {};
  const params = new URLSearchParams({
    context: state.mode, lat: selected.lat, lon: selected.lon,
    name: selected.name || city.name || "", country: selected.country || city.name_de || "",
  });
  try {
    const data = await getJSON("/api/affiliate?" + params.toString());
    if (!data.link) return;
    const link = data.link;
    const box = el("div", { class: "partner-slot" },
      el("span", { class: "sponsor-label", text: `Anzeige · ${link.partner}` }),
      el("a", {
        class: "ghost sponsor-link", href: link.url, target: "_blank",
        rel: "sponsored nofollow noopener noreferrer", text: link.label,
        onclick: () => trackEvent("affiliate_click"),
      }),
      el("p", { class: "sub", text: link.note || "" }),
      el("p", { class: "sub", text: link.disclosure }));
    host.append(box);
  } catch { /* Ohne Konfiguration oder bei Fehlern bleibt der Platz unsichtbar. */ }
}

/* ------------------------------------------------------------ Klimazeitraum */
// Drei sprechende Wege statt SSP-Kürzeln; die Kürzel stehen nur noch klein dabei.
const SCENARIOS = ["ssp126", "ssp245", "ssp585"];
let lastScenario = "ssp245";

function periodInfo(id = state.period) {
  return ((META && META.periods) || []).find(period => period.id === id) || null;
}

function currentPeriodLabel() {
  const info = periodInfo();
  return info ? info.label : "Heute";
}

/** „now“ (gemessene Jahre) oder das Projektionsjahr „2030“/„2050“. */
function periodWhen(id = state.period) {
  const info = periodInfo(id);
  if (info && info.group) return info.group;
  return /^20[0-9]{2}/.test(id) ? id.slice(0, 4) : "now";
}

function scenarioOf(id) {
  const match = /-(ssp\d+)$/.exec(id || "");
  return match ? match[1] : "ssp245";
}

function periodIdFor(when, scenario) {
  if (when === "now") return $("#period-recent5")?.checked ? "recent5" : "all";
  return scenario === "ssp245" ? when : `${when}-${scenario}`;
}

function updatePeriodHelp() {
  const info = periodInfo();
  const help = $("#period-help");
  if (help) help.textContent = info && periodWhen() === "now" ? (info.summary || info.description || "") : "";
  const years = $("#about-years");
  if (years && info && info.years != null) years.textContent = num(info.analog_sample_years ?? info.years, 0);
  const warning = $("#projection-warning");
  warning.hidden = !info?.projection;
  warning.textContent = info?.projection
    ? "Modellrechnung, keine Vorhersage: das Wetter von heute plus die erwartete Erwärmung. "
      + "Sonne unverändert, Schwüle geschätzt." : "";
  $("#city-data-method").textContent = info?.projection
    ? "Modellrechnung für die Zukunft, keine Messwerte: Taupunkt geschätzt, Sonnenstunden unverändert."
    : "Klimamittel für den oben gewählten Zeitraum.";
}

function setupPeriodSelector() {
  const periods = (META && META.periods) || [];
  for (const button of document.querySelectorAll("#period-when button")) {
    const when = button.getAttribute("data-when");
    const available = when === "now" || periods.some(p => p.group === when && p.available);
    button.disabled = !available;
    if (!available) button.title = "Klimamodell-Daten sind gerade nicht verfügbar";
    button.addEventListener("click", () => chooseWhen(when));
  }
  const recent = periodInfo("recent5");
  if (recent && recent.start_date) {
    $("#period-recent-label").textContent =
      `Nur die letzten 5 Jahre (${recent.start_date.slice(0, 4)}–${recent.end_date.slice(0, 4)})`;
  }
  $("#period-recent5").addEventListener("change", (event) => selectPeriod(event.target.checked ? "recent5" : "all"));
  if (!periodInfo(state.period)?.available) state.period = META.default_period || "all";
  if (periodWhen() !== "now") lastScenario = scenarioOf(state.period);
  renderPeriodControls();
}

function chooseWhen(when) {
  if (when === periodWhen()) return;
  selectPeriod(periodIdFor(when, lastScenario));
}

/** Zeitraum-Knöpfe, „letzte 5 Jahre“ und die drei Klimaschutz-Wege aus dem Zustand zeichnen. */
function renderPeriodControls() {
  const id = state.period, when = periodWhen(id);
  document.querySelectorAll("#period-when button").forEach(button => {
    button.setAttribute("aria-checked", String(button.getAttribute("data-when") === when));
  });
  const recent = periodInfo("recent5");
  $("#period-recent-wrap").hidden = when !== "now" || !(recent && recent.available);
  $("#period-recent5").checked = id === "recent5";
  const list = $("#scenario-list"), host = $("#scenario-options");
  list.hidden = when === "now";
  host.replaceChildren();
  if (when !== "now") {
    let sample = null;
    for (const scenario of SCENARIOS) {
      const pid = periodIdFor(when, scenario);
      const info = periodInfo(pid);
      if (!info || !info.available) continue;
      sample = info;
      const input = el("input", { type: "radio", name: "scenario", value: scenario });
      input.checked = pid === id;
      input.addEventListener("change", () => {
        if (!input.checked) return;
        lastScenario = scenario;
        selectPeriod(pid);
      });
      host.append(el("label", { class: "scenario", title: `${info.scenario_code}: ${info.description || ""}` },
        input,
        el("span", { class: "sc-name", text: info.scenario_name || info.label }),
        el("span", { class: "sc-delta", text: info.warming_c != null ? fmtDelta(info.warming_c) : "" }),
        el("span", { class: "sc-text", text: info.scenario_summary || "" })));
    }
    if (sample) {
      const base = sample.baseline ? `${sample.baseline.start_year}–${sample.baseline.end_year}` : "heute";
      host.append(el("p", { class: "scenario-caption",
        text: `Zahl rechts: so viel wärmer als ${base} ist es ${sample.start_date.slice(0, 4)}–`
          + `${sample.end_date.slice(0, 4)} im Mittel über Europa.` }));
    }
  }
  updatePeriodHelp();
}

function selectPeriod(period) {
  const option = periodInfo(period);
  if (!option || !option.available || period === state.period) return;
  state.period = period;
  if (periodWhen(period) !== "now") lastScenario = scenarioOf(period);
  renderPeriodControls();
  invalidateCombination();
  updateHash();
  refresh(true);
  const details = $("#city-data");
  if (details && details.open) {
    CITY_CLIMATE_DATA = null;
    $("#city-table-body").replaceChildren();
    loadCityClimate(true);
  }
}

/** Einheit der Kartenwerte – am liebsten die des Servers (Legende/Hover). */
function monthUnit() {
  if (isWinterquarters()) return "Tage pro Jahr an einem Ort";
  if (GRID && GRID.unit_label) return GRID.unit_label;
  return state.mode === "month" ? `Tage im ${monthNames[state.month - 1]}` : "Tage pro Jahr";
}

/** Im Zwei-Orte-Modus zeigt Karte/Liste das Traumwetter an einem Einzelort. */
function wqQuery(q = new URLSearchParams()) {
  for (const [k, v] of Object.entries(state.params)) q.set(k, v);
  q.set("month", "all");
  q.set("period", state.period);
  return q;
}

/** Filterparameter der aktuellen Ansicht – für Karte, Orte, Listen und Suche. */
function filterQuery(q = new URLSearchParams()) {
  if (isWinterquarters()) return wqQuery(q);
  for (const [k, v] of Object.entries(state.params)) q.set(k, v);
  q.set("month", activeMonth() || "all");
  q.set("period", state.period);
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
    zoomControl: true, minZoom: 3, maxZoom: 19, worldCopyJump: false,
    attributionControl: true,
    maxBounds: L.latLngBounds([[bbox.lat_min - 6, bbox.lon_min - 8], [bbox.lat_max + 4, bbox.lon_max + 8]]),
    maxBoundsViscosity: 0.7,
  }).setView([52.5, 11], 4);

  // Standard-OSM wie vor der CARTO-Umstellung: kein Konto, Token oder Key.
  // Nur sichtbare Kacheln laden; Browser-Caching/Referer nicht unterdrücken.
  const borderPane = map.createPane("borders"); borderPane.style.zIndex = 450;
  const labelPane = map.createPane("placeLabels");
  labelPane.style.zIndex = 500;
  labelPane.style.pointerEvents = "none"; // Klick, Ziehen und Hover gehen zur Karte
  map.createPane("cities").style.zIndex = 550;
  osmLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap-Mitwirkende</a>',
    // OSM requires a Referer; override the page-wide no-referrer only for tiles.
    // Send only the origin, never paths, search parameters or climate profiles.
    referrerPolicy: "strict-origin-when-cross-origin",
    updateWhenIdle: true, keepBuffer: 2,
  }).addTo(map);
  placeLabelLayer = L.layerGroup([], {
    attribution: 'Orte: <a href="https://www.geonames.org/">GeoNames</a>',
  }).addTo(map);
  layerControl = L.control.layers({ "OpenStreetMap": osmLayer }, {}, {
    collapsed: true, position: "topleft",
  }).addTo(map);
  layerControl.addOverlay(placeLabelLayer, "Ortsnamen über Klimadaten");

  // Nur Landgrenzen als Linien (Küsten zeigt die Grundkarte). Früher kamen hier
  // Länderflächen, deren Schnittkanten am Kartenrand als gerade Striche auftauchten.
  fetch("/api/countries").then(r => r.json()).then(geo => {
    borderLayer = L.geoJSON(geo, {
      pane: "borders", interactive: false,
      style: { color: "#33415a", weight: 0.9, opacity: 0.6, fill: false },
    });
    if (map.getZoom() <= BORDER_MAX_ZOOM) borderLayer.addTo(map);
  }).catch(() => {});

  cityLayer = L.layerGroup([], { pane: "cities" }).addTo(map);

  hoverTip = el("div", { class: "leaflet-tooltip klima", style: "position:absolute;display:none;pointer-events:none;z-index:800" });
  map.getContainer().append(hoverTip);

  map.on("mousemove", onHover);
  map.on("mouseout", () => { hoverTip.style.display = "none"; });
  map.on("click", (e) => selectPlace(e.latlng.lat, e.latlng.lng));
  map.on("zoomend", () => {
    paintCities();
    if (!borderLayer) return;
    if (map.getZoom() > BORDER_MAX_ZOOM) map.removeLayer(borderLayer);
    else map.addLayer(borderLayer);
  });
  map.on("moveend zoomend", () => {
    paintPlaceLabels();
    const w = currentWidth();
    if (overlay && GRID && GRID.width && Math.abs(w - GRID.width) > 160) refresh();
  });
  map.on("overlayadd overlayremove", (event) => {
    if (event.layer === overlay) paintPlaceLabels();
  });
  map.on("resize", paintPlaceLabels);
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
      opacity: climateOpacity, interactive: false,
      className: "klima-overlay", pane: "overlayPane",
    }).addTo(map);
    if (layerControl) layerControl.addOverlay(overlay, "Klimadaten");
  } else {
    overlay.setUrl(url);
    overlay.setBounds(bounds());
  }
  fadeOverlayIn();
  paintPlaceLabels();
}

/** Neues Kartenbild weich einblenden, sobald es geladen ist.
 *  Der jsdom-Stub der Tests kennt `getElement` nicht – deshalb die Prüfung.
 *  Das Sicherheitsnetz sorgt dafür, dass das Bild auch dann sichtbar wird, wenn
 *  das load-Ereignis bereits vorbei ist (z. B. aus dem Browser-Cache). */
function fadeOverlayIn() {
  if (!overlay || typeof overlay.getElement !== "function") return;
  const img = overlay.getElement();
  if (!img || !img.style) return;
  img.style.opacity = "0";
  let shown = false;
  // Deckkraft erst beim Einblenden lesen: ein laufender Bildabruf darf eine
  // zwischenzeitliche Regleränderung nicht mit dem alten Wert überschreiben.
  const show = () => { if (shown) return; shown = true; img.style.opacity = String(climateOpacity); };
  img.addEventListener("load", show, { once: true });
  if (img.complete) requestAnimationFrame(show);
  setTimeout(show, 1500);
}

/** Nur Darstellung ändern, keine neue Klimaberechnung/PNG-Anfrage. */
function setClimateOpacity(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return;
  climateOpacity = Math.max(0, Math.min(0.9, number));
  $("#climate-opacity").value = Math.round(climateOpacity * 100);
  $("#climate-opacity-value").textContent = `${Math.round(climateOpacity * 100)} %`;
  if (overlay) overlay.setOpacity(climateOpacity);
  paintPlaceLabels();
}

/** Lokale, kontrastreiche Ortsnamen statt eines externen Labels-Tilediensts.
 *  OSM enthält weiterhin die vollständigen Orts-/Straßennamen. Diese ergänzende
 *  Ebene hält große Orte bei kräftiger Klimafarbe lesbar. Größere Städte zuerst;
 *  Bildschirmrechtecke verhindern Kollisionen auch nach Zoom und Verschieben. */
function paintPlaceLabels() {
  if (!placeLabelLayer || !map) return;
  placeLabelLayer.clearLayers();
  // Ohne Klimafläche reichen die Originalbeschriftungen der OSM-Karte.
  if (!overlay || !map.hasLayer(overlay) || climateOpacity === 0) return;
  const size = map.getSize();
  const boxes = [];
  for (const city of [...cityData].sort((a, b) => b.pop - a.pop)) {
    if (city.pop < cityZoomLimit()) continue;
    const point = map.latLngToContainerPoint([city.lat, city.lon]);
    const width = Math.min(180, 18 + [...city.name].length * 7);
    const box = { x: point.x + 8, y: point.y - 11, width, height: 22 };
    if (box.x < 0 || box.y < 0 || box.x + width > size.x || box.y + box.height > size.y) continue;
    if (boxes.some(b => box.x < b.x + b.width + 6 && box.x + width + 6 > b.x
        && box.y < b.y + b.height + 6 && box.y + box.height + 6 > b.y)) continue;
    boxes.push(box);
    placeLabelLayer.addLayer(L.marker([city.lat, city.lon], {
      pane: "placeLabels", interactive: false, keyboard: false,
      icon: L.divIcon({ className: "city-label", html: `<span>${escapeHtml(city.name)}</span>`,
        iconSize: [width, 22], iconAnchor: [-8, 11] }),
    }));
    if (boxes.length >= 80) break;
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
    paintPlaceLabels();
  } catch {
    cityData = [];
    paintCities();
    paintPlaceLabels();
  }
}

function paintCities() {
  if (!cityLayer || !map) return;
  cityLayer.clearLayers();
  if (!state.showCities || !cityData.length) return;
  const vmax = Math.max(1, (GRID && GRID.vmax) || 1);
  const limit = cityZoomLimit();
  const unit = monthUnit();
  let added = 0;
  for (const c of cityData) {
    if (c.pop < limit || added >= 420) continue;
    added++;
    const dot = L.circleMarker([c.lat, c.lon], {
      pane: "cities", radius: c.pop > 1_000_000 ? 5.4 : 3.9,
      color: "rgba(255,255,255,.85)", weight: 1.1,
      fillColor: colorFor(c.days / vmax), fillOpacity: 1,
      className: "city-marker",
    });
    dot.on("click", (e) => {
      if (e && e.originalEvent) e.originalEvent.stopPropagation();
      selectPlace(c.lat, c.lon, c.name, c.country);
    });
    dot.bindTooltip(
      `<strong>${fmtDays(c.days)}</strong> ${escapeHtml(unit)}<br>`
      + `<span class="sub">${escapeHtml(c.name)}${c.country ? ` · ${escapeHtml(c.country)}` : ""}</span>`,
      { direction: "top", offset: [0, -4], className: "klima city-tip", opacity: 1 });
    cityLayer.addLayer(dot);
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
      ? `bei ${city.name}${city.country ? " · " + city.country : ""}`
      : `${e.latlng.lat.toFixed(1)}° N, ${e.latlng.lng.toFixed(1)}° O · Rasterpunkt ${Math.round(distKm)} km entfernt`;
    hoverTip.innerHTML = `<strong>${fmtDays(days)}</strong> ${escapeHtml(monthUnit())}<br>`
      + `<span class="sub">${escapeHtml(label)}</span>`;
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

function monthBars(rep) {
  const maxBar = Math.max(1, ...rep.days_per_month);
  const wrap = el("div", { class: "bars" });
  rep.days_per_month.forEach((v, i) => {
    wrap.append(el("div", {
      class: "bar" + (state.mode === "month" && i === state.month - 1 ? " sel" : ""),
      style: `height:${Math.max(2, (v / maxBar) * 100)}%`,
      title: `${monthNames[i]}: ${fmtDays(v)} Tage – Klick zeigt diesen Monat auf der Karte`,
      onclick: () => { state.month = i + 1; setMode("month"); },
    }));
  });
  const labels = el("div", { class: "bar-labels" }, monthLetters.map(t => el("span", { text: t })));
  return [wrap, labels];
}

function climateDiagram(rep) {
  const c = (rep.climate && rep.climate.months) || null;
  if (!c) return null;
  const W = 306, H = 100, padL = 3, padR = 3, padT = 8, padB = 20;
  const lo = Math.floor(Math.min(0, ...c.tmin) / 5) * 5;
  const hi = Math.max(10, Math.ceil(Math.max(...c.tmax) / 5) * 5);
  const sunMax = Math.max(12, ...c.sun);
  const y = (t) => padT + (hi - t) / (hi - lo) * (H - padT - padB);
  const ys = (s) => padT + (1 - s / sunMax) * (H - padT - padB);
  const bw = (W - padL - padR) / 12;
  const parts = [`<svg viewBox="0 0 ${W} ${H}" class="climsvg" role="img" aria-label="Klimadiagramm">`];
  if (lo < 0) parts.push(`<line x1="${padL}" x2="${W - padR}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" class="zero"/>`);
  for (let i = 0; i < 12; i++) {
    const x = padL + i * bw;
    const yTop = y(c.tmax[i]), yBot = y(c.tmin[i]);
    const sel = state.mode === "month" && i === state.month - 1 ? " sel" : "";
    parts.push(
      `<g class="mcol${sel}"><title>${monthNames[i]}: tagsüber ${num(c.tmax[i], 1)} °C, nachts ${num(c.tmin[i], 1)} °C, `
      + `Sonne ${num(c.sun[i], 1)} h/Tag, Regen ${num(c.prec[i], 0)} mm</title>`
      + `<rect x="${(x + 1).toFixed(1)}" y="${Math.min(yTop, yBot).toFixed(1)}" width="${(bw - 2).toFixed(1)}" `
      + `height="${Math.max(2, Math.abs(yBot - yTop)).toFixed(1)}" rx="2" fill="${tempColor((c.tmax[i] + c.tmin[i]) / 2)}"/>`
      + `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 6}" class="mlab">${monthLetters[i]}</text></g>`);
  }
  const sunPath = c.sun.map((s, i) =>
    `${i === 0 ? "M" : "L"}${(padL + i * bw + bw / 2).toFixed(1)},${ys(s).toFixed(1)}`).join(" ");
  parts.push(`<path d="${sunPath}" class="sunline"/>`);
  c.sun.forEach((s, i) => parts.push(
    `<circle cx="${(padL + i * bw + bw / 2).toFixed(1)}" cy="${ys(s).toFixed(1)}" r="1.7" class="sundot"/>`));
  parts.push(`</svg>`);
  return el("div", { class: "climwrap", html: parts.join("") });
}

/** Sechs Kennzahlen, die man sofort versteht – der Rest steht eingeklappt darunter. */
function climateFacts(clim) {
  const year = clim.year || {};
  const fact = (label, value, title) => el("div", { title }, el("span", { text: label }), el("b", { text: value }));
  return el("div", { class: "facts" },
    fact("Tagsüber", `${num(year.tmax, 1)} °C`, "Mittlere Tageshöchsttemperatur im Jahr"),
    fact("Nachts", `${num(year.tmin, 1)} °C`, "Mittlere Tagestiefsttemperatur im Jahr"),
    fact("Sonne", `${num(year.sun, 1)} h/Tag`, "Mittlere Sonnenscheindauer pro Tag"),
    fact("Regen", `${num(year.prec, 0)} mm`, "Niederschlag pro Jahr"),
    fact("Sommertage", fmtDays(clim.summer_days || 0), "Tage mit mindestens 25 °C"),
    fact("Frosttage", fmtDays(clim.frost_days || 0), "Nächte mit 0 °C oder kälter"));
}

function moreClimate(clim) {
  const year = clim.year || {};
  const row = (label, value) => el("li", {}, el("span", { text: label }), el("b", { text: value }));
  return el("details", { class: "more-climate" },
    el("summary", { text: "Mehr Klimawerte" }),
    el("ul", { class: "more-facts" },
      row("Hitzetage (über 30 °C)", fmtDays(clim.hot_days || 0)),
      row("Tropennächte (nachts ab 20 °C)", fmtDays(clim.tropical_nights || 0)),
      row("Schwüle Tage (Taupunkt über 15 °C)", fmtDays(clim.sultry_days || 0)),
      row("Regentage (ab 1 mm)", fmtDays(clim.wet_days || 0)),
      row("Sonnentage (über 4 h Sonne)", fmtDays(clim.sunny_days || 0)),
      row("Eistage (ganztags unter 0 °C)", fmtDays(clim.ice_days || 0)),
      row("Taupunkt im Mittel", `${num(year.tdew, 1)} °C`)));
}

/** Ein Satz zur Zukunft: so viel wärmer wird es hier im Jahresmittel. */
function projectionNote(rep) {
  const data = rep.climate?.projection;
  if (!rep.period?.projection) return null;
  if (!data) return el("p", { class: "note period-basis", text: rep.period.label });
  const deltas = data.temperature_delta_c || [];
  const mean = deltas.reduce((a, b) => a + b, 0) / Math.max(1, deltas.length);
  const base = rep.period.baseline ? `${rep.period.baseline.start_year}–${rep.period.baseline.end_year}` : "heute";
  return el("p", { class: "note period-basis", text: `${rep.period.label}: `
    + `hier im Jahresmittel etwa ${fmtDelta(mean)} wärmer als ${base}.` });
}

function renderDetail(opts = {}) {
  const box = $("#detail");
  const sel = state.selected;
  if (!sel) { box.hidden = true; return; }
  box.hidden = false;
  if (opts.loading) {
    box.replaceChildren(el("h3", {}, el("span", { text: "Einen Moment …" }), closeBtn()),
      el("div", { class: "skeleton" }, el("span"), el("span"), el("span")));
    return;
  }
  if (opts.error) {
    box.replaceChildren(el("h3", {}, el("span", { text: "Das hat nicht geklappt" }), closeBtn()),
      el("p", { class: "sub", text: opts.error }));
    return;
  }

  const rep = sel.report;
  const mIdx = state.month - 1;
  const isMonth = state.mode === "month";
  const headline = isMonth ? rep.days_per_month[mIdx] : rep.days_per_year;
  const unit = isMonth ? `Tage im ${monthNames[mIdx]}` : "Tage pro Jahr";
  const clim = rep.climate || {};
  const place = sel.cityInfo && sel.cityInfo.dist_km > 1.5
    ? `nächster Ort: ${sel.cityInfo.name} (${num(sel.cityInfo.dist_km, 0)} km)`
    : `Rasterpunkt ${num(rep.lat, 2)}° N, ${num(rep.lon, 2)}° O`;
  const cityLine = place + (rep.grid_distance_km > 1 ? ` · Rasterpunkt ${Math.round(rep.grid_distance_km)} km entfernt` : "");

  const [bars, barLabels] = monthBars(rep);
  box.replaceChildren(
    el("h3", {},
      el("span", { text: (sel.name || "Ausgewählter Ort") + (sel.country ? ", " + sel.country : ""), class: "title" }),
      closeBtn()),
    el("div", { class: "big" }, fmtDays(headline), " ", el("small", { text: unit })),
    el("p", { class: "sub", text: isWinterquarters()
      ? `wenn du das ganze Jahr hier bist · mehr als an ${num(rep.percentile, 0)} % der Orte`
      : `mit deinem Wetter · mehr als an ${num(rep.percentile, 0)} % der Orte` }),
    rep.period?.projection ? projectionNote(rep)
      : el("p", { class: "sub period-basis", text: rep.period?.label || currentPeriodLabel() }),
    el("h4", { text: "Schöne Tage je Monat" }),
    bars, barLabels,
    el("h4", { html: `Klima hier <span class="muted">· Balken: nachts bis tagsüber (°C) · gelb: Sonne</span>` }),
    climateDiagram(rep) || el("p", { class: "sub", text: "keine Klimadaten" }),
    climateFacts(clim),
    moreClimate(clim),
    projectionDetails(rep),
    el("div", { class: "share-actions" },
      el("button", { type: "button", class: "ghost", onclick: shareCurrentResult }, "Ergebnis teilen"),
      el("a", { class: "ghost", href: currentShareCardUrl(), download: "mein-perfektes-klima-ergebnis.png" }, "Bild speichern")),
    shareMenu(),
    el("div", { class: "partner-host", id: "partner-slot" }),
    el("p", { class: "sub", style: "margin-top:.6rem", text: cityLine }),
  );
  loadAffiliate(document.getElementById("partner-slot"), sel);
}

function projectionDetails(rep) {
  if (!rep.period?.projection) return null;
  const data = rep.climate?.projection;
  const box = el("details", { class: "projection-detail" },
    el("summary", { text: "Zukunft: Annahmen und Modellstreuung" }),
    el("p", { text: "Taupunkt geschätzt bei gleicher relativer Feuchte; Sonne und Wetterabfolge wie 2016–2025. "
      + "Die Temperaturänderung gilt für die ganze Region, nicht für einzelne Tage." }));
  if (!data) return box;
  box.append(el("p", { text: `Region: ${data.region_name_de || data.region_name} · ${rep.period.model_count} Klimamodelle (${rep.period.scenario_code}). `
    + "Spanne: 10.–90. Perzentil der Temperaturänderung, kein Intervall der Wohlfühltage. Regenfaktor skaliert die Menge, nicht die Zahl der Regentage." }));
  const table = el("table", { class: "projection-table" },
    el("caption", { text: `Änderung gegenüber ${rep.period.baseline.start_year}–${rep.period.baseline.end_year}` }),
    el("thead", {}, el("tr", {}, ["Monat", "Wärmer", "Spanne", "Regen ×"].map(text => el("th", { scope: "col", text })))),
    el("tbody", {}, monthNames.map((month, i) => el("tr", {},
      el("th", { scope: "row", text: month }),
      el("td", { text: fmtDelta(data.temperature_delta_c[i]) }),
      el("td", { text: `${num(data.temperature_delta_p10_c[i], 1)} bis ${num(data.temperature_delta_p90_c[i], 1)}` }),
      el("td", { text: num(data.precipitation_ratio[i], 2) })))));
  box.append(el("div", { class: "projection-table-wrap" }, table),
    el("a", { href: rep.period.source_url, target: "_blank", rel: "noopener", text: "Datenquelle: IPCC-Atlas (CMIP6)" }));
  return box;
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
/**
 * Ortsfeld mit Vorschlagsliste – für die Kartensuche und den Wohnort.
 * Tastatur: ↑/↓ wählen, Enter übernimmt (auch bevor die Liste da ist), Esc schließt.
 * Die Suche selbst ist tolerant (Akzente, ae/oe/ue, deutsche und lokale Namen).
 */
function citySuggest({ input, list, area = "all", limit = 8, onPick, onType, onEnter }) {
  let timer = null, request = 0, items = [], active = -1, lastQuery = "";
  const options = () => [...list.querySelectorAll(".option")];
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const open = () => { list.hidden = false; input.setAttribute("aria-expanded", "true"); };
  const highlight = (i) => {
    active = i;
    options().forEach((node, j) => {
      node.classList.toggle("active", j === i);
      node.setAttribute("aria-selected", String(j === i));
    });
  };
  const pick = (city) => { request++; clearTimeout(timer); close(); onPick(city); };
  async function fetchCities(query) {
    const q = new URLSearchParams({ q: query, limit: String(limit), area });
    const data = await getJSON("/api/cities/search?" + q.toString());
    return data.cities || [];
  }
  function render(cities, query) {
    items = cities;
    list.replaceChildren();
    if (!cities.length) list.append(el("div", { class: "nohit", text: `Keine Stadt „${query}“ gefunden.` }));
    cities.forEach((city) => {
      const extra = [city.alias ? `(${city.alias})` : null, city.country].filter(Boolean).join(" · ");
      list.append(el("button", {
        type: "button", class: "option", role: "option", "aria-selected": "false",
        onmousedown: (event) => event.preventDefault(),   // Fokus bleibt im Feld
        onclick: () => pick(city),
      }, el("span", { class: "place", text: city.name }), el("span", { class: "region", text: extra })));
    });
    open();
    highlight(cities.length ? 0 : -1);
  }
  /** Freitext → Stadt: der beste Treffer (der Server sortiert genaue Treffer nach vorn). */
  async function resolve() {
    const query = input.value.trim();
    clearTimeout(timer);
    request++;                       // eine noch ausstehende Vorschlagsliste verwerfen
    close();
    if (query.length < 2) return null;
    if (query === lastQuery && items.length) return items[0];
    const cities = await fetchCities(query);
    return cities[0] || null;
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const query = input.value.trim();
    const current = ++request;
    if (onType) onType(query);
    if (query.length < 2) { items = []; lastQuery = ""; close(); return; }
    timer = setTimeout(async () => {
      try {
        const cities = await fetchCities(query);
        if (current !== request) return;
        lastQuery = query;
        render(cities, query);
      } catch { if (current === request) close(); }
    }, 150);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && items.length) {
      event.preventDefault(); open(); highlight((active + 1) % items.length);
    } else if (event.key === "ArrowUp" && items.length) {
      event.preventDefault(); open(); highlight((active - 1 + items.length) % items.length);
    } else if (event.key === "Escape") {
      close();
    } else if (event.key === "Enter") {
      event.preventDefault();
      const chosen = !list.hidden && items[active] ? items[active] : null;
      if (chosen) pick(chosen);
      if (onEnter) onEnter();
      else if (!chosen) resolve().then(city => { if (city) pick(city); }).catch(() => {});
    }
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
  return { resolve, close };
}

function initSearch() {
  const input = $("#city-search"), results = $("#search-results");
  if (!input) return;
  citySuggest({
    input, list: results, area: "all",
    onPick: (city) => {
      input.value = city.name;
      input.blur();
      map.flyTo([city.lat, city.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
      selectPlace(city.lat, city.lon, city.name, city.country);
    },
  });
}

/* Wohnort fürs Winterquartier: Tippen genügt – „Ziele finden“ nimmt den besten Treffer. */
let homeSuggest = null;
function initHomeSearch() {
  const input = $("#wq-home");
  homeSuggest = citySuggest({
    input, list: $("#wq-home-results"), area: "data",
    onType: (query) => {
      if (state.wq.home && query === state.wq.home.name) return;
      if (state.wq.home) { state.wq.home = null; updateHash(); }
      $("#wq-home-status").textContent = "";
      invalidateCombination();
    },
    onPick: setHome,
    // Enter = Stadt übernehmen und gleich suchen (ohne Auswahl nimmt die Suche den besten Treffer).
    onEnter: () => { if (!$("#btn-wq-search").disabled) searchCombination(); },
  });
}

function setHome(city, keepSearch = false) {
  state.wq.home = { name: city.name, country: city.country || "", lat: city.lat, lon: city.lon };
  $("#wq-home").value = city.name;
  if (homeSuggest) homeSuggest.close();
  if (!keepSearch) invalidateCombination();     // eine laufende Suche nicht abbrechen
  syncWqInputs();
  updateHash();
}

/* ------------------------------------------------------------------ Steuerung */
function rangeControl(cfg) {
  const wrap = el("div", { class: "ctrl" });
  const out = el("output");
  const head = el("div", { class: "ctrl-head" }, el("label", { text: cfg.label, title: cfg.help || "" }), out);
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
    out.textContent = cfg.format(Number(lo.value), Number(hi.value));
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
  wrap.append(el("div", { class: "ctrl-head" }, el("label", { text: cfg.label, title: cfg.help || "" }), out), box);
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

const controls = {};         // das eine Wohlfühlwetter-Profil in allen Modi

/** Fünf Regler für ein Wetterprofil in ``host`` aufbauen. */
function buildProfile(host, values, target) {
  const set = {};
  set.tmax = rangeControl({
    label: "Tagsüber (Höchstwert)", min: -20, max: 45, step: 0.5,
    help: "Höchsttemperatur des Tages",
    lo: values.tmax_min, hi: values.tmax_max, format: (a, b) => fmtTempRange(a, b),
  });
  set.tmin = rangeControl({
    label: "Nachts (Tiefstwert)", min: -30, max: 30, step: 0.5,
    help: "Tiefsttemperatur der Nacht",
    lo: values.tmin_min, hi: values.tmin_max, format: (a, b) => fmtTempRange(a, b),
  });
  set.tdew = singleControl({
    label: "Schwüle: Taupunkt bis", min: 0, max: 26, step: 0.5,
    help: "Der Taupunkt misst die Luftfeuchte: unter 16 °C fühlt es sich angenehm an, ab etwa 18 °C schwül.",
    value: values.tdew_max, format: (v) => (v >= 26 ? "egal" : `${fmtNumber(v)} °C`),
  });
  set.sun = singleControl({
    label: "Sonne mindestens", min: 0, max: 12, step: 0.5,
    value: values.sun_min, format: (v) => (v <= 0 ? "egal" : `${fmtNumber(v)} h am Tag`),
  });
  set.prec = singleControl({
    label: "Regen höchstens", min: 0, max: 20, step: 0.5,
    value: Math.min(values.prec_max, 20), format: (v) => (v >= 20 ? "egal" : `${fmtNumber(v)} mm am Tag`),
  });
  host.append(set.tmax, set.tmin, set.tdew, set.sun, set.prec);
  set._target = target;
  return set;
}

function buildControls() {
  Object.assign(controls, buildProfile($("#controls"), state.params, "params"));
}

/** Reglerwerte des Traumwetter-Profils in den Zustand übernehmen. */
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
}

/** Zustand -> Regler (nach Presets, Zurücksetzen oder Link). */
function writeProfile(set, values) {
  set.tmax._set(values.tmax_min, values.tmax_max);
  set.tmin._set(values.tmin_min, values.tmin_max);
  set.tdew._set(values.tdew_max);
  set.sun._set(values.sun_min);
  set.prec._set(values.prec_max >= 90 ? 20 : values.prec_max);
}

function applyParamsToControls() {
  writeProfile(controls, state.params);
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
        invalidateCombination();
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
}

/* ------------------------------------------------------------------ Refresh */
function queueRefresh(ms = 220) {
  syncParams();
  invalidateCombination();
  document.querySelectorAll("#presets .preset").forEach(b => b.classList.remove("active"));
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => refresh(), ms);
}

async function refresh(immediate = false) {
  if (immediate) { clearTimeout(refreshTimer); return refresh(); }
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
  if (!isWinterquarters()) loadRanking();
  if (state.selected) selectPlace(state.selected.lat, state.selected.lon, state.selected.name, state.selected.country);
}

async function loadRanking() {
  const q = filterQuery(new URLSearchParams({ limit: 10 }));
  const host = $("#ranking");
  try {
    const data = await getJSON("/api/ranking?" + q);
    host.replaceChildren();
    if (!data.cities.length) { host.append(el("li", { class: "muted", text: "Kein Ort passt – lockere die Einstellungen etwas." })); return; }
    data.cities.forEach((c, i) => {
      host.append(el("li", {},
        el("button", {
          type: "button",
          title: `${c.name}: ${fmtDays(c.days)} ${monthUnit()} (Rasterpunkt ${c.dist_km} km entfernt)`,
          onclick: () => {
            map.flyTo([c.lat, c.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
            selectPlace(c.lat, c.lon, c.name, c.country);
          },
        },
          el("span", { class: "rank", text: String(i + 1) }),
          el("span", { class: "who" },
            el("span", { class: "place", text: c.name }),
            el("span", { class: "region", text: c.country || "" })),
          el("span", { class: "days", text: fmtDays(c.days) }))));
    });
  } catch (err) {
    host.replaceChildren(el("li", { class: "muted", text: `Fehler: ${err.message || err}` }));
  }
}

function renderLegend(info) {
  const legend = $("#legend");
  legend.hidden = false;
  const stops = META.color_stops.map(([f, r, g, b]) => `rgb(${r},${g},${b}) ${Math.round(f * 100)}%`).join(", ");
  $("#legend-bar").style.background = `linear-gradient(90deg, ${stops})`;
  $("#legend-ticks").replaceChildren(...info.legend_ticks.map(t => el("span", { text: fmtDays(t) })));
  $("#legend-title").replaceChildren(
    document.createTextNode(`${monthUnit()} mit deinem Wetter`),
    el("small", { text: currentPeriodLabel() + (info.fixed_scale ? " · feste Skala" : "") }));
}

/* ------------------------------------------------------- Alle Städte / Tabelle */
function normalizeCityText(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("de");
}

function cityTableRows() {
  if (!CITY_CLIMATE_DATA) return [];
  const query = normalizeCityText($("#city-climate-search").value);
  const column = CITY_TABLE_COLUMNS.find(c => c.key === citySort.key) || CITY_TABLE_COLUMNS[0];
  const rows = (CITY_CLIMATE_DATA.cities || []).filter(city => {
    if (!query) return true;
    const haystack = [city.name, city.country, city.country_en, ...(city.aliases || [])].join(" ");
    return normalizeCityText(haystack).includes(query);
  });
  rows.sort((a, b) => {
    const av = column.value(a), bv = column.value(b);
    let cmp;
    if (column.type === "text") {
      cmp = String(av || "").localeCompare(String(bv || ""), "de", { sensitivity: "base", numeric: true });
    } else {
      cmp = (Number(av) || 0) - (Number(bv) || 0);
    }
    return cmp ? cmp * citySort.direction : String(a.name).localeCompare(String(b.name), "de");
  });
  return rows;
}

function cityColumnHeading(column) {
  const definition = column.indicator && CITY_CLIMATE_DATA.indicators
    ? CITY_CLIMATE_DATA.indicators[column.indicator] : null;
  return {
    label: definition ? definition.label : column.label,
    detail: definition ? (column.shortThreshold || definition.threshold) : (column.sub || ""),
    title: definition
      ? `${definition.label}: ${definition.threshold}; Mittelwert in ${definition.unit || "Tage/Jahr"}`
      : `${column.label}${column.sub ? ` (${column.sub})` : ""}; sortieren`,
  };
}

function renderCityTableHead() {
  const head = $("#city-table-head");
  head.replaceChildren();
  for (const column of CITY_TABLE_COLUMNS) {
    const heading = cityColumnHeading(column);
    const active = citySort.key === column.key;
    const arrow = active ? (citySort.direction > 0 ? "▲" : "▼") : "↕";
    const button = el("button", {
      class: "city-sort", type: "button", "data-sort-key": column.key,
      title: heading.title,
      "aria-label": `${heading.label}, ${sortLabel(column)}`,
    },
      el("span", { class: "city-sort-label", text: heading.label }),
      heading.detail ? el("small", { text: heading.detail }) : null,
      el("span", { class: "city-sort-arrow", "aria-hidden": "true", text: arrow }));
    head.append(el("th", {
      scope: "col",
      "aria-sort": active ? (citySort.direction > 0 ? "ascending" : "descending") : "none",
    }, button));
  }
}

function sortLabel(column) {
  return column.type === "text" ? "alphabetisch sortieren" : "nach Wert sortieren";
}

function cityCsvHeading(column) {
  const heading = cityColumnHeading(column);
  return heading.detail ? `${heading.label} (${heading.detail})` : heading.label;
}

function cityFormattedValue(city, column) {
  const value = column.value(city);
  if (value === null || value === undefined || value === "") return "–";
  if (typeof value === "number") return num(value, column.digits ?? 0);
  return String(value);
}

function renderCityTable() {
  if (!CITY_CLIMATE_DATA) return;
  const all = cityTableRows();
  const total = (CITY_CLIMATE_DATA.cities || []).length;
  const pageCount = Math.max(1, Math.ceil(all.length / cityPageSize));
  cityPage = Math.max(0, Math.min(cityPage, pageCount - 1));
  const start = cityPage * cityPageSize;
  const visible = all.slice(start, start + cityPageSize);
  const body = $("#city-table-body");
  const fragment = document.createDocumentFragment();

  for (const city of visible) {
    const row = el("tr");
    for (const column of CITY_TABLE_COLUMNS) {
      const cell = el("td");
      if (column.key === "name") {
        cell.append(el("button", {
          class: "city-open", type: "button",
          title: `${city.name}${city.country ? `, ${city.country}` : ""} auf der Karte anzeigen`,
          onclick: () => {
            if (map) map.flyTo([city.lat, city.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
            selectPlace(city.lat, city.lon, city.name, city.country);
            document.querySelector("main").scrollIntoView?.({ behavior: "smooth", block: "start" });
          },
        }, city.name));
      } else {
        cell.textContent = cityFormattedValue(city, column);
      }
      row.append(cell);
    }
    fragment.append(row);
  }
  body.replaceChildren(fragment);

  const count = $("#city-data-count");
  count.textContent = all.length === total
    ? `${num(total)} Städte` : `${num(all.length)} von ${num(total)} Städten`;
  $("#city-csv-download").disabled = all.length === 0;
  const status = $("#city-data-status");
  status.hidden = all.length > 0;
  status.textContent = all.length ? "" : "Keine Städte zu dieser Suche gefunden.";

  const pagination = $("#city-data-pagination");
  pagination.hidden = pageCount <= 1 || all.length === 0;
  $("#city-page-prev").disabled = cityPage === 0;
  $("#city-page-next").disabled = cityPage >= pageCount - 1;
  const first = all.length ? start + 1 : 0;
  const last = Math.min(start + visible.length, all.length);
  $("#city-page-info").textContent = `Seite ${num(cityPage + 1)} von ${num(pageCount)} · ${num(first)}–${num(last)} von ${num(all.length)}`;
  renderCityTableHead();
}

async function loadCityClimate(force = false) {
  const periodId = state.period;
  if (!force && CITY_CLIMATE_DATA && CITY_CLIMATE_DATA.period?.id === periodId) return;
  if (CITY_CLIMATE_LOADING && CITY_CLIMATE_LOADING_PERIOD === periodId) return;
  const request = ++CITY_CLIMATE_REQUEST_SEQ;
  CITY_CLIMATE_LOADING = true;
  CITY_CLIMATE_LOADING_PERIOD = periodId;
  const status = $("#city-data-status");
  const retry = $("#city-data-retry");
  status.hidden = false;
  status.textContent = `Klimadaten für alle Städte (${currentPeriodLabel()}) werden geladen …`;
  retry.hidden = true;
  try {
    const data = await getJSON(`/api/cities/climate?period=${encodeURIComponent(periodId)}`);
    if (request !== CITY_CLIMATE_REQUEST_SEQ || state.period !== periodId) return;
    CITY_CLIMATE_DATA = data;
    const period = data.period || {};
    const dates = period.label || `${(period.start_date || "").slice(0, 4)}–${(period.end_date || "").slice(0, 4)}`;
    const years = period.years ? ` · ${num(period.years, 1)} Jahre` : "";
    $("#city-data-summary").textContent = `${num(data.city_count || 0)} Städte · ${dates}${years}`;
    $("#city-data-tools").hidden = false;
    $("#city-table-wrap").hidden = false;
    status.hidden = true;
    renderCityTable();
  } catch (err) {
    if (request !== CITY_CLIMATE_REQUEST_SEQ) return;
    status.hidden = false;
    status.textContent = `Städtedaten konnten nicht geladen werden: ${err.message || err}`;
    retry.hidden = false;
  } finally {
    if (request === CITY_CLIMATE_REQUEST_SEQ) {
      CITY_CLIMATE_LOADING = false;
      CITY_CLIMATE_LOADING_PERIOD = null;
    }
  }
}

function downloadCityClimateCsv() {
  const rows = cityTableRows();
  if (!rows.length) return;
  const escape = value => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [
    CITY_TABLE_COLUMNS.map(cityCsvHeading),
    ...rows.map(city => CITY_TABLE_COLUMNS.map(column => {
      const value = column.value(city);
      return typeof value === "number" ? num(value, column.digits ?? 0) : (value ?? "");
    })),
  ];
  const period = CITY_CLIMATE_DATA?.period;
  if (period?.projection) {
    lines[0].push("Datenart", "Projektionszeitraum", "Szenario", "Methodik / Annahmen", "Quelle");
    for (const row of lines.slice(1)) row.push("Modell-basierte Näherung", period.label, period.scenario,
      period.assumptions.join(" "), period.source_url);
  }
  const csv = "\uFEFF" + lines.map(line => line.map(escape).join(";")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = el("a", { href: url, download: `klimadaten-staedte-${state.period}.csv` });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function initCityClimateTable() {
  const details = $("#city-data");
  const summary = details && details.querySelector("summary");
  if (!details || !summary) return;
  details.addEventListener("toggle", () => { if (details.open) loadCityClimate(); });
  // Auch Browser ohne ein zuverlässiges <details>-toggle-Ereignis laden beim Öffnen.
  summary.addEventListener("click", () => setTimeout(() => {
    if (details.open) loadCityClimate();
  }, 0));
  $("#city-data-retry").addEventListener("click", loadCityClimate);
  $("#city-climate-search").addEventListener("input", () => { cityPage = 0; renderCityTable(); });
  $("#city-page-size").addEventListener("change", e => {
    cityPageSize = Number(e.target.value) || 100;
    cityPage = 0;
    renderCityTable();
  });
  $("#city-table-head").addEventListener("click", e => {
    const button = e.target.closest("button[data-sort-key]");
    if (!button) return;
    const key = button.getAttribute("data-sort-key");
    const column = CITY_TABLE_COLUMNS.find(c => c.key === key);
    if (!column) return;
    if (citySort.key === key) citySort.direction *= -1;
    else {
      citySort.key = key;
      citySort.direction = column.type === "text" ? 1 : -1;
    }
    cityPage = 0;
    renderCityTable();
  });
  $("#city-page-prev").addEventListener("click", () => {
    if (cityPage > 0) { cityPage--; renderCityTable(); }
  });
  $("#city-page-next").addEventListener("click", () => {
    const pageCount = Math.ceil(cityTableRows().length / cityPageSize);
    if (cityPage + 1 < pageCount) { cityPage++; renderCityTable(); }
  });
  $("#city-csv-download").addEventListener("click", downloadCityClimateCsv);
}

/* -------------------------------------------------------------------- State */
function updateHash() {
  const p = new URLSearchParams();
  p.set("mode", state.mode);
  p.set("period", state.period);
  p.set("month", state.month);
  for (const [k, v] of Object.entries(state.params)) p.set(k, v);
  p.set("sfrom", state.wq.sfrom);
  p.set("sto", state.wq.sto);
  p.set("season", state.wq.season);
  if (state.wq.home) {
    p.set("home_lat", state.wq.home.lat); p.set("home_lon", state.wq.home.lon);
    p.set("home_name", state.wq.home.name);
  }
  if (state.fixedScale) p.set("fixed", "1");
  history.replaceState(null, "", "#" + p.toString());
}

function readHash() {
  if (!location.hash.length) return;
  const p = new URLSearchParams(location.hash.slice(1));
  if (["year", "month", "wq"].includes(p.get("mode"))) state.mode = p.get("mode");
  const hashPeriod = periodInfo(p.get("period"));
  if (hashPeriod && hashPeriod.available) state.period = hashPeriod.id;
  if (p.get("month")) state.month = Number(p.get("month")) || 7;
  for (const k of Object.keys(state.params)) if (p.has(k)) state.params[k] = Number(p.get(k));
  const sfrom = Number(p.get("sfrom")), sto = Number(p.get("sto"));
  if (Number.isInteger(sfrom) && sfrom >= 1 && sfrom <= 12) state.wq.sfrom = sfrom;
  if (Number.isInteger(sto) && sto >= 1 && sto <= 12) state.wq.sto = sto;
  if (["winter", "summer"].includes(p.get("season"))) state.wq.season = p.get("season");
  const lat = Number(p.get("home_lat")), lon = Number(p.get("home_lon"));
  if (p.has("home_lat") && p.has("home_lon") && Number.isFinite(lat) && Number.isFinite(lon)
      && lat >= 34 && lat <= 72 && lon >= -25 && lon <= 45) {
    state.wq.home = { lat, lon, name: p.get("home_name") || "Wohnort" };
  }
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
    : `Wetterdaten ${period} · ERA5 (Copernicus)`;
  badge.title = `${ds.n_points} Rasterpunkte · ${ds.n_days} Tage · Raster ${String(ds.grid_step_deg).replace(".", ",")}°`;
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
  $("#city-data-section").hidden = true;
  $("#building").hidden = false;
  if (payload && payload.state === "unusable") buildReasons = buildReasons.length
    ? buildReasons : ["Datensatz nicht verwendbar"];
  await pollBuild(true);
}

/* ------------------------------------------------------------------- Start */
async function boot() {
  trackPageView();
  injectLegalLinks();
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
  readSharedState();
  state.params = { ...META.defaults, ...state.params };
  WQ_BOUNDS = META.winterquarters || {};
  showDataStatus();
  setupPeriodSelector();
  $("#city-data-section").hidden = false;

  const sel = $("#month-select");
  monthNames.forEach((m, i) => sel.append(el("option", { value: i + 1, text: m })));
  sel.value = state.month;

  // Sommermonate fürs Winterquartier
  for (const id of ["#wq-sfrom", "#wq-sto"]) {
    const box = $(id);
    if (box) monthNames.forEach((m, i) => box.append(el("option", { value: i + 1, text: m })));
  }

  buildControls();
  applyParamsToControls();
  buildAllPresets();
  initSearch();
  initHomeSearch();
  initCityClimateTable();

  $("#mode-year").addEventListener("click", () => setMode("year"));
  $("#mode-month").addEventListener("click", () => setMode("month"));
  $("#mode-wq").addEventListener("click", () => setMode("wq"));
  sel.addEventListener("change", () => { state.month = Number(sel.value); refresh(true); });
  $("#wq-sfrom").addEventListener("change", setSummerWindow);
  $("#wq-sto").addEventListener("change", setSummerWindow);
  document.querySelectorAll("#wq-season button").forEach(button => button.addEventListener("click", () => {
    const season = button.getAttribute("data-season");
    if (season === state.wq.season) return;
    state.wq.season = season;
    invalidateCombination(); syncWqInputs(); updateHash();
  }));
  $("#btn-wq-search").addEventListener("click", searchCombination);
  $("#fixed-scale").checked = state.fixedScale;
  $("#fixed-scale").addEventListener("change", (e) => { state.fixedScale = e.target.checked; refresh(true); });
  $("#show-cities").addEventListener("change", (e) => { state.showCities = e.target.checked; paintCities(); });
  $("#climate-opacity").addEventListener("input", (e) => setClimateOpacity(Number(e.target.value) / 100));
  setClimateOpacity(climateOpacity);
  $("#btn-reset").addEventListener("click", () => {
    setClimateOpacity(CLIMATE_OVERLAY_OPACITY);
    state.params = { ...META.defaults };
    state.period = META.default_period || "all";
    lastScenario = "ssp245";
    renderPeriodControls();
    const summer = WQ_BOUNDS.summer_months || [5, 6, 7, 8, 9];
    state.wq.home = null;
    state.wq.season = "winter";
    $("#wq-home").value = "";
    $("#wq-home-status").textContent = "";
    state.wq.sfrom = summer[0];
    state.wq.sto = summer[summer.length - 1];
    invalidateCombination();
    applyParamsToControls();
    syncWqInputs();
    document.querySelectorAll("#presets .preset").forEach(b => b.classList.remove("active"));
    refresh(true);
    if ($("#city-data").open) {
      CITY_CLIMATE_DATA = null;
      $("#city-table-body").replaceChildren();
      loadCityClimate(true);
    }
  });
  $("#btn-link").addEventListener("click", async () => {
    const resultLink = currentShareUrl();
    const link = resultLink || location.href;
    try { await navigator.clipboard.writeText(link); toast(resultLink ? "Ergebnis-Link kopiert" : "Link kopiert"); }
    catch { toast(link, 6500); }
  });
  setMode(state.mode, true);

  initMap();
  await refresh(true);
  if (pendingShareSelection) {
    const selected = pendingShareSelection;
    pendingShareSelection = null;
    map.setView([selected.lat, selected.lon], Math.max(map.getZoom(), 6), { animate: false });
    await selectPlace(selected.lat, selected.lon, selected.name, selected.country);
  }
}

const MODE_HINTS = {
  year: "Wo gibt es übers Jahr die meisten Tage mit deinem Wetter?",
  month: "Wo ist dein Wetter im gewählten Monat am häufigsten?",
  wq: "Bleib zu Hause, wenn es dort schön ist – und finde für den Rest des Jahres ein Ziel mit mehr schönen Tagen.",
};

function setMode(mode, initial = false) {
  state.mode = mode;
  $("#mode-year").setAttribute("aria-selected", String(mode === "year"));
  $("#mode-month").setAttribute("aria-selected", String(mode === "month"));
  $("#mode-wq").setAttribute("aria-selected", String(mode === "wq"));
  $("#mode-hint").textContent = MODE_HINTS[mode] || "";
  // Die Monatsauswahl gehört ausschließlich zum Reisemonat.
  $("#month-picker").hidden = mode !== "month";
  // Winterquartier-Karte nur in diesem Modus; die Rangliste dafür nur außerhalb.
  document.querySelectorAll("[data-wq-only]").forEach(node => { node.hidden = mode !== "wq"; });
  $("#rank-card").hidden = mode === "wq";
  $("#rank-title").textContent = mode === "month" ? `Die besten Orte im ${monthNames[state.month - 1]}` : "Die besten Orte";
  if (mode === "month") $("#month-select").value = state.month;
  if (mode === "wq") syncWqInputs();
  else clearWqMarkers();
  loadSponsor();
  if (!initial) refresh(true);
  else if (state.selected) renderDetail();
}

/* Optional first-party sponsors, loaded only by general page mode, never by home/profile. */
let sponsorSeq = 0;
async function loadSponsor() {
  const request = ++sponsorSeq;
  const host = $("#sponsor-slot");
  host.hidden = true; host.replaceChildren();
  try {
    const data = await getJSON("/api/ads?placement=sidebar&context=" + encodeURIComponent(state.mode));
    if (request !== sponsorSeq || !data.ad) return;
    const ad = data.ad;
    // Defence in depth; all labels are text nodes, never campaign HTML.
    const link = new URL(ad.url);
    if (link.protocol !== "https:" || link.username || link.password) return;
    host.append(el("span", { class: "sponsor-label", text: `Anzeige · ${ad.advertiser}` }),
      el("strong", { text: ad.title }), el("p", { text: ad.body }),
      el("a", {
        href: link.href, target: "_blank", rel: "sponsored nofollow noopener noreferrer", text: ad.cta,
        onclick: () => trackEvent("ad_click"),
      }));
    host.hidden = false;
  } catch { /* No campaign/network error never blocks the climate app. */ }
}

/* ------------------------------------------------------- Winterquartier-Modus */
let combinationSeq = 0;

function updateWqButton() {
  const button = $("#btn-wq-search");
  if (!button) return;
  const typed = ($("#wq-home")?.value || "").trim().length >= 2;
  button.disabled = (!state.wq.home && !typed) || summerMonths().length >= 12;
}

function clearWqMarkers() {
  if (wqLayer) wqLayer.clearLayers();
}

function invalidateCombination() {
  combinationSeq++;
  const out = $("#wq-result"), alternatives = $("#wq-alternatives");
  if (out) { out.hidden = true; out.replaceChildren(); }
  if (alternatives) { alternatives.hidden = true; alternatives.replaceChildren(); }
  clearWqMarkers();
  const button = $("#btn-wq-search");
  if (button) button.textContent = "Ziele finden";
  updateWqButton();
}

/** Monatsgrenzen anzeigen und in einem Satz sagen, wann man wo ist. */
function syncWqInputs() {
  document.querySelectorAll("#wq-season button").forEach(button => {
    button.setAttribute("aria-checked", String(button.getAttribute("data-season") === state.wq.season));
  });
  if (state.wq.home) {
    $("#wq-home").value = state.wq.home.name;
    $("#wq-home-status").textContent = `Wohnort: ${state.wq.home.name}${state.wq.home.country ? ", " + state.wq.home.country : ""}`;
  }
  const sfrom = $("#wq-sfrom"), sto = $("#wq-sto");
  if (sfrom) sfrom.value = state.wq.sfrom;
  if (sto) sto.value = state.wq.sto;
  const summer = summerMonths(), winter = winterMonths();
  const hint = $("#wq-window");
  if (hint) {
    hint.replaceChildren();
    if (summer.length >= 12) {
      hint.textContent = "Bitte höchstens 11 Monate wählen – sonst bleibt keine Zeit fürs Quartier.";
    } else if (state.wq.season === "winter") {
      hint.append(el("b", { text: monthsLabel(summer) }), " zu Hause, ",
        el("b", { text: monthsLabel(winter) }), ` im Winterquartier (${winter.length} Monate).`);
    } else {
      hint.append(el("b", { text: monthsLabel(winter) }), " zu Hause, ",
        el("b", { text: monthsLabel(summer) }), ` im Sommerquartier (${summer.length} Monate).`);
    }
  }
  updateWqButton();
}

function setSummerWindow() {
  state.wq.sfrom = Number($("#wq-sfrom").value);
  state.wq.sto = Number($("#wq-sto").value);
  invalidateCombination();
  syncWqInputs();
  updateHash();
  refresh(true);
}

/** Wohnort und vorgeschlagene Ziele als nummerierte Punkte auf der Karte. */
function showWqMarkers(home, destinations) {
  if (!map || !L.layerGroup) return;
  if (!wqLayer) wqLayer = L.layerGroup([], { pane: "cities" }).addTo(map);
  wqLayer.clearLayers();
  const points = [[home.lat, home.lon]];
  wqLayer.addLayer(L.marker([home.lat, home.lon], {
    pane: "cities", interactive: false, keyboard: false,
    icon: L.divIcon({ className: "wq-pin home", html: `<span>${escapeHtml(home.name)}</span>`, iconSize: null }),
  }));
  destinations.forEach((city, i) => {
    points.push([city.lat, city.lon]);
    const pin = L.marker([city.lat, city.lon], {
      pane: "cities", keyboard: false,
      icon: L.divIcon({ className: "wq-pin", html: `<span>${i + 1}</span>`, iconSize: [22, 22] }),
    });
    pin.on("click", () => selectPlace(city.lat, city.lon, city.name, city.country));
    pin.bindTooltip(`${i + 1}. ${escapeHtml(city.name)}`, { direction: "top", className: "klima", offset: [0, -10] });
    wqLayer.addLayer(pin);
  });
  // Platz lassen für Suchfeld (oben) und Legende (unten rechts).
  if (points.length > 1) {
    map.fitBounds(L.latLngBounds(points), { maxZoom: 6, paddingTopLeft: [30, 70], paddingBottomRight: [30, 120] });
  }
}

/** Sucht Ziele zu einem festen Wohnort; das Monatsfenster bleibt, wie es ist. */
async function searchCombination() {
  syncParams();
  const button = $("#btn-wq-search");
  const out = $("#wq-result"), alternatives = $("#wq-alternatives");
  const summer = summerMonths();
  const request = ++combinationSeq;
  const say = (text, cls) => {
    out.hidden = false;
    out.replaceChildren(el("p", { class: cls || "", text }));
  };
  alternatives.hidden = true;
  alternatives.replaceChildren();
  if (summer.length >= 12) {
    say("Bitte höchstens 11 Sommermonate wählen.", "error");
    return;
  }
  button.disabled = true;
  button.textContent = "Suche läuft …";
  try {
    // Getippt, aber nicht aus der Liste gewählt? Dann den besten Treffer nehmen.
    if (!state.wq.home) {
      const typed = $("#wq-home").value.trim();
      if (typed.length < 2) {
        say("Bitte gib zuerst ein, wo du wohnst.", "error");
        $("#wq-home").focus();
        return;
      }
      const city = homeSuggest ? await homeSuggest.resolve() : null;
      if (request !== combinationSeq) return;
      if (!city) {
        say(`Keine Stadt „${typed}“ im Kartengebiet gefunden. Tipp: deutscher oder örtlicher Name, z. B. „München“ oder „Milano“.`, "error");
        return;
      }
      setHome(city, true);
    }
    say("Ziele werden gesucht …");
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(state.params)) query.set(key, value);
    query.set("period", state.period);
    query.set("sfrom", state.wq.sfrom);
    query.set("sto", state.wq.sto);
    query.set("limit", 8);
    query.set("lat", state.wq.home.lat); query.set("lon", state.wq.home.lon);
    query.set("season", state.wq.season);
    updateHash();
    const data = await getJSON("/api/winterquarters/escape?" + query.toString());
    if (request !== combinationSeq) return;

    const best = data.best;
    if (!best) {
      say("Für dein Wetter und diese Monate wurde kein passendes Ziel gefunden.");
      return;
    }
    const awayKey = state.wq.season + "_city";
    const quarter = state.wq.season === "winter" ? "Winterquartier" : "Sommerquartier";
    const awayLabel = state.wq.season === "winter" ? data.winter_label : data.summer_label;
    const home = state.wq.home;
    out.hidden = false;
    out.replaceChildren(
      el("p", { class: "home-days" }, el("b", { text: home.name }), " allein: ",
        el("b", { text: fmtDays(data.baseline_days) }), " schöne Tage pro Jahr."),
      el("p", { text: `Mit einem ${quarter} (${awayLabel}) bis zu ${fmtDays(best.total_days)} Tage. `
        + "Die grüne Zahl zeigt, wie viele Tage du gewinnst:" }));

    const destinations = [];
    (data.candidates || []).forEach((candidate, index) => {
      const city = candidate[awayKey];
      destinations.push(city);
      const gain = candidate.gain_days;
      alternatives.append(el("li", { class: "combo-item" },
        el("button", {
          type: "button", class: "combo-place",
          title: `${home.name} + ${city.name}: ${fmtDays(candidate.total_days)} schöne Tage pro Jahr`,
          onclick: (event) => {
            alternatives.querySelectorAll("button").forEach(b => b.classList.remove("active"));
            event.currentTarget.classList.add("active");
            map.flyTo([city.lat, city.lon], Math.max(map.getZoom(), 6), { duration: 0.7 });
            selectPlace(city.lat, city.lon, city.name, city.country);
          },
        },
          el("span", { class: "rank", text: String(index + 1) }),
          el("span", { class: "who" },
            el("span", { class: "place", text: city.name }),
            el("span", { class: "region", text: `${city.country ? city.country + " · " : ""}${num(candidate.distance_km, 0)} km` })),
          el("span", { class: "gain" + (gain < 0 ? " neg" : "") },
            `${gain >= 0 ? "+" : "−"}${fmtDays(Math.abs(gain))}`,
            el("small", { text: `${fmtDays(candidate.total_days)} im Jahr` })))));
    });
    alternatives.hidden = !destinations.length;
    showWqMarkers(home, destinations);
  } catch (err) {
    if (request !== combinationSeq) return;
    say("Suche fehlgeschlagen: " + (err.message || err), "error");
  } finally {
    if (request === combinationSeq) {
      button.textContent = "Ziele finden";
      updateWqButton();
    }
  }
}

document.addEventListener("DOMContentLoaded", boot);
