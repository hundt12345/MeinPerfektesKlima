"""Offline-Test der Abruf-Pipeline (ohne Netz): Kontrollpunkte, Zusammensetzen,
Quantisierung, Metadaten, Aufräumen – mit einem gefälschten ``fetch_batch``.

Aufruf:  pytest tests/test_fetch_offline.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_climate_grid", ROOT / "scripts" / "fetch_climate_grid.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)  # type: ignore[union-attr]


def _fake_batch(lat, lon, start, end, model, pace):
    """Liefert deterministische Kunstwerte für die angefragten Punkte."""
    days = np.arange(np.datetime64(start), np.datetime64(end) + 1)
    n = lat.size
    base = lat.astype(np.float32)[:, None]
    out = {
        "tmax": 10 + base + np.zeros((n, days.size), dtype=np.float32),
        "tmin": base - 5 + np.zeros((n, days.size), dtype=np.float32),
        "tdew": base - 8 + np.zeros((n, days.size), dtype=np.float32),
        "sun": np.full((n, days.size), 20000.0, dtype=np.float32),
        "prec": np.zeros((n, days.size), dtype=np.float32),
    }
    out["_time"] = days
    return out


def _counting(calls: dict, fail_on: int | None = None):
    def fake(lat, lon, start, end, model, pace):
        calls["n"] += 1
        if fail_on is not None and calls["n"] == fail_on:
            raise RuntimeError("Unterbrechung simuliert")
        return _fake_batch(lat, lon, start, end, model, pace)
    return fake


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    conf = dict(fetch.DEFAULT_CFG)
    conf.update({"step": 4.0, "start_date": "2023-01-01", "end_date": "2023-01-20",
                 "batch_points": 6, "chunk_years": 10, "mask_step": 0.25,
                 "threads": 1, "max_minutes": 2})
    monkeypatch.setattr(fetch, "CHECKPOINT_DIR", tmp_path / "ckpt")
    monkeypatch.setattr(fetch, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(fetch, "fetch_batch", _fake_batch)
    return conf


def test_pipeline_complete(cfg, tmp_path):
    fetched = fetch.run(cfg, limit=14)
    assert fetched > 0

    out = tmp_path / "out" / "climate_grid.npz"
    assert out.exists()
    with np.load(out) as z:
        assert z["tmax"].shape == (14, 20)
        assert z["lat"].size == 14
        # Quantisierung: tmax = 10 + lat  ->  int8 = round((10+lat)*2)
        assert z["tmax"][0, 0] == int(round((10 + float(z["lat"][0])) * 2))
        assert int(z["sun"][0, 0]) == int(round(20000.0 * fetch.SCALES["sun"]))
        assert int(z["prec"][0, 0]) == 0
        assert z["land_mask_shape"].size == 2

    meta = json.loads((tmp_path / "out" / "climate_meta.json").read_text())
    assert meta["n_days"] == 20
    assert meta["n_points"] == 14
    assert meta["start_date"] == "2023-01-01"
    # vollständig -> Kontrollpunkte aufgeräumt
    assert not list((tmp_path / "ckpt").rglob("*.npz"))


def test_resume_fetches_only_missing_blocks(cfg, tmp_path, monkeypatch):
    """Nach einer Unterbrechung wird nur der fehlende Block nachgeholt."""
    calls = {"n": 0}
    monkeypatch.setattr(fetch, "fetch_batch", _counting(calls, fail_on=2))
    fetch.run(cfg, limit=14)
    assert calls["n"] >= 3, "Testaufbau: der Lauf sollte unterbrochen worden sein"

    ck = tmp_path / "ckpt"
    leftovers = sorted(ck.rglob("*.npz"))
    assert leftovers, "Kontrollpunkte fehlen nach der Unterbrechung"

    calls2 = {"n": 0}
    monkeypatch.setattr(fetch, "fetch_batch", _counting(calls2))
    fetch.run(cfg, limit=14)
    assert calls2["n"] == 1, f"es wurde mehr als der fehlende Block geholt ({calls2['n']})"
    assert not list(ck.rglob("*.npz")), "Kontrollpunkte wurden nicht aufgeräumt"


def test_partial_run_writes_filled_raster(cfg, tmp_path, monkeypatch):
    """Ein abgebrochener Lauf hinterlässt trotzdem ein nutzbares (gefülltes) Raster."""
    counters = {"n": 0}
    monkeypatch.setattr(fetch, "fetch_batch", _counting(counters, fail_on=1))
    fetch.run(cfg, limit=14)
    out = tmp_path / "out" / "climate_grid.npz"
    assert out.exists()
    with np.load(out) as z:
        assert z["tmax"].shape[0] == 14
        assert int(z["tmax"].min()) > -128, "Lücken wurden nicht gefüllt"


# ------------------------------------------------- Drosselung: Anfrage teilen
def _api_response(url: str, value: float = 1.0):
    """Baut aus der Anfrage-URL eine Antwort im Open-Meteo-Format."""
    import urllib.parse

    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    lats = qs["latitude"][0].split(",")
    days = np.arange(np.datetime64(qs["start_date"][0]), np.datetime64(qs["end_date"][0]) + 1)
    return [
        {"daily": {"time": [str(d) for d in days],
                   **{api: [value] * days.size for api in fetch.VARIABLES.values()}}}
        for _ in lats
    ]


def _throttle_above(limit: float, seen: list, value: float = 1.0):
    """``seen`` sammelt Paare (Punkt-Tage, durchgekommen) je Anfrage."""
    def fake_http(url, pace, cost, **kwargs):
        if cost > limit:
            seen.append((cost, False))
            raise fetch.Throttled("simulierte Drosselung")
        seen.append((cost, True))
        return _api_response(url, value)
    return fake_http


def test_gedrosselte_anfrage_teilt_punkte(monkeypatch):
    """Eine zu große Anfrage wird halbiert, statt die Rate in den Keller zu fahren."""
    seen: list = []
    monkeypatch.setattr(fetch, "http_json", _throttle_above(100, seen))
    lat = np.array([50.0, 51.0, 52.0, 53.0])
    lon = np.array([7.0, 8.0, 9.0, 10.0])
    res = fetch.fetch_batch(lat, lon, "2023-01-01", "2023-02-10", "era5", fetch.Pace(10_000))
    assert res["tmax"].shape == (4, 41)
    assert res["_time"].size == 41
    ok = [c for c, good in seen if good]
    assert ok and max(ok) <= 100, "die Anfrage wurde nicht verkleinert"
    assert any(not good for _, good in seen), "es sollte mindestens eine Drosselung geben"


def test_gedrosselte_anfrage_teilt_zeitraum(monkeypatch):
    """Ein langer Zeitraum wird entlang der Tage geteilt und lückenlos zusammengesetzt."""
    seen: list = []
    monkeypatch.setattr(fetch, "http_json", _throttle_above(100, seen, value=2.0))
    lat, lon = np.array([48.0]), np.array([11.0])
    start, end = "2020-01-01", "2022-03-01"
    res = fetch.fetch_batch(lat, lon, start, end, "era5", fetch.Pace(10_000))
    n_days = int((np.datetime64(end) - np.datetime64(start)).astype(int)) + 1
    assert res["tmax"].shape == (1, n_days)
    gaps = np.diff(res["_time"].astype("datetime64[D]").astype(int))
    assert (gaps == 1).all(), "Zeitachse hat Lücken oder Duplikate"
    assert float(np.unique(res["tmax"])[0]) == 2.0
    ok = [c for c, good in seen if good]
    assert ok and max(ok) <= 100, "die Anfrage wurde nicht verkleinert"


def test_rate_bleibt_handlungsfaehig_und_erholt_sich():
    """Viele Drosselungen dürfen den Lauf nicht zum Stillstand bringen."""
    import time

    pace = fetch.Pace(60_000, max_wait_per_request=75.0)
    for _ in range(30):
        pace.penalize("HTTP 429", wait=0.0)
    cost = 18_000.0
    pace.next_at = time.monotonic()     # kein echtes Warten im Test
    pace.acquire(cost)
    assert pace.per_min >= cost / 75.0 * 60.0, "eine Anfrage würde länger als 75 s warten"
    for _ in range(100):
        pace.succeed()
    assert pace.per_min == 60_000, "Rate erholt sich nicht auf den Zielwert"
