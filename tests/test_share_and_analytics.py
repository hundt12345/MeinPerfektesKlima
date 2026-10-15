"""Regression tests for social-result links and first-party page-view counters."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from io import BytesIO

from app.main import app


def _share_token(**overrides) -> str:
    payload = {
        "v": 1,
        "mode": "year",
        "period": "all",
        "month": 7,
        "params": {
            "tmax_min": 18, "tmax_max": 30, "tmin_min": 5, "tmin_max": 20,
            "tdew_max": 18, "sun_min": 3, "prec_max": 99,
        },
        "selected": {"lat": 52.52, "lon": 13.405, "name": "Berlin", "country": "Deutschland"},
    }
    payload.update(overrides)
    return base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode().rstrip("=")


def test_share_page_has_dynamic_social_metadata_and_png_preview() -> None:
    token = _share_token()
    with TestClient(app) as client:
        page = client.get("/share", params={"state": token})
        assert page.status_code == 200
        assert 'property="og:title"' in page.text
        assert 'property="og:description"' in page.text
        assert 'property="og:image"' in page.text
        assert 'name="twitter:card" content="summary_large_image"' in page.text
        assert "Berlin, Deutschland" in page.text
        assert f"/share/card.png?state={token}" in page.text

        card = client.get("/share/card.png", params={"state": token})
        assert card.status_code == 200
        assert card.headers["content-type"] == "image/png"
        image = Image.open(BytesIO(card.content))
        assert image.size == (1200, 630)


def test_share_rejects_malformed_or_invalid_filters() -> None:
    with TestClient(app) as client:
        assert client.get("/share", params={"state": "not-a-token"}).status_code == 400
        bad = _share_token(params={"tmax_min": 90})
        assert client.get("/share/card.png", params={"state": bad}).status_code == 400


def test_pageviews_are_aggregated_and_private(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KLIMA_ANALYTICS_DB", str(tmp_path / "metrics.sqlite3"))
    monkeypatch.setenv("KLIMA_ANALYTICS_TOKEN", "test-only-secret")
    monkeypatch.setenv("KLIMA_ANALYTICS_ENABLED", "1")
    with TestClient(app) as client:
        dashboard = client.get("/analytics")
        assert dashboard.status_code == 200
        assert "KLIMA_ANALYTICS_TOKEN" in dashboard.text
        assert client.post("/api/analytics/pageview", params={"path": "/"}).status_code == 204
        assert client.post("/api/analytics/pageview", params={"path": "/share"}).status_code == 204
        # Only the two coarse labels are accepted; a query/path cannot be stored.
        assert client.post("/api/analytics/pageview", params={"path": "/share?city=Berlin"}).status_code == 204
        assert client.get("/api/analytics/summary?days=7").status_code == 401
        assert client.get("/api/analytics/summary?days=7",
                          headers={"Authorization": "Bearer test-only-secret"}).status_code == 200
        response = client.get("/api/analytics/summary?days=7",
                              headers={"Authorization": "Bearer test-only-secret"})
        data = response.json()
        assert data["total"] == 3
        assert data["pages"]["/"] == 2
        assert data["pages"]["/share"] == 1
        assert len(data["daily"]) == 7
        assert set(data["daily"][-1]) == {"date", "views", "pages", "events"}
        assert "unique_visitors" not in data

        # Funnel events: only allow-listed names count, nothing free-form.
        assert client.post("/api/analytics/event", params={"name": "share_click"}).status_code == 204
        assert client.post("/api/analytics/event", params={"name": "affiliate_click"}).status_code == 204
        assert client.post("/api/analytics/event",
                           params={"name": "wohnort_malaga"}).status_code == 204
        funnel = client.get("/api/analytics/summary?days=7",
                            headers={"Authorization": "Bearer test-only-secret"}).json()
        assert funnel["events"]["share_click"] == 1
        assert funnel["events"]["affiliate_click"] == 1
        assert funnel["events_total"] == 2
        assert "wohnort_malaga" not in json.dumps(funnel)
        assert funnel["total"] == 3  # page views stay page views
