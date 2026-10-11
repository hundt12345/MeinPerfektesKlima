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
