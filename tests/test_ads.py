from datetime import date
import json

from fastapi.testclient import TestClient
import pytest

from app.ads import select_ad
from app.main import app


def campaign(**overrides):
    return {"id": "winter-partner", "advertiser": "Testpartner", "title": "Saisonquartiere",
            "body": "Ausdrücklich nur ein Test.", "url": "https://example.com/quartiere",
            "contexts": ["wq"], "placements": ["sidebar"], "start": "2026-01-01", "end": "2026-12-31",
            **overrides}


def configure(tmp_path, monkeypatch, campaigns, enabled=True):
    path = tmp_path / "ads.json"
    path.write_text(json.dumps({"schema_version": 1, "enabled": enabled, "campaigns": campaigns}))
    monkeypatch.setenv("KLIMA_AD_CAMPAIGNS_FILE", str(path))
    return path


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("KLIMA_AD_CAMPAIGNS_FILE", raising=False)
    assert select_ad("wq", "sidebar") is None


def test_context_dates_and_safe_public_response(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, [campaign()])
    ad = select_ad("wq", "sidebar", date(2026, 10, 14))
    assert ad["id"] == "winter-partner"
    assert "weight" not in ad and "start" not in ad
    assert select_ad("year", "sidebar", date(2026, 10, 14)) is None
    assert select_ad("wq", "sidebar", date(2027, 1, 1)) is None
    assert select_ad("wq", "sidebar", date(2025, 12, 31)) is None
    assert select_ad("wq", "sidebar", date(2026, 1, 1))
    assert select_ad("wq", "sidebar", date(2026, 12, 31))
    configure(tmp_path, monkeypatch, [campaign(enabled=False)])
    assert select_ad("wq", "sidebar") is None
    configure(tmp_path, monkeypatch, [campaign()], enabled=False)
    assert select_ad("wq", "sidebar") is None


@pytest.mark.parametrize("changes", [
    {"url": "javascript:alert(1)"}, {"url": "http://example.com"},
    {"url": "https://user:password@example.com"}, {"url": "https://example.com/\n"},
    {"end": "2025-01-01"}, {"weight": 0}, {"contexts": ["home_lat"]},
    {"placements": ["popup"]}, {"script": "https://example.com/tracker.js"},
])
def test_bad_campaign_fails_closed(tmp_path, monkeypatch, changes):
    configure(tmp_path, monkeypatch, [campaign(**changes)])
    assert select_ad("wq", "sidebar") is None


def test_duplicates_and_corrupt_file(tmp_path, monkeypatch):
    path = configure(tmp_path, monkeypatch, [campaign(), campaign()])
    assert select_ad("wq", "sidebar") is None
    path.write_text("bad json")
    assert select_ad("wq", "sidebar") is None
    path.unlink()
    assert select_ad("wq", "sidebar") is None


def test_dynamic_config_update_and_api_without_cookies(tmp_path, monkeypatch):
    path = configure(tmp_path, monkeypatch, [campaign(start="1970-01-01", end="2100-12-31")])
    client = TestClient(app)
    response = client.get("/api/ads?context=wq&placement=sidebar")
    assert response.status_code == 200
    assert response.json()["ad"]["title"] == "Saisonquartiere"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert client.get("/api/ads?context=month").json()["ad"] is None
    assert client.get("/api/ads?context=invalid").status_code == 422
    path.write_text(json.dumps({"enabled": False, "campaigns": []}))
    assert client.get("/api/ads?context=wq").json()["ad"] is None
