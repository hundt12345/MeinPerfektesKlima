"""FastAPI-Anwendung: „Mein perfektes Klima – Europa“.

  GET /                 Startseite mit Karte
  GET /api/meta         Datensatz-Infos, Standardwerte, Voreinstellungen
  GET /api/map          Statistik + Rasterwerte für die aktuellen Parameter
  GET /api/map.png      Kartenbild (Web-Mercator-PNG)
  GET /api/point        Auswertung für einen Ort (Monatsaufschlüsselung)
  GET /api/ranking      Beste Orte (Städte) für die aktuellen Parameter
  GET /api/cities       Städteliste für die Suche
"""
from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from collections import OrderedDict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .climate import ClimateData, Params

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
STATIC_DIR = BASE / "static"

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
async def lifespan(_: FastAPI):
    """Raster im Hintergrund laden – der erste Kartenaufruf soll nicht warten müssen."""
    def load() -> None:
        try:
            climate()
            print("[startup] Klimaraster geladen", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[startup] Raster noch nicht verfügbar: {exc}", flush=True)

    threading.Thread(target=load, name="warm-up", daemon=True).start()
    yield


app = FastAPI(title="Mein perfektes Klima – Europa", docs_url="/api/docs", lifespan=lifespan)

_climate: ClimateData | None = None
_load_error: str | None = None
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
    png, info = climate().render(p, month, width, fixed)
    with _lock:
        _png_cache[key] = (png, info)
        while len(_png_cache) > _CACHE_MAX:
            _png_cache.popitem(last=False)
    return png, info


@app.get("/healthz")
def healthz():
    return {"ok": _climate is not None or _load_error is None, "loaded": _climate is not None}


@app.get("/api/meta")
def api_meta():
    try:
        cd = climate()
    except HTTPException as exc:
        return JSONResponse({"error": exc.detail, "data_ready": False}, status_code=503)
    return {
        "data_ready": True,
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
    ticks = climate().legend_ticks(info["vmax"])
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
def api_point(lat: float, lon: float, p: Params = Depends(parse_params), month: str | None = Query(None)):
    p = p or Params()
    m = parse_month(month)
    cd = climate()
    rep = cd.point_report(lat, lon, p)
    if m is not None:
        rep["selected_month"] = m
        rep["days_selected_month"] = rep["days_per_month"][m]
    return rep


@app.get("/api/ranking")
def api_ranking(p: Params = Depends(parse_params), month: str | None = Query(None), limit: int = 12):
    p = p or Params()
    m = parse_month(month)
    return {
        "month": m,
        "month_label": "ganzes Jahr" if m is None else MONTHS[m],
        "cities": climate().ranking(p, m, max(1, min(40, limit))),
    }


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


@app.get("/")
def index():
    path = STATIC_DIR / "index.html"
    if not path.exists():
        raise HTTPException(500, "index.html fehlt")
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
