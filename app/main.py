"""FastAPI-Anwendung: „Mein perfektes Klima – Europa“.

  GET /                 Startseite mit Karte (bzw. Statusseite, solange keine
                        echten ERA5-Daten vorliegen)
  GET /api/meta         Datensatz-Infos, Standardwerte, Voreinstellungen
  GET /api/build        Live-Fortschritt des Datenbaus (GitHub-Action)
  GET /api/map          Statistik + Rasterwerte für die aktuellen Parameter
  GET /api/map.png      Kartenbild (Web-Mercator-PNG)
  GET /api/point        Auswertung für einen Ort (Monatsaufschlüsselung)
  GET /share            Ergebnis-Link mit Social-Media-Metadaten
  GET /share/card.png   generierte 1200×630-Teilen-Grafik
  POST /api/analytics/pageview  aggregierter First-Party-Tageszähler
  GET /api/analytics/summary   geschützte Seitenaufruf-Zusammenfassung
  GET /analytics        Betreiber-Dashboard für Seitenaufrufe
  GET /klima            Verzeichnis aller Ortsprofile (SEO-Einstieg)
  GET /klima/{ort}      serverseitiges Ortsprofil mit echten Zahlen, FAQ, Teilen
  GET /ueberwintern-in-europa, /sommerhitze-entfliehen  Ratgeber aus den Daten
  GET /sitemap.xml, /robots.txt, /feed.xml, /badge/{ort}.svg
  GET /impressum, /datenschutz  aus den Betreiberangaben (sonst 503)
  GET /api/affiliate    serverseitig konfigurierter Partnerlink (Standard: aus)
  POST /api/analytics/event   erlaubte Funnel-Ereignisse (aggregiert)
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

import html
import hmac
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import seo
from .ads import ads_enabled, select_ad
from .affiliate import affiliate_enabled, select_affiliate
from .analytics import (event_summary, is_enabled as analytics_enabled, pageview_summary,
                        record_event, record_pageview)
from .build_progress import DEFAULT_REPO, build_status
from .legal import legal_configured, load_legal, notice_html
from .sharing import decode_share_state, render_share_card, share_headline
from .climate import (CITY_DAY_INDICATORS, DEFAULT_SUMMER_MONTHS, FIELD_WIDTH, MONTH_NAMES,
                      WINTER_DEFAULTS, ClimateData, Filter, Params, cyclic_months,
                      months_label, norm_months)
from .names import matched_alias, search as city_search

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
# Vorgeschlagene Ausweichziele liegen mindestens so weit auseinander: verschiedene
# Regionen statt mehrerer Nachbarorte mit demselben Rasterpunkt.
WQ_SPREAD_KM = 120.0

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
            # Stadtwerte werden höhenkorrigiert, die Kartenfläche nicht.
            "city_elevation_adjusted": cd.city_elevation_adjusted,
            "elevation_source": cd.elevation_meta.get("source"),
            "elevation_method": cd.elevation_meta.get("method"),
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
        "cities": cd.ranking_city_counts(f.city_counts(cd), max(1, min(40, limit))),
    }


@app.get("/api/cities/map")
def api_cities_map(f: Filter = Depends(parse_filter), limit: int = 400):
    """Größte Orte mit ihrer Tageszahl – für anklickbare Kartenmarker."""
    cd = ready_dataset()
    return {"month": f.single_month(), "view_kind": f.kind, "period": f.period,
            "cities": cd.cities_for_map_city_counts(f.city_counts(cd), max(20, min(1200, limit)))}


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
    spread_km: float = Query(WQ_SPREAD_KM, ge=0, le=1000,
                             description="Mindestabstand zwischen vorgeschlagenen Zielen"),
):
    """Saisonales Ausweichquartier zu einem unveränderlichen Wohnort."""
    return ready_dataset().escape_report(
        lat, lon, season, p, summer_window(sfrom, sto), limit=limit,
        min_distance_km=WQ_MIN_CITY_DISTANCE_KM, period=parse_period_id(period),
        spread_km=spread_km)


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
    zugeordnet; seine Temperaturen werden auf die Höhe der Stadt korrigiert
    (siehe ``city_method`` und die ``elevation``-Angabe je Stadt). Tageszahlen
    sind Mittelwerte pro Jahr über den gewählten Zeitraum; Grenzwerte stehen
    explizit in ``indicators``.
    """
    period_id = parse_period_id(period)
    cd = ready_dataset()
    selected_period = cd.period_info(period_id)
    return JSONResponse(
        content={
            "period": selected_period,
            "grid_step_deg": cd.meta.get("grid_step_deg"),
            "city_count": len(cd.cities),
            "city_method": cd.city_method(period_id),
            "indicators": {
                key: {"label": label, "threshold": threshold, "unit": "Tage/Jahr"}
                for key, label, threshold in CITY_DAY_INDICATORS
            },
            "cities": cd.city_climate_rows(period_id),
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/cities/search")
def api_cities_search(q: str = Query("", min_length=1), limit: int = 8,
                      area: str = Query("all", pattern="^(all|data)$",
                                        description="all | data (nur Orte im Klimaraster)")):
    """Tolerante Ortssuche: Akzente, ae/oe/ue, deutsche und lokale Namen.

    ``area=data`` liefert nur Orte innerhalb des Klimarasters – dort, wo auch
    Wohnort und Ausweichquartier liegen dürfen. Die Grenze gilt *vor* dem Limit,
    damit Treffer außerhalb keine gültigen Orte verdrängen.
    """
    cd = ready_dataset()
    if not q.strip() or not cd.cities:
        return {"q": q, "cities": []}
    bbox = cd.bbox
    accept = None
    if area == "data":
        accept = (lambda c: bbox["lat_min"] <= c["lat"] <= bbox["lat_max"]
                  and bbox["lon_min"] <= c["lon"] <= bbox["lon_max"])
    hits = city_search(cd.cities, cd.city_search_keys, q, max(1, min(20, limit)), accept)
    return {"q": q, "cities": [{
        "name": c["name"], "country": c.get("name_de", ""), "lat": c["lat"], "lon": c["lon"],
        "pop": c.get("pop", 0), "alias": matched_alias(c, q), "exact": rank == 0,
    } for c, rank in hits]}


def share_result(token: str) -> tuple[dict, dict, str, str, str]:
    """Validate a public share payload and calculate its live, reproducible result."""
    try:
        state = decode_share_state(token)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cd = ready_dataset()
    period = parse_period_id(state["period"])
    params = Params(**state["params"])
    month = state["month"] - 1 if state["mode"] == "month" else None

    def calculate(place: dict) -> tuple[dict, dict]:
        # A user can click just outside the data rectangle on the visual map, so
        # point_index selects the nearest valid grid cell, just as /api/point does.
        report = cd.point_report(place["lat"], place["lon"], params, month, period)
        city = report.get("city") or {}
        name = place["name"] or (city.get("name") if city.get("dist_km", 999) < 40 else "") or "Europa"
        country = place["country"] or (city.get("name_de") if city.get("dist_km", 999) < 40 else "") or ""
        resolved = {**place, "name": name, "country": country}
        return report, resolved

    if state.get("compare"):
        results = [calculate(place) for place in state["compare"]]
        reports = [item[0] for item in results]
        state["compare"] = [item[1] for item in results]
        state["period"] = period
        location = f"{state['compare'][0]['name']} vs. {state['compare'][1]['name']}"
        return state, {"comparison": reports}, "", "", location

    report, selected = calculate(state["selected"])
    state["selected"] = selected
    name, country = selected["name"], selected["country"]
    location = f"{name}, {country}" if country and country.lower() not in name.lower() else name
    state["period"] = period
    return state, report, name, country, location


def public_origin(request: Request) -> str:
    """Use a configured canonical URL, or the external request scheme/host."""
    configured = os.environ.get("KLIMA_PUBLIC_URL", "").strip()
    if configured:
        parts = urlsplit(configured)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username or parts.password or parts.path not in {"", "/"}
                or parts.query or parts.fragment):
            raise HTTPException(500, "KLIMA_PUBLIC_URL muss eine URL ohne Pfad oder Query sein")
        return urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
    parts = urlsplit(str(request.base_url))
    forwarded_scheme = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = forwarded_scheme if forwarded_scheme in {"http", "https"} else parts.scheme
    return urlunsplit((scheme, parts.netloc, "", "", "")).rstrip("/")


@app.get("/share", response_class=HTMLResponse, include_in_schema=False)
def share_page(request: Request, state: str = Query(..., min_length=1, max_length=4096)):
    """App page with crawler-readable Open Graph metadata for one result."""
    shared, report, _name, _country, location = share_result(state)
    title, description, _days = share_headline(report, shared, location)
    encoded = quote(state, safe="")
    origin = public_origin(request)
    canonical = f"{origin}/share?state={encoded}"
    image_url = f"{origin}/share/card.png?state={encoded}"
    tags = "\n".join((
        f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Mein perfektes Klima">',
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">',
        f'<meta property="og:description" content="{html.escape(description, quote=True)}">',
        f'<meta property="og:url" content="{html.escape(canonical, quote=True)}">',
        f'<meta property="og:image" content="{html.escape(image_url, quote=True)}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:type" content="image/png">',
        f'<meta property="og:image:alt" content="{html.escape(title, quote=True)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{html.escape(title, quote=True)}">',
        f'<meta name="twitter:description" content="{html.escape(description, quote=True)}">',
        f'<meta name="twitter:image" content="{html.escape(image_url, quote=True)}">',
    ))
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(500, "index.html fehlt")
    document = index_path.read_text(encoding="utf-8").replace("%%ORIGIN%%", origin)
    description_tag = f'<meta name="description" content="{html.escape(description, quote=True)}">'
    document, replaced = re.subn(r'<meta\s+name="description"\s+content="[^"]*"\s*/?>',
                                 description_tag, document, count=1)
    if not replaced:
        tags = f"{description_tag}\n{tags}"
    document = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>",
                      document, count=1, flags=re.IGNORECASE | re.DOTALL)
    document = document.replace("</head>", f"{tags}\n</head>", 1)
    return HTMLResponse(document, headers={"Cache-Control": "public, max-age=300"})


@app.get("/share/card.png", include_in_schema=False)
def share_card(state: str = Query(..., min_length=1, max_length=4096)):
    """A 1200×630 first-party PNG preview for social platforms and downloads."""
    shared, report, _name, _country, location = share_result(state)
    png = render_share_card(report, shared, location)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/analytics/pageview", status_code=204, include_in_schema=False)
def api_analytics_pageview(path: str = Query("/", max_length=64)):
    """Record only a coarse page label; this endpoint stores no request metadata."""
    if analytics_enabled():
        try:
            record_pageview(path)
        except Exception:  # Analytics must never prevent the app from working.
            logging.getLogger(__name__).exception("Could not record aggregate page view")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@app.get("/api/analytics/summary", include_in_schema=False)
def api_analytics_summary(request: Request, days: int = Query(30, ge=1, le=365)):
    """Private aggregate counters, visible only with an operator-set bearer token."""
    secret = os.environ.get("KLIMA_ANALYTICS_TOKEN", "")
    if not analytics_enabled() or not secret:
        raise HTTPException(404, "Analytics ist nicht aktiviert oder nicht konfiguriert")
    auth = request.headers.get("authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(401, "Ungültiger Schlüssel", headers={"WWW-Authenticate": "Bearer"})
    try:
        summary = pageview_summary(days)
        funnel = event_summary(days)
    except Exception as exc:  # Do not expose local paths or database internals.
        logging.getLogger(__name__).exception("Could not read aggregate page views")
        raise HTTPException(503, "Analytics-Daten sind gerade nicht verfügbar") from exc
    for row, event_row in zip(summary["daily"], funnel["daily"]):
        row["events"] = event_row["events"]
    summary["events"] = funnel["events"]
    summary["events_total"] = funnel["total"]
    return JSONResponse(summary, headers={"Cache-Control": "no-store"})


@app.get("/analytics", include_in_schema=False)
def analytics_dashboard():
    path = STATIC_DIR / "analytics.html"
    if not path.exists():
        raise HTTPException(404, "Analytics-Dashboard fehlt")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ Inhaltsseiten
# Serverseitig gerenderte Seiten aus denselben geprüften Daten wie die Karte.
# Sie sind der Einstieg für Suchmaschinen und Social Media; ohne Datensatz
# antworten sie (wie die Karte) mit 503 statt mit erfundenen Zahlen.
CACHE_PUBLIC = {"Cache-Control": "public, max-age=600"}


def landing_view() -> "seo.DatasetView":
    return seo.view_for(ready_dataset())


@app.get("/robots.txt", include_in_schema=False)
def robots_txt(request: Request):
    return Response(seo.robots_txt(public_origin(request)), media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request):
    body = seo.sitemap_xml(landing_view(), public_origin(request))
    return Response(body, media_type="application/xml", headers=CACHE_PUBLIC)


@app.get("/feed.xml", include_in_schema=False)
def feed_xml(request: Request):
    body = seo.feed_xml(landing_view(), public_origin(request))
    return Response(body, media_type="application/rss+xml; charset=utf-8", headers=CACHE_PUBLIC)


@app.get("/klima", response_class=HTMLResponse, include_in_schema=False)
def klima_index(request: Request):
    return HTMLResponse(seo.index_page(landing_view(), public_origin(request)),
                        headers=CACHE_PUBLIC)


@app.get("/klima/{slug}", response_class=HTMLResponse, include_in_schema=False)
def klima_place(request: Request, slug: str):
    view = landing_view()
    page = seo.city_page(view, public_origin(request), slug)
    if page is None:
        raise HTTPException(404, "Für diesen Ort gibt es kein Profil")
    return HTMLResponse(page[0], headers=CACHE_PUBLIC)


@app.get("/klima/{slug}/card.png", include_in_schema=False)
def klima_card(request: Request, slug: str):
    """1200×630-Vorschau je Ortsprofil – für Social Media und Newsletter."""
    view = landing_view()
    entry, context = seo.city_context_by_slug(view, slug)
    if entry is None:
        raise HTTPException(404, "Für diesen Ort gibt es kein Profil")
    state = seo.social_card_state(entry)
    location = f"{entry.name}, {entry.country}" if entry.country else entry.name
    png = render_share_card(context["report"], state, location)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/og/home.png", include_in_schema=False)
def og_home():
    """1200×630-Vorschau der Startseite für Social Media und Newsletter."""
    png = seo.render_home_card(landing_view())
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/badge/{slug}.svg", include_in_schema=False)
def badge_svg(slug: str):
    """Einbettbare Kennzahl-Grafik – verlinkt in Blogs zurück auf das Ortsprofil."""
    view = landing_view()
    entry = view.by_slug.get(slug)
    if entry is None:
        raise HTTPException(404, "Für diesen Ort gibt es kein Profil")
    svg = seo.badge_svg(entry, view.days(entry, "mild"))
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


def _guide_response(request: Request, slug: str) -> HTMLResponse:
    page = seo.guide_page(landing_view(), public_origin(request), slug)
    if page is None:
        raise HTTPException(404, "Diesen Ratgeber gibt es nicht")
    return HTMLResponse(page[0], headers=CACHE_PUBLIC)


@app.get("/ueberwintern-in-europa", response_class=HTMLResponse, include_in_schema=False)
def guide_winter(request: Request):
    return _guide_response(request, "ueberwintern-in-europa")


@app.get("/sommerhitze-entfliehen", response_class=HTMLResponse, include_in_schema=False)
def guide_summer(request: Request):
    return _guide_response(request, "sommerhitze-entfliehen")


@app.get("/guide/{slug}/card.png", include_in_schema=False)
def guide_card(slug: str):
    guide = seo.GUIDES.get(slug)
    if guide is None:
        raise HTTPException(404, "Diesen Ratgeber gibt es nicht")
    png = seo.render_guide_card(landing_view(), guide)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/site", include_in_schema=False)
def api_site():
    """Kleine Statusauskunft: welche optionalen Bausteine sind eingerichtet?"""
    try:
        pages = len(seo.view_for(ready_dataset()).entries)
    except HTTPException:
        pages = 0
    return JSONResponse({
        "legal_configured": legal_configured(),
        "affiliate_enabled": affiliate_enabled(),
        "ads_enabled": ads_enabled(),
        "analytics_enabled": analytics_enabled(),
        "landing_pages": pages,
    }, headers={"Cache-Control": "no-store"})


@app.get("/api/affiliate", include_in_schema=False)
def api_affiliate(context: str = Query("year", pattern="^(year|month|wq)$"),
                  lat: float = Query(..., ge=-90, le=90),
                  lon: float = Query(..., ge=-180, le=180),
                  name: str = Query("", max_length=100),
                  country: str = Query("", max_length=100),
                  slug: str = Query("", max_length=80)):
    """Höchstens ein serverseitig konfigurierter Partnerlink – sonst ``null``."""
    link = select_affiliate(context, city=name, country=country, lat=lat, lon=lon, slug=slug)
    return JSONResponse({"link": link}, headers={"Cache-Control": "no-store"})


@app.get("/impressum", response_class=HTMLResponse, include_in_schema=False)
def impressum(request: Request):
    legal = load_legal()
    if legal is None:
        return HTMLResponse(seo.layout(
            title="Impressum – noch nicht eingerichtet", description="Betreiberangaben fehlen",
            canonical=public_origin(request) + "/impressum", origin=public_origin(request),
            body=notice_html()), status_code=503)
    return HTMLResponse(seo.render_impressum(legal, public_origin(request)),
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/datenschutz", response_class=HTMLResponse, include_in_schema=False)
def datenschutz(request: Request):
    legal = load_legal()
    if legal is None:
        return HTMLResponse(seo.layout(
            title="Datenschutz – noch nicht eingerichtet", description="Betreiberangaben fehlen",
            canonical=public_origin(request) + "/datenschutz", origin=public_origin(request),
            body=notice_html()), status_code=503)
    return HTMLResponse(seo.render_datenschutz(
        legal, public_origin(request), analytics_on=analytics_enabled(),
        ads_on=ads_enabled(), affiliate_on=affiliate_enabled()),
        headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/analytics/event", status_code=204, include_in_schema=False)
def api_analytics_event(name: str = Query(..., max_length=32)):
    """Erlaubte Funnel-Ereignisse – unbekannte Namen werden still verworfen."""
    if analytics_enabled():
        try:
            record_event(name)
        except Exception:  # Messung darf die App niemals stören.
            logging.getLogger(__name__).exception("Could not record aggregate event")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def page_html(request: Request) -> str:
    """index.html mit absoluten URLs (canonical, Open Graph) für die echte Domain."""
    path = STATIC_DIR / "index.html"
    if not path.exists():
        raise HTTPException(500, "index.html fehlt")
    return path.read_text(encoding="utf-8").replace("%%ORIGIN%%", public_origin(request))


@app.get("/")
def index(request: Request):
    return HTMLResponse(page_html(request))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
