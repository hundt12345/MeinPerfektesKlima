/**
 * Rauchtest der Statusseite – läuft ohne Server und ohne echte Daten.
 *
 * Die Website darf keine Karte mit ungeprüften Werten zeigen. Statt dessen
 * erscheint die Statusseite mit dem Baufortschritt des Workflows
 * „Klimadaten bauen". Hier wird genau das geprüft: Die API-Antworten werden
 * eingespielt (503 + Fortschritt), index.html und app.js kommen aus dem
 * Arbeitsverzeichnis, und am Ende muss der Balken mit Prozentwert, Phase,
 * Restzeit, Gründen und Logzeilen im DOM stehen.
 *
 * Aufruf:  node tests/ui_statusseite.mjs
 */
import { JSDOM, VirtualConsole } from "jsdom";
import { readFile } from "node:fs/promises";

const failures = [];
const ok = (cond, msg) => { console.log(`${cond ? "  ✓" : "  ✗"} ${msg}`); if (!cond) failures.push(msg); };

const ROOT = new URL("../", import.meta.url);
const HTML = await readFile(new URL("app/static/index.html", ROOT), "utf8");
const JS = await readFile(new URL("app/static/app.js", ROOT), "utf8");

const WORKFLOW = "https://github.com/hundt12345/MeinPerfektesKlima/actions/workflows/build-climate-data.yml";

/** Antwort der App, solange keine geprüften Daten vorliegen (HTTP 503). */
const META_FEHLT = {
  data_ready: false,
  state: "missing",
  reasons: ["Kein Klimaraster gefunden – bitte den Workflow 'Klimadaten bauen' ausführen"],
  allow_unverified: false,
};

/** Antwort der App, wenn ein synthetischer Datensatz abgelehnt wurde. */
const META_UNBRAUCHBAR = {
  data_ready: false,
  state: "unusable",
  reasons: [
    "Das Raster stammt aus synthetischen Testdaten (scripts/make_dev_grid.py), nicht aus ERA5",
    "Jahresgang unplausibel: Juli − Januar im Süden = -24.0 K",
    "Ø Abweichung der Monatsmittel ≤ 4.5 K: 20.02 K über 88 Werte",
    "Höchstens ein Drittel der Temperatur-Vergleiche außerhalb der Toleranz: 86/88",
    "Sonnenschein im Rahmen der Normalwerte: 20/22 Städte außerhalb ±2.5 h",
    "Südeuropa: Juli deutlich wärmer als Januar: Ø -24.9 K (Jul − Jan)",
    "Nordeuropa: Juli deutlich wärmer als Januar: Ø -37.1 K (Jul − Jan)",
    "Südspanien liegt über 100 Tagen/Jahr: 82",
  ],
  allow_unverified: false,
};

const BUILD_LAEUFT = {
  state: "building", percent: 42.5, phase: "Abruf",
  message: "Jahr 2020 gelesen (5 von 10 Jahren)",
  blocks_done: 5, blocks_total: 10, rate: "11 min/Jahr", eta_minutes: 55,
  source: "ERA5 (offene Zarr-Daten, Google Cloud)",
  progress_source: "GitHub-Zweig klima-fortschritt",
  age_seconds: 32, stale: false, workflow_url: WORKFLOW,
  log_tail: ["[10:00:00] Jahr 2019 fertig in 11 min", "[10:11:00] Jahr 2020: 366 Tage"],
  data_ready: false,
};

const BUILD_FEHLER = { ...BUILD_LAEUFT, state: "failed", percent: 60, phase: "Fehler",
  message: "Qualitätstor nicht bestanden", eta_minutes: null, log_tail: ["TOR NICHT BESTANDEN: 6 Fehler"] };

const BUILD_FERTIG = { ...BUILD_LAEUFT, state: "finished", percent: 100, phase: "Fertig",
  message: "Datensatz gebaut und geprüft", data_ready: true, eta_minutes: null };

/** Startseite in jsdom laden und die eingespielten API-Antworten prüfen. */
async function szenario({ meta, build, warten = 1500 }) {
  const virtualConsole = new VirtualConsole();
  const errors = [];
  virtualConsole.on("jsdomError", (e) => errors.push(e.message));
  virtualConsole.on("error", (m) => errors.push(String(m)));
  const dom = new JSDOM(HTML, { url: "https://beispiel.test/", runScripts: "outside-only",
    pretendToBeVisual: true, virtualConsole });
  const { window } = dom;
  window.fetch = async (url) => {
    const pfad = new URL(url, "https://beispiel.test/").pathname +
                 new URL(url, "https://beispiel.test/").search;
    if (pfad.startsWith("/api/build")) {
      return { ok: true, status: 200, json: async () => build };
    }
    if (pfad.startsWith("/api/meta")) {
      return { ok: false, status: 503, json: async () => meta };
    }
    throw new Error(`unerwarteter Aufruf: ${pfad}`);
  };
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
  window.L = { map: () => ({ on: () => ({ setView: () => ({}) }) }) };
  window.eval(JS);
  window.document.dispatchEvent(new window.Event("DOMContentLoaded", { bubbles: true }));

  const deadline = Date.now() + 8000;
  const bereit = () => {
    const b = window.document.querySelector("#build-title");
    return !window.document.querySelector("#building").hidden && (b.textContent || "").length > 3;
  };
  while (Date.now() < deadline && !bereit()) await new Promise((r) => setTimeout(r, 100));
  await new Promise((r) => setTimeout(r, warten));
  return { doc: window.document, window, errors };
}

const txt = (doc, sel) => (doc.querySelector(sel) || {}).textContent || "";

/* ------------------------------------------------- 1) Lauf mit Fortschritt */
console.log("Statusseite während des Datenlaufs:");
{
  const { doc, errors } = await szenario({ meta: META_FEHLT, build: BUILD_LAEUFT });
  ok(doc.querySelector("#building").hidden === false, "Statusseite sichtbar");
  ok(doc.querySelector("main").hidden === true, "Kartenbereich ausgeblendet (keine Zahlen ohne Daten)");
  ok(txt(doc, "#build-title").includes("aufgebaut"), "Titel: " + txt(doc, "#build-title"));
  const bar = doc.querySelector("#build-bar");
  ok(bar.style.width === "42.5%", `Balkenbreite 42.5 % (ist: ${bar.style.width || "leer"})`);
  ok(txt(doc, "#build-percent").includes("42,5"), "Prozentanzeige: " + txt(doc, "#build-percent"));
  ok(doc.querySelector("#build-bar-wrap").getAttribute("aria-valuenow") === "43", "aria-valuenow gesetzt");
  ok(!doc.querySelector("#build-bar-wrap").classList.contains("indeterminate"), "Balken bestimmt, nicht flatternd");
  const meta = txt(doc, "#build-meta").replace(/\s+/g, " ");
  ok(meta.includes("Abruf"), "Phase im Klartext: " + meta.slice(0, 70));
  ok(meta.includes("5 von 10"), "Teilabschnitte: " + (meta.match(/\d+ von \d+/) || [""])[0]);
  ok(meta.includes("55 min"), "Restzeit: " + (meta.match(/Restzeit \(ca\.\)\s*[^A-Za-z]*/) || [""])[0].trim());
  ok(meta.includes("klima-fortschritt"), "Herkunft des Fortschritts genannt");
  ok(!doc.querySelector("#build-log").hidden && txt(doc, "#build-log").includes("2020"), "Logzeilen sichtbar");
  ok(txt(doc, "#build-reasons").includes("Klimadaten bauen"), "Grund der Leere erklärt");
  ok(doc.querySelector("#build-hint a").getAttribute("href") === WORKFLOW, "Link zum Workflow");
  ok(errors.length === 0, `keine Browser-Fehler (${errors.slice(0, 2).join(" | ")})`);
}

/* ---------------------------------------------- 2) Abgelehnte Testdaten */
console.log("\nAbgelehnter synthetischer Datensatz:");
{
  const { doc } = await szenario({ meta: META_UNBRAUCHBAR, build: { ...BUILD_LAEUFT, state: "not_started", percent: null } });
  ok(txt(doc, "#build-title").includes("abgelehnt"), "Titel: " + txt(doc, "#build-title"));
  const gruende = [...doc.querySelectorAll("#build-reasons li")].map(li => li.textContent);
  ok(gruende.length === 6, `Gründe gekürzt dargestellt (${gruende.length} Einträge für 8 Meldungen)`);
  ok(gruende[0].includes("synthetisch"), "Erster Grund: " + gruende[0].slice(0, 60));
  ok(gruende.at(-1).includes("3 weitere"), "Hinweis auf weitere Gründe: " + gruende.at(-1));
  ok(doc.querySelector("#build-bar-wrap").classList.contains("indeterminate"), "Balken unbestimmt, solange nichts läuft");
  ok(txt(doc, "#build-percent").includes("warten"), "Klartext statt leerem Balken: " + txt(doc, "#build-percent"));
}

/* ------------------------------------------------------ 3) Fehlgeschlagen */
console.log("\nFehlgeschlagener Datenlauf:");
{
  const { doc } = await szenario({ meta: META_FEHLT, build: BUILD_FEHLER, warten: 800 });
  ok(txt(doc, "#build-title").includes("fehlgeschlagen"), "Titel: " + txt(doc, "#build-title"));
  ok(doc.querySelector("#build-bar-wrap").classList.contains("warn"), "Balken in Warnfarbe");
  ok(txt(doc, "#build-log").includes("TOR NICHT BESTANDEN"), "Fehlermeldung im Log");
}

/* ------------------------------------------- 4) Fertig -> Seite lädt neu */
console.log("\nDatenlauf fertig (Website übernimmt automatisch):");
{
  const { doc, errors } = await szenario({ meta: META_FEHLT, build: BUILD_FERTIG, warten: 1600 });
  ok(txt(doc, "#build-title").includes("fertig"), "Titel: " + txt(doc, "#build-title"));
  ok(doc.querySelector("#build-bar").style.width === "100%", "Balken auf 100 %");
  ok(errors.some(e => /navigation|reload/i.test(e)), "Seite lädt neu, sobald die Daten da sind");
}

console.log(failures.length ? `\n${failures.length} Fehler:\n - ${failures.join("\n - ")}` : "\nAlle Prüfungen bestanden");
process.exit(failures.length ? 1 : 0);
