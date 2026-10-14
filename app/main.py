"""FastAPI-Anwendung: „Mein perfektes Klima – Europa“.

  GET /                 Startseite mit Karte (bzw. Statusseite, solange keine
                        echten ERA5-Daten vorliegen)
  GET /api/meta         Datensatz-Infos, Standardwerte, Voreinstellungen
  GET /api/build        Live-Fortschritt des Datenbaus (GitHub-Action)
  GET /api/map          Statistik + Rasterwerte für die aktuellen Parameter
  GET /api/map.png      Kartenbild (Web-Mercator-PNG)
  GET /api/point        Auswertung für einen Ort (Monatsaufschlüsselung)
  GET /api/ranking      Beste Orte (Städte) für die aktuellen Parameter
  GET /api/cities       Städteliste für die Suche
  GET /api/cities/climate  vollständige Städteliste mit Jahres-Klimakenngrößen
  GET /api/winterquarters/combination  beste Zwei-Orte-Kombination mit einem Traumwetterprofil
  GET /api/winterquarters/optimize     Legacy: Ein-Ort-Optimierung mit zwei Profilen
  GET /api/winterquarters/pair         Legacy: Gegenstück zu einem festen Quartier

Die Karten- und Ortsendpunkte teilen sich :func:`parse_filter`. Dessen
``view=wq``-Filter mit getrenntem Sommer-/Winterprofil bleibt für ältere
API-Clients erhalten. Die aktuelle Oberfläche nutzt für die neue
Wohnort-Suche ``/api/winterquarters/escape`` mit einem gemeinsamen
Wetterprofil und einem festen ``sfrom``/``sto``-Fenster.
"""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .ads import select_ad
from .build_progress import DEFAULT_REPO, build_status
from .climate import (CITY_DAY_INDICATORS, DEFAULT_SUMMER_MONTHS, FIELD_WIDTH, MONTH_NAMES,
                      WINTER_DEFAULTS, ClimateData, Filter, Params, cyclic_months,
                      months_label, norm_months)

BASE = Path(__file__).resolve().parent
# Eigener Datenordner für Entwicklung/Tests (KLIMA_DATA_DIR=/tmp/devgrid)
DATA_DIR = Path(os.environ.get("KLIMA_DATA_DIR", BASE / "data")).resolve()
STATIC_DIR = BASE / "static"

# Die Website zeigt ausschließlich echte, geprüfte Klimadaten. Für Entwicklung
# und Tests (synthetisches Raster) kann das Tor bewusst geöffnet werden:
#   KLIMA_ALLOW_UNVERIFIED=1 uvicorn app.main:app
ALLOW_UNVERIFIED = os.environ.get("KLIMA_ALLOW_UNVERIFIED", "").strip().lower() in (
    "1", "true", "yes", "on")

WORKFLOW_URL = f"https://github.com/{DEFAULT_REPO}/actions/workflows/build-climate-data.yml"

MONTHS = MONTH_NAMES

PRESETS = [
    {"id": "mild", "name": "Ausgewogen mild",
     "desc": "Tagsüber 18–28 °C, nachts nicht kälter als 8 °C, angenehm trocken, sonnig.",
     "params": {"tmax_min": 18, "tmax_max": 28, "tmin_min": 8, "tmin_max": 20,
                "tdew_max": 17, "sun_min": 3, "prec_max": 99}},
    {"id": "sommer", "name": "Warm & schweißfrei",
     "desc": "Richtig warm, aber nicht schwül: Taupunkt höchstens 15 °C.",
     "params": {"tmax_min": 24, "tmax_max": 33, "tmin_min": 14, "tmin_max": 24,
                "tdew_max": 15, "sun_min": 5, "prec_max": 99}},
    {"id": "sonnig", "name": "Viel Sonne",
     "desc": "Mindestens 6 Stunden Sonne am Tag und über 15 °C.",
     "params": {"tmax_min": 15, "tmax_max": 32, "tmin_min": 2, "tmin_max": 22,
                "tdew_max": 22, "sun_min": 6, "prec_max": 99}},
    {"id": "kuehl", "name": "Kühler Aktivurlaub",
     "desc": "Für Wanderer: 8–20 °C, trocken – gern auch in Skandinavien.",
     "params": {"tmax_min": 8, "tmax_max": 20, "tmin_min": -5, "tmin_max": 14,
                "tdew_max": 16, "sun_min": 2, "prec_max": 2}},
    {"id": "winter", "name": "Schneesicher",
     "desc": "Eiskalt und sonnig – Maximum höchstens 5 °C, Minimum unter −5 °C.",
     "params": {"tmax_min": -15, "tmax_max": 5, "tmin_min": -30, "tmin_max": -5,
                "tdew_max": 20, "sun_min": 1, "prec_max": 99}},
]

# Voreinstellungen für das *Winterquartier*-Wetter (im gleichnamigen Modus das
# zweite Profil neben dem Sommer-Wohlfühlwetter).
WINTER_PRESETS = [
    {"id": "wq-mild", "name": "Milder Winter",
     "desc": "Tagsüber 10–20 °C, nachts über 3 °C, ein paar Stunden Sonne – „Winter im Warmen“.",
     "params": {"tmax_min": 10, "tmax_max": 20, "tmin_min": 3, "tmin_max": 15,
                "tdew_max": 16, "sun_min": 3, "prec_max": 99}},
    {"id": "wq-warm", "name": "Warmer Winter",
     "desc": "Tagsüber 16–26 °C, nachts über 10 °C – Kanaren-/Nordafrika-Klasse.",
     "params": {"tmax_min": 16, "tmax_max": 26, "tmin_min": 10, "tmin_max": 20,
                "tdew_max": 17, "sun_min": 5, "prec_max": 99}},
    {"id": "wq-sonnig", "name": "Sonniger Winter",
     "desc": "Mindestens 5 h Sonne am Tag und über 12 °C.",
     "params": {"tmax_min": 12, "tmax_max": 24, "tmin_min": 4, "tmin_max": 18,
                "tdew_max": 18, "sun_min": 5, "prec_max": 99}},
    {"id": "wq-trocken", "name": "Trockener Winter",
     "desc": "12–22 °C, höchstens 1 mm Niederschlag am Tag – für Wanderer und Radler.",
     "params": {"tmax_min": 12, "tmax_max": 22, "tmin_min": 2, "tmin_max": 16,
                "tdew_max": 14, "sun_min": 4, "prec_max": 1}},
]

# Grenzen für historische/kompatible Endpunkte. Die neue Zwei-Orte-Suche hält
# das vom Nutzer gesetzte Sommerfenster unverändert.
WQ_MIN_MONTHS, WQ_MAX_MONTHS = 3, 9
WQ_MIN_CITY_DISTANCE_KM = 50.0

from .climate import COLOR_STOPS as _COLOR_STOPS


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Daten im Hintergrund vorwärmen, damit die erste Anfrage schnell ist.

    Auf Render (Free-Plan) schläft der Dienst ein; nach dem Aufwachen würde der
    erste Nutzer sonst Ladezeit + Datensatz-Load + erste Kartenberechnung
    zusammen bezahlen. ``/healthz`` bleibt dabei sofort beantwortbar.
    """
    def warm_up() -> None:
        global _warm, _load_error
        try:
            ready_dataset()                             # Datensatz laden + prüfen
            render_cached(Params(), None, 1000, False)  # IDW-Gewichte, Landmaske, PNG-Cache
            _warm = True
        except HTTPException as exc:
            # Keine belastbaren Daten: nichts vorwärmen, die Statusseite antwortet sofort.
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            _load_error = "; ".join(detail.get("reasons") or [str(exc.detail)])
        except Exception as exc:  # noqa: BLE001
            _load_error = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=warm_up, name="warmup", daemon=True).start()
    yield


app = FastAPI(title="Mein perfektes Klima – Europa", docs_url="/api/docs", lifespan=lifespan)

# Text-Antworten (JSON, GeoJSON, JS, CSS) komprimiert ausliefern. Das spart auf
# dem Free-Plan deutlich Bandbreite: die Ländergrenzen allein sind ~330 KB JSON,
# die Städtetabelle ~1,6 MB. Bilder und Schriftdateien (PNG/WOFF2) sind bereits
# komprimiert und bleiben unangetastet – Starlette überspringt sie anhand des
# Content-Type. Kompressionsstufe 6 statt 9: auf der geteilten Free-CPU zählt
# jede Millisekunde, der Unterschied in der Größe ist marginal.
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

_climate: ClimateData | None = None
_load_error: str | None = None
_warm: bool = False
_lock = threading.Lock()
_png_cache: "OrderedDict[tuple, tuple[bytes, dict]]" = OrderedDict()
_CACHE_MAX = 32


def climate() -> ClimateData:
    global _climate, _load_error
    if _climate is not None:
        return _climate
    try:
        _climate = ClimateData(DATA_DIR)
        return _climate
    except FileNotFoundError as exc:
        _load_error = (
            "Die Klimadaten (app/data/climate_grid.npz) fehlen noch. Der Datensatz wird vom "
            "GitHub-Action-Workflow „Klimadaten bauen“ erzeugt – bitte diesen Workflow starten."
        )
        raise HTTPException(status_code=503, detail=_load_error) from exc


def report_matches_dataset(cd: ClimateData) -> tuple[bool, str]:
    """Gehört der committete Qualitätsbericht zum gerade geladenen Raster?

    Ein Datenlauf kann einen Bericht zu einem *anderen* Datensatz hinterlassen –
    etwa ein abgebrochener Altlauf, der noch das synthetische Testraster geprüft
    hat, während längst echte ERA5-Daten deployed sind. Dessen Urteil gilt dann
    nicht: Es würde die Website für Daten sperren, die nie geprüft werden
    mussten. Umgekehrt darf ein fehlender/alter Bericht echte Daten nicht
    automatisch „grün“ schreiben – deshalb gibt die Funktion auch den Grund zurück.
    """
    report = cd.quality or {}
    meta = getattr(cd, "meta", {}) or {}
    if not report:
        return False, "kein Qualitätsbericht vorhanden"
    if report.get("model") and meta.get("model") and report["model"] != meta["model"]:
        return False, (f"Bericht prüfte Modell '{report['model']}', "
                       f"geladen ist '{meta['model']}'")
    fingerprint = report.get("dataset") or {}
    for key in ("generated_at", "n_points", "n_days", "start_date", "end_date", "grid_step_deg"):
        if key in fingerprint and key in meta and fingerprint[key] != meta[key]:
            return False, f"Bericht und Datensatz unterscheiden sich in „{key}“"
    return True, ""


def dataset_blockers(cd: ClimateData) -> list[str]:
    """Harte Gründe, warum dieser Datensatz nicht angezeigt werden darf.

    Leere Liste = Daten sind echt und haben das Qualitätstor bestanden.
    """
    blockers: list[str] = []
    if cd.is_synthetic:
        blockers.append("Das Raster stammt aus synthetischen Testdaten "
                        "(scripts/make_dev_grid.py), nicht aus ERA5")
    if not cd.season_ok:
        blockers.append(f"Jahresgang unplausibel: Juli − Januar im Süden "
                        f"= {round(cd.season_delta, 1)} K")
    if cd.coverage < 0.9:
        blockers.append(f"Nur {cd.coverage * 100:.1f} % der Punkt-Tage sind vorhanden")
    report = cd.quality or {}
    gehoert, _ = report_matches_dataset(cd)
    if report.get("ok") is False and gehoert:
        errors = [e for e in (report.get("errors") or [])]
        blockers.extend(errors or ["Qualitätsprüfung nicht bestanden"])
    seen: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.append(b)
    return seen


def not_ready_payload(state: str, reasons: list[str], cd: ClimateData | None = None) -> dict:
    """Antwortrumpf für „noch keine belastbaren Daten" – inkl. Baufortschritt."""
    payload: dict = {
        "data_ready": False,
        "state": state,
        "reasons": reasons,
        "allow_unverified": ALLOW_UNVERIFIED,
        "build": build_status(DATA_DIR),
        "workflow_url": WORKFLOW_URL,
    }
    if cd is not None:
        payload["dataset"] = {
            "source": cd.meta.get("source"), "model": cd.meta.get("model"),
            "generated_at": cd.meta.get("generated_at"), "n_points": cd.meta.get("n_points"),
            "start_date": cd.meta.get("start_date"), "end_date": cd.meta.get("end_date"),
        }
        payload["quality"] = cd.quality or {}
    return payload


def missing_data_reason(exc: Exception) -> str:
    return ("Die Klimadaten (app/data/climate_grid.npz) fehlen noch – der Datensatz wird vom "
            "GitHub-Action-Workflow „Klimadaten bauen“ erzeugt.")


def ready_dataset() -> ClimateData:
    """Datensatz, aber nur wenn er echt und geprüft ist – sonst HTTP 503.

    Jeder Karten-/Orts-Endpunkt benutzt diesen Zugriff. Damit ist
    sichergestellt, dass die Website niemals synthetische oder vom Qualitätstor
    verworfene Zahlen als Klima ausweist (früher: Banner über einer falschen Karte).
    """
    try:
        cd = climate()
    except HTTPException as exc:
        raise HTTPException(503, detail=not_ready_payload("missing", [missing_data_reason(exc)])) from exc
    blockers = dataset_blockers(cd)
    if blockers and not ALLOW_UNVERIFIED:
        raise HTTPException(503, detail=not_ready_payload("unusable", blockers, cd))
    return cd


def parse_params(
    tmax_min: float = Query(18, ge=-40, le=45),
    tmax_max: float = Query(30, ge=-40, le=45),
    tmin_min: float = Query(5, ge=-45, le=40),
    tmin_max: float = Query(20, ge=-45, le=40),
    tdew_max: float = Query(18, ge=-20, le=30),
    sun_min: float = Query(3, ge=0, le=16),
    prec_max: float = Query(99, ge=0, le=99),
) -> Params:
    if tmax_min > tmax_max:
        tmax_min, tmax_max = tmax_max, tmax_min
    if tmin_min > tmin_max:
        tmin_min, tmin_max = tmin_max, tmin_min
    return Params(tmax_min, tmax_max, tmin_min, tmin_max, tdew_max, sun_min, prec_max)


def parse_winter_params(
    wtmax_min: float = Query(WINTER_DEFAULTS.tmax_min, ge=-40, le=45),
    wtmax_max: float = Query(WINTER_DEFAULTS.tmax_max, ge=-40, le=45),
    wtmin_min: float = Query(WINTER_DEFAULTS.tmin_min, ge=-45, le=40),
    wtmin_max: float = Query(WINTER_DEFAULTS.tmin_max, ge=-45, le=40),
    wtdew_max: float = Query(WINTER_DEFAULTS.tdew_max, ge=-20, le=30),
    wsun_min: float = Query(WINTER_DEFAULTS.sun_min, ge=0, le=16),
    wprec_max: float = Query(WINTER_DEFAULTS.prec_max, ge=0, le=99),
) -> Params:
    """Wunschwetter für das Winterquartier (Gegenstück zum Sommer-Profil)."""
    if wtmax_min > wtmax_max:
        wtmax_min, wtmax_max = wtmax_max, wtmax_min
    if wtmin_min > wtmin_max:
        wtmin_min, wtmin_max = wtmin_max, wtmin_min
    return Params(wtmax_min, wtmax_max, wtmin_min, wtmin_max, wtdew_max, wsun_min, wprec_max)


MONTH_HELP = "month muss 1..12, 'all', ein Bereich (z. B. 5-9) oder eine Liste (1,2,12) sein"


def _one_month(text: str) -> int:
    try:
        m = int(text)
    except ValueError as exc:
        raise HTTPException(400, MONTH_HELP) from exc
    if not 1 <= m <= 12:
        raise HTTPException(400, MONTH_HELP)
    return m - 1


def _month_spec(part: str) -> list[int]:
    """„7“ → [6], „5-9“ → [4..8], „10-3“ → [9,10,11,0,1,2]."""
    if "-" not in part:
        return [_one_month(part)]
    start_text, _, end_text = part.partition("-")
    start, end = _one_month(start_text), _one_month(end_text)
    out: list[int] = []
    m = start
    while len(out) < 12:
        out.append(m)
        if m == end:
            break
        m = (m + 1) % 12
    return out


def parse_month(month: str | None) -> tuple[int, ...] | None:
    """Monatsfilter → 0-basierte Monatsliste (``None`` = ganzes Jahr).

    Zusätzlich zur einzelnen Monatsnummer sind Bereiche (``5-9``, auch über den
    Jahreswechsel: ``10-3``) und Listen (``1,2,12``) erlaubt – damit lässt sich
    auch ein Zeitraum wie „Wintermonate“ auf die Karte legen.
    """
    if month is None or not str(month).strip():
        return None
    text = str(month).strip().lower().replace(" ", "")
    if text in ("all", "jahr", "year"):
        return None
    months: list[int] = []
    for part in text.split(","):
        if part:
            months.extend(_month_spec(part))
    if not months:
        return None
    return norm_months(months)


def summer_window(sfrom: int, sto: int) -> tuple[int, ...]:
    """Sommerfenster aus „von Monat“/„bis Monat“ (1-basiert, Jahreswechsel erlaubt)."""
    start, end = int(sfrom) - 1, int(sto) - 1
    if not (0 <= start < 12 and 0 <= end < 12):
        raise HTTPException(400, "sfrom/sto müssen 1..12 sein")
    if end == (start - 1) % 12:
        # „von Juni bis Mai“ wäre das ganze Jahr – dann gäbe es kein Winterquartier.
        raise HTTPException(400, "Sommer- und Winterquartier brauchen je mindestens einen "
                                 "Monat – bitte ein Sommerfenster von 1 bis 11 Monaten wählen")
    months: list[int] = []
    m = start
    while True:
        months.append(m)
        if m == end:
            return tuple(months)
        m = (m + 1) % 12


def parse_period_id(period: str) -> str:
    """Only enable projection IDs from a validated regional model manifest."""
    value = (period or "all").strip().lower()
    options = {item["id"]: item for item in climate().period_options()}
    if value not in options or not options[value]["available"]:
        raise HTTPException(400, "Klimazeitraum unbekannt oder nicht verfügbar")
    return value


def parse_filter(
    view: str = Query("plain", description="plain | wq (Winterquartier-Modus)"),
    wview: str = Query("combo", description="combo | summer | winter"),
    sfrom: int = Query(5, ge=1, le=12, description="Sommerfenster ab Monat"),
    sto: int = Query(9, ge=1, le=12, description="Sommerfenster bis Monat"),
    month: str | None = Query(None),
    period: str = Query("all", description="all | recent5 | 2030 | 2050 (optional -ssp126 or -ssp585)"),
    p: Params = Depends(parse_params),
    winter: Params = Depends(parse_winter_params),
) -> Filter:
    """Gemeinsamer Filter für Karte, Raster, Ranking und Ortsauswertung.

    Im Normalmodus steuert ``month`` den Zeitraum. ``view=wq`` ist ein
    rückwärtskompatibler Altmodus mit getrennten Wetterprofilen; die aktuelle
    Zwei-Orte-Suche hat bewusst einen eigenen Endpoint und verwendet nur ein Profil.
    """
    kind = (view or "plain").strip().lower()
    if kind in ("wq", "winterquartier", "quartier"):
        if wview not in ("combo", "summer", "winter"):
            raise HTTPException(400, "wview muss 'combo', 'summer' oder 'winter' sein")
        return Filter(kind=wview, p=p or Params(), winter=winter,
                      summer_months=summer_window(sfrom, sto), period=parse_period_id(period))
    if kind not in ("plain", "normal"):
        raise HTTPException(400, "view muss 'plain' oder 'wq' sein")
    return Filter(kind="plain", p=p or Params(), winter=winter, months=parse_month(month),
                  period=parse_period_id(period))


def render_cached(f: Filter, width: int, fixed: bool) -> tuple[bytes, dict]:
    """Kartenbild + Statistik für einen Filter (mit kleinem PNG-Zwischenspeicher).

    Der Filter ist unveränderlich und hashbar, deshalb genügt er zusammen mit
    Breite und Skalenmodus als Schlüssel – auch für die Winterquartier-Kombination,
    die aus zwei Profilen zusammengesetzt ist.
    """
    key = (f, width, fixed)
    with _lock:
        hit = _png_cache.get(key)
        if hit is not None:
            _png_cache.move_to_end(key)
            return hit
    cd = ready_dataset()
    png, info = cd.render_counts(f.counts(cd), width, fixed,
                                 limit=f.days_limit(cd),
                                 meta={"months": list(f.months) if f.months else None,
                                       "view_kind": f.kind,
                                       "window_label": f.label(),
                                       "unit_label": f.unit_label(),
                                       "period": f.period,
                                       "period_info": cd.period_info(f.period)})
    if f.is_winterquarters:
        info["summer_months"] = list(f.summer_months)
        info["winter_months"] = list(f.winter_months)
    with _lock:
        _png_cache[key] = (png, info)
        while len(_png_cache) > _CACHE_MAX:
            _png_cache.popitem(last=False)
    return png, info


@app.get("/healthz")
def healthz():
    """Immer HTTP 200 – Render prüft den Dienst, nicht den Datenstand.

    Der Inhalt sagt trotzdem ehrlich, ob schon belastbare Klimadaten geladen
    sind (``data_ready``) und woran es sonst hängt (``error``).
    """
    loaded = _climate is not None
    data_ready = False
    if loaded:
        try:
            data_ready = not dataset_blockers(_climate) or ALLOW_UNVERIFIED
        except Exception:  # noqa: BLE001
            data_ready = False
    return {"ok": True, "loaded": loaded, "warm": _warm, "data_ready": data_ready,
            "allow_unverified": ALLOW_UNVERIFIED,
            "error": _load_error if not data_ready else None}


@app.get("/api/meta")
def api_meta():
    """Datensatz-Infos – antwortet mit 503, solange keine echten Daten vorliegen."""
    try:
        cd = climate()
    except HTTPException:
        return JSONResponse(not_ready_payload("missing", [missing_data_reason(None)]),
                            status_code=503)
    blockers = dataset_blockers(cd)
    if blockers and not ALLOW_UNVERIFIED:
        return JSONResponse(not_ready_payload("unusable", blockers, cd), status_code=503)
    quality = cd.quality or {}
    gehoert, grund = report_matches_dataset(cd)
    veraltet = bool(quality) and not gehoert
    stale_warnings = ([f"Qualitätsbericht passt nicht zum geladenen Datensatz ({grund}) – "
                       f"bitte den Workflow „Klimadaten bauen“ erneut ausführen"]
                      if veraltet else [])
    season_errors = [] if cd.season_ok else [
        f"Jahresgang invertiert oder flach (Juli − Januar im Süden: "
        f"{round(cd.season_delta, 1)} K) – Datensatz unbrauchbar"]
    return {
        "data_ready": True,
        "state": "ready",
        "months": MONTHS,
        "presets": PRESETS,
        "winter_presets": WINTER_PRESETS,
        "defaults": Params().query(),
        "winter_defaults": WINTER_DEFAULTS.query(),
        "periods": cd.period_options(),
        "default_period": "all",

        # Alles, was die Oberfläche für den Winterquartier-Modus braucht.
        "winterquarters": {
            # 1-basiert wie der month-Parameter, damit die Oberfläche nichts umrechnen muss
            "summer_months": [m + 1 for m in DEFAULT_SUMMER_MONTHS],
            "min_months": WQ_MIN_MONTHS,
            "max_months": WQ_MAX_MONTHS,
            "views": [
                {"id": "combo", "name": "Kombination",
                 "desc": "Sommer- und Winterquartier-Tage zusammen – ein Ort fürs ganze Jahr"},
                {"id": "summer", "name": "Sommerquartier",
                 "desc": "Nur die Wohlfühl-Tage im Sommerfenster"},
                {"id": "winter", "name": "Winterquartier",
                 "desc": "Nur die Wohlfühl-Tage in den Wintermonaten"},
            ],
            "roles": [
                {"id": "summer", "name": "Sommerquartier", "wants": "Winterquartier"},
                {"id": "winter", "name": "Winterquartier", "wants": "Sommerquartier"},
            ],
        },
        "dataset": {
            "source": cd.meta.get("source"),
            "attribution": cd.meta.get("attribution"),
            "start_date": cd.meta.get("start_date"),
            "end_date": cd.meta.get("end_date"),
            "n_days": cd.meta.get("n_days"),
            "n_years": cd.meta.get("n_years"),
            "n_points": cd.meta.get("n_points"),
            "grid_step_deg": cd.meta.get("grid_step_deg"),
            "generated_at": cd.meta.get("generated_at"),
            "bbox": cd.bbox,
            "model": cd.meta.get("model"),
        },
        # Ehrliche Kennzeichnung der Datenbasis – die Oberfläche zeigt daraus
        # einen Hinweis, wenn keine echten ERA5-Werte vorliegen.
        "synthetic": cd.is_synthetic,
        "coverage": round(cd.coverage, 4),
        "repaired": cd.repaired,
        "quality": {
            "ok": ((bool(quality.get("ok", not cd.is_synthetic)) and cd.season_ok)
                   if gehoert else (not cd.is_synthetic and cd.season_ok)),
            "checked_at": quality.get("checked_at") if gehoert else None,
            "checks": quality.get("checks", []) if gehoert else [],
            "warnings": list(quality.get("warnings", [])) + stale_warnings if gehoert
                        else stale_warnings,
            "errors": (list(quality.get("errors", [])) if gehoert else []) + season_errors,
            "stale": veraltet,
            "season_delta_k": round(cd.season_delta, 1),
        },
        "years_in_record": round(cd.years, 2),
        "color_stops": [[round(float(s), 3), int(r), int(g), int(b)] for s, r, g, b in _COLOR_STOPS],
        "points": {"lat": [round(float(v), 3) for v in cd.lat],
                   "lon": [round(float(v), 3) for v in cd.lon]},
    }


@app.get("/api/map")
def api_map(f: Filter = Depends(parse_filter), width: int = 1100, fixed: bool = False):
    _, info = render_cached(f, int(width), bool(fixed))
    ticks = ready_dataset().legend_ticks(info["vmax"])
    return {**{k: v for k, v in info.items() if k != "values"},
            "values": info["values"], "legend_ticks": ticks,
            "month": f.single_month(), "params": f.p.query(),
            "winter_params": f.winter.query() if f.is_winterquarters else None,
            "view_kind": f.kind,
            "month_label": f.label(), "unit_label": f.unit_label()}


@app.get("/api/map.png")
def api_map_png(f: Filter = Depends(parse_filter), width: int = 1100, fixed: bool = False):
    png, info = render_cached(f, int(width), bool(fixed))
    return Response(content=png, media_type="image/png",
                    headers={"X-Legend-Max": str(info["vmax"]),
                             "Cache-Control": "public, max-age=600"})


@app.get("/api/point")
def api_point(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
              f: Filter = Depends(parse_filter)):
    """Auswertung für einen Ort: Wohlfühl-Tage, Monatsprofil und Klima.

    Im Winterquartier-Modus kommt der Block ``wq`` dazu (Sommer-, Winter- und
    Kombinations-Tage dieses Ortes).
    """
    cd = ready_dataset()
    month = f.single_month()
    rep = cd.point_report(lat, lon, f.p, month if month is not None else f.months, f.period)
    rep["view_kind"] = f.kind
    if f.is_winterquarters:
        rep["wq"] = cd.winterquarters_point(lat, lon, f)
    return rep


@app.get("/api/ranking")
def api_ranking(f: Filter = Depends(parse_filter), limit: int = 12):
    cd = ready_dataset()
    return {
        "month": f.single_month(),
        "month_label": f.label(),
        "unit_label": f.unit_label(),
        "view_kind": f.kind,
        "period": f.period,
        "cities": cd.ranking_counts(f.counts(cd), max(1, min(40, limit))),
    }


@app.get("/api/cities/map")
def api_cities_map(f: Filter = Depends(parse_filter), limit: int = 400):
    """Größte Orte mit ihrer Tageszahl – für anklickbare Kartenmarker."""
    cd = ready_dataset()
    return {"month": f.single_month(), "view_kind": f.kind, "period": f.period,
            "cities": cd.cities_for_map_counts(f.counts(cd), max(20, min(1200, limit)))}


@app.get("/api/winterquarters/combination")
def api_winterquarters_combination(
    p: Params = Depends(parse_params),
    sfrom: int = Query(5, ge=1, le=12),
    sto: int = Query(9, ge=1, le=12),
    limit: int = Query(8, ge=1, le=40),
    period: str = Query("all", description="all | recent5 | 2030 | 2050 (optional -ssp126 or -ssp585)"),
):
    """Findet die beste Sommer-/Winter-Kombination aus zwei Städten.

    In beiden Zeitfenstern gilt dasselbe Traumwetterprofil. Das fest gewählte
    Sommerfenster wird nicht während der Optimierung verschoben; der Winterort
    deckt alle restlichen Monate ab.
    """
    months = summer_window(sfrom, sto)
    period_id = parse_period_id(period)
    return ready_dataset().two_city_combination(
        p, months, limit=max(1, min(40, limit)),
        min_distance_km=WQ_MIN_CITY_DISTANCE_KM,
        period=period_id,
    )


@app.get("/api/winterquarters/escape")
def api_winterquarters_escape(
    lat: float = Query(..., ge=34, le=72),
    lon: float = Query(..., ge=-25, le=45),
    season: str = Query("winter", pattern="^(winter|summer)$"),
    p: Params = Depends(parse_params),
    sfrom: int = Query(5, ge=1, le=12),
    sto: int = Query(9, ge=1, le=12),
    limit: int = Query(8, ge=1, le=40),
    period: str = Query("all"),
):
    """Saisonales Ausweichquartier zu einem unveränderlichen Wohnort."""
    return ready_dataset().escape_report(
        lat, lon, season, p, summer_window(sfrom, sto), limit=limit,
        min_distance_km=WQ_MIN_CITY_DISTANCE_KM, period=parse_period_id(period))


@app.get("/api/winterquarters/optimize")
def api_winterquarters_optimize(f: Filter = Depends(parse_filter),
                                min_months: int = Query(WQ_MIN_MONTHS, ge=1, le=11),
                                max_months: int = Query(WQ_MAX_MONTHS, ge=1, le=11),
                                limit: int = Query(8, ge=1, le=40)):
    """Optimale Kombination aus Sommer- und Winterquartier-Monaten.

    Getestet werden alle zusammenhängenden Aufteilungen des Jahres (Sommerfenster
    mit ``min_months``–``max_months`` Monaten, Rest = Winterquartier) gegen beide
    Wetterprofile. Antwort: beste Aufteilungen samt bestem Ort und bester Stadt.
    """
    lo, hi = sorted((int(min_months), int(max_months)))
    return ready_dataset().season_split_report(f.p, f.winter, lo, hi, max(1, min(40, limit)),
                                               period=f.period)


@app.get("/api/winterquarters/pair")
def api_winterquarters_pair(lat: float = Query(..., ge=-90, le=90),
                            lon: float = Query(..., ge=-180, le=180),
                            role: str = Query("summer", description="summer | winter"),
                            max_km: float | None = Query(None, ge=1, le=20000),
                            limit: int = Query(8, ge=1, le=40),
                            f: Filter = Depends(parse_filter)):
    """Gegenstück-Suche: welches andere Quartier passt am besten?

    ``role`` beschreibt den gewählten Ort: ``summer`` = hier ist mein Sommerquartier,
    gesucht wird das Winterquartier (und umgekehrt). Jeder Vorschlag nennt die
    Wohlfühl-Tage im gesuchten Zeitraum, die Summe beider Quartiere und die
    Entfernung – die optimale Monatsaufteilung wird je Paar mitberechnet.
    """
    if role.lower() not in ("summer", "winter", "s", "w", "sommer", "winterquartier"):
        raise HTTPException(400, "role muss 'summer' oder 'winter' sein")
    return ready_dataset().pair_report(lat, lon, role, f.p, f.winter, f.summer_months,
                                       limit=max(1, min(40, limit)), max_km=max_km,
                                       min_len=WQ_MIN_MONTHS, max_len=WQ_MAX_MONTHS,
                                       period=f.period)


@app.get("/api/build")
def api_build(refresh: bool = False):
    """Fortschritt des Datenbaus – für den Balken auf der Statusseite.

    Antwortet immer mit 200: Der Status ist auch dann interessant, wenn gerade
    nichts gebaut wird oder GitHub nicht erreichbar ist.
    """
    payload = build_status(DATA_DIR, force_refresh=bool(refresh))
    payload["workflow_url"] = WORKFLOW_URL
    payload["repo"] = DEFAULT_REPO
    try:
        cd = climate()
    except HTTPException:
        payload["data_ready"] = False
        payload["reasons"] = [missing_data_reason(None)]
        return payload
    blockers = dataset_blockers(cd)
    payload["data_ready"] = not blockers or ALLOW_UNVERIFIED
    payload["reasons"] = blockers
    payload["dataset"] = {
        "model": cd.meta.get("model"), "source": cd.meta.get("source"),
        "generated_at": cd.meta.get("generated_at"), "n_points": cd.meta.get("n_points"),
        "start_date": cd.meta.get("start_date"), "end_date": cd.meta.get("end_date"),
    }
    return payload


@app.get("/api/ads")
def api_ads(context: str = Query("year", pattern="^(year|month|wq)$"),
            placement: str = Query("sidebar", pattern="^sidebar$")):
    """Operator-configured contextual sponsors; no user identity or tracking."""
    return JSONResponse({"ad": select_ad(context, placement)}, headers={"Cache-Control": "no-store"})


@app.get("/api/countries")
def api_countries():
    path = DATA_DIR / "countries.geojson"
    if not path.exists():
        raise HTTPException(503, "Ländergrenzen fehlen (scripts/build_geo.py ausführen)")
    return Response(content=path.read_text(), media_type="application/geo+json",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/cities")
def api_cities():
    path = DATA_DIR / "cities.json"
    if not path.exists():
        raise HTTPException(503, "Städteliste fehlt")
    return Response(content=path.read_text(), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/cities/climate")
def api_cities_climate(period: str = Query("all", description="all | recent5 | 2030 | 2050 (optional -ssp126 or -ssp585)")):
    """Jahres-Klimakenngrößen für alle Städte (unabhängig vom Kartenfilter).

    Die Stadtkoordinaten werden dem jeweils nächsten ERA5-Rasterpunkt
    zugeordnet. Tageszahlen sind Mittelwerte pro Jahr über den gewählten
    Zeitraum; Grenzwerte stehen explizit in ``indicators``.
    """
    period_id = parse_period_id(period)
    cd = ready_dataset()
    selected_period = cd.period_info(period_id)
    return JSONResponse(
        content={
            "period": selected_period,
            "grid_step_deg": cd.meta.get("grid_step_deg"),
            "city_count": len(cd.cities),
            "city_method": "nearest_grid_point_regional_delta" if cd.is_projection(period_id) else "nearest_grid_point",
            "indicators": {
                key: {"label": label, "threshold": threshold, "unit": "Tage/Jahr"}
                for key, label, threshold in CITY_DAY_INDICATORS
            },
            "cities": cd.city_climate_rows(period_id),
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/cities/search")
def api_cities_search(q: str = Query("", min_length=1), limit: int = 8):
    """Ortssuche (Prefix + Teilstring) über die Städteliste – schlankes JSON."""
    cd = ready_dataset()
    needle = q.strip().lower()
    if not needle or not cd.cities:
        return {"q": q, "cities": []}
    hits = []
    for c in cd.cities:
        hay = c.get("search") or c["name"].lower()
        if needle in hay or needle in c["name"].lower():
            hits.append(c)
            if len(hits) >= 200:
                break
    hits.sort(key=lambda c: (0 if (c.get("search") or "").startswith(needle) else 1, -int(c.get("pop", 0))))
    return {"q": q, "cities": [{
        "name": c["name"], "country": c.get("name_de", ""), "lat": c["lat"], "lon": c["lon"],
        "pop": c.get("pop", 0),
    } for c in hits[: max(1, min(20, limit))]]}


@app.get("/")
def index():
    path = STATIC_DIR / "index.html"
    if not path.exists():
        raise HTTPException(500, "index.html fehlt")
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
