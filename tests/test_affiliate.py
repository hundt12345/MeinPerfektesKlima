"""Tests des Partner-Link-Adapters: vertraglich konfiguriert oder gar nichts.

Der Adapter ist die einzige Stelle, an der die App Geld verdienen kann – und die
einzige Stelle, an der ein Link die eigene Seite verlässt. Deshalb gilt hier
fail-closed: jeder Zweifel an der Konfiguration führt dazu, dass **kein**
Call-to-Action erscheint.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.affiliate import affiliate_enabled, select_affiliate
from app.main import app

PARTNER = {
    "id": "test-unterkunft",
    "partner": "Testpartner",
    "label": "Unterkünfte in {city} ansehen",
    "note": "Testangebot.",
    "url_template": "https://booking.example.com/search?ss={city}&aid=4711",
    "hosts": ["booking.example.com"],
    "contexts": ["year", "wq"],
    "start": "2026-01-01",
    "end": "2026-12-31",
}


def configure(tmp_path: Path, monkeypatch, partners=None, enabled=True) -> Path:
    path = tmp_path / "affiliate.json"
    path.write_text(json.dumps({"schema_version": 1, "enabled": enabled,
                                "disclosure": "Provisionshinweis.",
                                "partners": partners if partners is not None else [PARTNER]}),
                    encoding="utf-8")
    monkeypatch.setenv("KLIMA_AFFILIATE_FILE", str(path))
    return path


def test_off_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KLIMA_AFFILIATE_FILE", str(tmp_path / "fehlt.json"))
    assert affiliate_enabled() is False
    assert select_affiliate("year", city="Málaga", country="Spanien", lat=36.7, lon=-4.4) is None
    with TestClient(app) as client:
        link = client.get("/api/affiliate", params={"lat": 36.7, "lon": -4.4, "name": "Málaga"})
        assert link.status_code == 200
        assert link.json() == {"link": None}


def test_live_partner_renders_encoded_url(tmp_path: Path, monkeypatch) -> None:
    configure(tmp_path, monkeypatch)
    link = select_affiliate("year", city="Málaga", country="Spanien", lat=36.7, lon=-4.4,
                            today=date(2026, 6, 1))
    assert link is not None
    assert link["partner"] == "Testpartner"
    assert link["label"] == "Unterkünfte in Málaga ansehen"
    assert "ss=M%C3%A1laga" in link["url"] and "aid=4711" in link["url"]
    assert link["disclosure"] == "Provisionshinweis."
    # Kontext- und Zeitfenster gelten.
    assert select_affiliate("month", city="Málaga", country="Spanien", lat=36.7, lon=-4.4,
                            today=date(2026, 6, 1)) is None
    assert select_affiliate("year", city="Málaga", country="Spanien", lat=36.7, lon=-4.4,
                            today=date(2027, 6, 1)) is None
    with TestClient(app) as client:
        data = client.get("/api/affiliate", params={"lat": 36.7, "lon": -4.4, "name": "Málaga",
                                                    "context": "year"}).json()
    assert data["link"]["url"].startswith("https://booking.example.com/")


def test_country_restriction(tmp_path: Path, monkeypatch) -> None:
    configure(tmp_path, monkeypatch, partners=[{**PARTNER, "countries": ["Portugal"]}])
    assert select_affiliate("year", city="Lissabon", country="Portugal", lat=38.7, lon=-9.1) is not None
    assert select_affiliate("year", city="Berlin", country="Deutschland", lat=52.5, lon=13.4) is None


def test_invalid_configuration_fails_closed(tmp_path: Path, monkeypatch) -> None:
    broken = [
        {**PARTNER, "url_template": "http://booking.example.com/search?ss={city}"},
        {**PARTNER, "url_template": "https://booking.example.com/search?ss={city}&next={redirect}"},
        {**PARTNER, "url_template": "https://evil.example.net/search?ss={city}"},
        {**PARTNER, "hosts": ["*.example.com"]},
        {**PARTNER, "url_template": "https://booking.example.com/search?aid=4711"},  # ohne Ort
        {**PARTNER, "end": "2025-01-01"},
        {**PARTNER, "label": "Angebot {secret}"},
        {**PARTNER, "script": "https://booking.example.com/tracker.js"},
    ]
    for partner in broken:
        configure(tmp_path, monkeypatch, partners=[partner])
        assert select_affiliate("year", city="Berlin", country="Deutschland",
                                lat=52.5, lon=13.4) is None, partner
        assert affiliate_enabled() is False, partner

    # Auch eine nicht lesbare Datei und eine abgeschaltete Konfiguration bleiben still.
    monkeypatch.setenv("KLIMA_AFFILIATE_FILE", str(tmp_path / "kaputt.json"))
    (tmp_path / "kaputt.json").write_text("{ kein json", encoding="utf-8")
    assert affiliate_enabled() is False
    configure(tmp_path, monkeypatch, enabled=False)
    assert select_affiliate("year", city="Berlin", country="Deutschland",
                            lat=52.5, lon=13.4) is None


def test_content_page_shows_partner_only_when_configured(tmp_path: Path, monkeypatch) -> None:
    from app.main import climate
    from app.seo import view_for

    slug = next(entry.slug for entry in view_for(climate()).entries if entry.name == "Berlin")
    with TestClient(app) as client:
        page = client.get(f"/klima/{slug}").text
        assert 'rel="sponsored' not in page

    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        page = client.get(f"/klima/{slug}").text
    assert 'rel="sponsored nofollow noopener noreferrer"' in page
    assert "booking.example.com" in page
    assert "Provisionshinweis." in page
    assert "Anzeige · Testpartner" in page
