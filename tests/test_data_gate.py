"""Datentor der Website: ohne echte, geprüfte ERA5-Daten keine Karte.

Früher zeigte die Startseite synthetische Testdaten mit einem Warnbanner – die
Zahlen waren trotzdem falsch. Seit dem Umbau verweigert die App die Auskunft
(HTTP 503) und die Oberfläche zeigt stattdessen den Baufortschritt. Hier wird
beides geprüft, inklusive des Entwicklungsschalters und des Fortschrittskanals.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA_DIR = Path(os.environ["KLIMA_DATA_DIR"])
pytestmark = pytest.mark.skipif(not (DATA_DIR / "climate_grid.npz").exists(),
                                reason="Kein Testraster vorhanden")


def body_of(response):
    """Antwortrumpf unabhängig davon, ob er in ``detail`` verpackt ist."""
    data = response.json()
    return data.get("detail", data)


def client_with(monkeypatch, **env):
    """App-Instanz mit bestimmter Umgebungskonfiguration (neu geladen)."""
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import app.main as main
    main = importlib.reload(main)
    return TestClient(main.app), main


def test_synthetische_daten_werden_abgelehnt(monkeypatch):
    client, _ = client_with(monkeypatch, KLIMA_ALLOW_UNVERIFIED="", KLIMA_PROGRESS_URL="")
    r = client.get("/api/meta")
    assert r.status_code == 503
    body = body_of(r)
    assert body["data_ready"] is False
    assert body["state"] == "unusable"
    assert any("synthetisch" in grund.lower() for grund in body["reasons"]), body["reasons"]
    assert "build" in body and "workflow_url" in body

    for pfad in ("/api/map", "/api/map.png", "/api/ranking", "/api/cities/map"):
        assert client.get(pfad).status_code == 503, pfad
    assert client.get("/api/point", params={"lat": 41.4, "lon": 2.17}).status_code == 503


def test_healthcheck_bleibt_gruen(monkeypatch):
    """Render prüft /healthz – der Dienst darf nicht als kaputt gelten, nur weil
    der Datensatz noch gebaut wird."""
    client, _ = client_with(monkeypatch, KLIMA_ALLOW_UNVERIFIED="", KLIMA_PROGRESS_URL="")
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True          # Render darf den Dienst nicht für kaputt halten
    assert body["data_ready"] is False


def test_entwicklungsschalter_zeigt_karte_mit_warnung(monkeypatch):
    client, _ = client_with(monkeypatch, KLIMA_ALLOW_UNVERIFIED="1", KLIMA_PROGRESS_URL="")
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["data_ready"] is True
    assert body["synthetic"] is True
    assert client.get("/api/map", params={"width": 300}).status_code == 200


def test_fehlende_daten_melden_baustatus(monkeypatch, tmp_path):
    leer = tmp_path / "leer"
    leer.mkdir()
    (leer / "cities.json").write_text("[]")
    client, _ = client_with(monkeypatch, KLIMA_DATA_DIR=str(leer),
                            KLIMA_ALLOW_UNVERIFIED="", KLIMA_PROGRESS_URL="")
    r = client.get("/api/meta")
    assert r.status_code == 503
    body = body_of(r)
    assert body["state"] == "missing"
    assert body["data_ready"] is False
    assert "Klimadaten bauen" in body["reasons"][0] or "climate_grid.npz" in body["reasons"][0]


def test_baufortschritt_wird_durchgereicht(monkeypatch, tmp_path):
    fortschritt = {
        "schema": 1, "status": "running", "phase": "Abruf", "percent": 42.5,
        "message": "Jahr 2020 gelesen (5 von 10 Jahren)",
        "blocks_done": 5, "blocks_total": 10, "rate": "11 min/Jahr",
        "eta_minutes": 55, "source": "ERA5 (Google ARCO Zarr)",
        "updated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_url": "https://github.com/hundt12345/MeinPerfektesKlima/actions/runs/1",
        "log_tail": ["[10:00:00] Jahr 2020 fertig"],
    }
    datei = tmp_path / "build_progress.json"
    datei.write_text(json.dumps(fortschritt))
    client, _ = client_with(monkeypatch, KLIMA_ALLOW_UNVERIFIED="",
                            KLIMA_PROGRESS_URL=str(datei))
    r = client.get("/api/build")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "building"
    assert body["percent"] == 42.5
    assert body["blocks_done"] == 5 and body["blocks_total"] == 10
    assert body["eta_minutes"] == 55
    assert body["log_tail"] == fortschritt["log_tail"]
    assert body["data_ready"] is False
    assert body["age_seconds"] < 120
    assert body["workflow_url"].endswith("build-climate-data.yml")


def test_alter_lauf_gilt_als_unterbrochen(monkeypatch, tmp_path):
    datei = tmp_path / "build_progress.json"
    datei.write_text(json.dumps({"status": "running", "percent": 10.0,
                                 "updated_at": "2020-01-01T00:00:00Z"}))
    client, _ = client_with(monkeypatch, KLIMA_ALLOW_UNVERIFIED="",
                            KLIMA_PROGRESS_URL=str(datei))
    body = client.get("/api/build").json()
    assert body["state"] == "stalled"
    assert body["stale"] is True


def test_startseite_liefert_statusseite(monkeypatch):
    client, _ = client_with(monkeypatch, KLIMA_ALLOW_UNVERIFIED="", KLIMA_PROGRESS_URL="")
    html = client.get("/").text
    assert 'id="building"' in html
    assert 'id="build-bar"' in html


def test_alter_pruefbericht_sperrt_echte_daten_nicht():
    """Ein liegengebliebener Prüfbericht darf echte Daten nicht sperren.

    Konkret: Abgebrochene Altläufe committen noch eine `quality_report.json` zum
    *synthetischen* Testraster. Lägen daneben echte ERA5-Daten, müsste die
    Website erreichbar bleiben – mit sichtbarem Hinweis statt stillschweigend.
    """
    from types import SimpleNamespace
    importlib.reload(importlib.import_module("app.main"))
    from app.main import dataset_blockers, report_matches_dataset

    def datensatz(report, meta, **kw):
        return SimpleNamespace(quality=report, meta=meta, is_synthetic=False,
                               season_ok=True, coverage=1.0, **kw)

    echt = datensatz({"ok": False, "model": "synthetic", "errors": ["alles kaputt"]},
                     {"model": "era5", "generated_at": "2026-10-12T10:00:00Z"})
    gehoert, grund = report_matches_dataset(echt)
    assert gehoert is False and "synthetic" in grund
    assert dataset_blockers(echt) == [], "Bericht eines anderen Datensatzes zählt nicht"

    alt = datensatz({"ok": False, "model": "era5", "errors": ["Sonne unplausibel"],
                     "dataset": {"generated_at": "2020-01-01T00:00:00Z", "n_points": 2250}},
                    {"model": "era5", "generated_at": "2026-10-12T10:00:00Z", "n_points": 2250})
    assert report_matches_dataset(alt)[0] is False, "Fingerabdruck entscheidet mit"

    defekt = datensatz({"ok": False, "model": "era5", "errors": ["Sonnenschein unplausibel"],
                        "dataset": {"generated_at": "2026-10-12T10:00:00Z", "n_points": 2250}},
                       {"model": "era5", "generated_at": "2026-10-12T10:00:00Z", "n_points": 2250})
    assert report_matches_dataset(defekt) == (True, "")
    assert dataset_blockers(defekt) == ["Sonnenschein unplausibel"], "passendes Tor greift"

    gesund = datensatz({"ok": True, "model": "era5",
                        "dataset": {"generated_at": "2026-10-12T10:00:00Z"}},
                       {"model": "era5", "generated_at": "2026-10-12T10:00:00Z"})
    assert dataset_blockers(gesund) == []
