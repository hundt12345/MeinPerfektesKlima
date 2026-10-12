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

from .build_progress import DEFAULT_REPO, build_status
from .climate import FIELD_WIDTH, ClimateData, Params

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

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni",
          "Juli", "August", "September", "Oktober", "November", "Dezember"]

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
    if report.get("ok") is False:
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


def parse_month(month: str | None) -> int | None:
    if month in (None, "", "all", "jahr", "year"):
        return None
    try:
        m = int(month)
    except ValueError as exc:
        raise HTTPException(400, "month muss 1..12 oder 'all' sein") from exc
    if not 1 <= m <= 12:
        raise HTTPException(400, "month muss 1..12 oder 'all' sein")
    return m - 1


def render_cached(p: Params, month: int | None, width: int, fixed: bool) -> tuple[bytes, dict]:
    key = (p, month, width, fixed)
    with _lock:
        hit = _png_cache.get(key)
        if hit is not None:
            _png_cache.move_to_end(key)
            return hit
    png, info = ready_dataset().render(p, month, width, fixed)
    with _lock:
        _png_cache[key] = (png, info)
        while len(_png_cache) > _CACHE_MAX:
            _png_cache.popitem(last=False)
    return png, info


@app.get("/healthz")
def healthz():
    return {"ok": _climate is not None or _load_error is None,
            "loaded": _climate is not None, "warm": _warm,
            "error": _load_error if _climate is None else None}


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
    season_errors = [] if cd.season_ok else [
        f"Jahresgang invertiert oder flach (Juli − Januar im Süden: "
        f"{round(cd.season_delta, 1)} K) – Datensatz unbrauchbar"]
    return {
        "data_ready": True,
        "state": "ready",
        "months": MONTHS,
        "presets": PRESETS,
        "defaults": Params().query(),
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
            "ok": bool(quality.get("ok", not cd.is_synthetic)) and cd.season_ok,
            "checked_at": quality.get("checked_at"),
            "checks": quality.get("checks", []),
            "warnings": quality.get("warnings", []),
            "errors": list(quality.get("errors", [])) + season_errors,
            "season_delta_k": round(cd.season_delta, 1),
        },
        "years_in_record": round(cd.years, 2),
        "color_stops": [[round(float(s), 3), int(r), int(g), int(b)] for s, r, g, b in _COLOR_STOPS],
        "points": {"lat": [round(float(v), 3) for v in cd.lat],
                   "lon": [round(float(v), 3) for v in cd.lon]},
    }


@app.get("/api/map")
def api_map(p: Params = Depends(parse_params), month: str | None = Query(None),
            width: int = 1100, fixed: bool = False):
    p = p or Params()
    m = parse_month(month)
    _, info = render_cached(p, m, int(width), bool(fixed))
    ticks = ready_dataset().legend_ticks(info["vmax"])
    return {**{k: v for k, v in info.items() if k != "values"},
            "values": info["values"], "legend_ticks": ticks,
            "month": m, "params": p.query(),
            "month_label": "ganzes Jahr" if m is None else MONTHS[m]}


@app.get("/api/map.png")
def api_map_png(p: Params = Depends(parse_params), month: str | None = Query(None),
                width: int = 1100, fixed: bool = False):
    p = p or Params()
    m = parse_month(month)
    png, info = render_cached(p, m, int(width), bool(fixed))
    return Response(content=png, media_type="image/png",
                    headers={"X-Legend-Max": str(info["vmax"]),
                             "Cache-Control": "public, max-age=600"})


@app.get("/api/point")
def api_point(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
              p: Params = Depends(parse_params), month: str | None = Query(None)):
    """Auswertung für einen Ort: Wohlfühl-Tage, Monatsprofil und Klima."""
    p = p or Params()
    m = parse_month(month)
    return ready_dataset().point_report(lat, lon, p, m)


@app.get("/api/ranking")
def api_ranking(p: Params = Depends(parse_params), month: str | None = Query(None), limit: int = 12):
    p = p or Params()
    m = parse_month(month)
    return {
        "month": m,
        "month_label": "ganzes Jahr" if m is None else MONTHS[m],
        "cities": ready_dataset().ranking(p, m, max(1, min(40, limit))),
    }


@app.get("/api/cities/map")
def api_cities_map(p: Params = Depends(parse_params), month: str | None = Query(None), limit: int = 400):
    """Größte Orte mit ihrer Tageszahl – für anklickbare Kartenmarker."""
    p = p or Params()
    m = parse_month(month)
    return {"month": m, "cities": ready_dataset().cities_for_map(p, m, max(20, min(1200, limit)))}


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
