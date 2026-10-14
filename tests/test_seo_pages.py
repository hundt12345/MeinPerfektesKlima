"""Tests der indexierbaren Inhaltsseiten, Grafiken, Sitemap, Feed und Rechtstexte.

Die Seiten sind der eigentliche Streuweg (Suchmaschinen, Social Media). Deshalb
prüfen diese Tests nicht nur Statuscodes, sondern auch, dass jede Seite
selbstständig beschreibbar bleibt: eigene Canonical-URL, eigene Vorschau-Grafik,
echte Zahlen und ein Deep-Link, den der Teilen-Dekoder akzeptiert.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.affiliate import affiliate_enabled
from app.main import app
from app.sharing import decode_share_state


def _view():
    from app.main import climate
    from app.seo import view_for

    return view_for(climate())


def test_robots_and_sitemap_are_consistent() -> None:
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert robots.headers["content-type"].startswith("text/plain")
        assert "Sitemap: http://testserver/sitemap.xml" in robots.text
        assert "Disallow: /api/" in robots.text

        sitemap = client.get("/sitemap.xml")
        assert sitemap.status_code == 200
        root = ET.fromstring(sitemap.content)
        urls = {element.text for element in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
        assert "http://testserver/" in urls
        assert "http://testserver/klima" in urls
        assert "http://testserver/ueberwintern-in-europa" in urls
        assert "http://testserver/sommerhitze-entfliehen" in urls
        # Jede Seite aus dem Verzeichnis ist enthalten und absolut verlinkt.
        sample = [entry.slug for entry in _view().entries[:5]]
        for slug in sample:
            assert f"http://testserver/klima/{slug}" in urls
        assert all(url.startswith("http://testserver/") for url in urls)
        assert len(urls) == len(_view().entries) + 4


def test_city_page_has_own_metadata_numbers_and_deep_link() -> None:
    view = _view()
    entry = next(item for item in view.entries if item.name == "Berlin")
    with TestClient(app) as client:
        page = client.get(f"/klima/{entry.slug}")
        assert page.status_code == 200
        assert page.headers["cache-control"].startswith("public")
        text = page.text
        assert f"<title>{entry.name}:" in text
        assert f'rel="canonical" href="http://testserver/klima/{entry.slug}"' in text
        assert f'content="http://testserver/klima/{entry.slug}/card.png"' in text
        assert 'name="twitter:card" content="summary_large_image"' in text
        assert "Wohlfühltage pro Jahr" in text
        # FAQ und Brotkrumen als strukturierte Daten
        assert "FAQPage" in text and "BreadcrumbList" in text and "schema.org" in text
        # Teilen-Links sind normale Links, keine Fremd-Skripte
        assert "wa.me" in text and "bsky.app" in text
        assert "<script src=" not in text

        # Der Deep-Link in die App passt zum serverseitigen Teilen-Format.
        match = re.search(r'href="http://testserver/\?state=([A-Za-z0-9_-]+)"', text)
        assert match, "Deep-Link in die App fehlt"
        state = decode_share_state(match.group(1))
        assert state["mode"] == "year"
        assert abs(state["selected"]["lat"] - entry.lat) < 0.01
        assert state["params"]["sun_min"] == 3.0


def test_city_page_card_and_badge() -> None:
    view = _view()
    entry = next(item for item in view.entries if item.name == "Berlin")
    with TestClient(app) as client:
        card = client.get(f"/klima/{entry.slug}/card.png")
        assert card.status_code == 200
        assert card.headers["content-type"] == "image/png"
        assert Image.open(BytesIO(card.content)).size == (1200, 630)

        badge = client.get(f"/badge/{entry.slug}.svg")
        assert badge.status_code == 200
        assert badge.headers["content-type"].startswith("image/svg+xml")
        assert "Berlin" in badge.text and "Wohlfühltage/Jahr" in badge.text
        assert entry.slug in client.get(f"/klima/{entry.slug}").text  # Einbettungscode


def test_home_card_and_site_status() -> None:
    with TestClient(app) as client:
        card = client.get("/og/home.png")
        assert card.status_code == 200
        assert Image.open(BytesIO(card.content)).size == (1200, 630)
        site = client.get("/api/site").json()
        assert set(site) == {"legal_configured", "affiliate_enabled", "ads_enabled",
                             "analytics_enabled", "landing_pages"}
        assert site["landing_pages"] > 100
        assert site["legal_configured"] is False  # ohne Betreiberangaben


def test_only_profiled_places_get_a_page() -> None:
    from app.seo import CURATED_CITIES, slugify

    view = _view()
    curated = {slugify(name) for name in CURATED_CITIES}
    small = [city for city in view.cd.cities
             if 15000 <= int(city.get("pop", 0)) < 100000 and slugify(city["name"]) not in curated]
    assert small, "Der Datensatz enthält kein kleines, nicht kuratiertes Dorf"
    tiny_slug = slugify(small[0]["name"])
    assert tiny_slug not in view.by_slug
    with TestClient(app) as client:
        assert client.get(f"/klima/{tiny_slug}").status_code == 404
        assert client.get("/klima/gibtesnicht").status_code == 404
        assert client.get("/klima/gibtesnicht/card.png").status_code == 404
        assert client.get("/badge/gibtesnicht.svg").status_code == 404
        assert client.get("/unbekannter-ratgeber").status_code == 404
        assert client.get("/guide/unbekannter-ratgeber/card.png").status_code == 404


def test_guides_index_and_feed() -> None:
    with TestClient(app) as client:
        winter = client.get("/ueberwintern-in-europa")
        assert winter.status_code == 200
        assert "Oktober bis April" in winter.text
        assert "Wie gerechnet wird" in winter.text
        assert "deutsche Städte" in winter.text  # Vergleichstabelle

        summer = client.get("/sommerhitze-entfliehen")
        assert summer.status_code == 200
        assert "Juli und August" in summer.text
        for slug in ("ueberwintern-in-europa", "sommerhitze-entfliehen"):
            card = client.get(f"/guide/{slug}/card.png")
            assert card.status_code == 200
            assert Image.open(BytesIO(card.content)).size == (1200, 630)

        index = client.get("/klima")
        assert index.status_code == 200
        assert "Ortsprofile" in index.text
        assert index.text.count("/klima/") > 100  # Verzeichnis verlinkt alle Orte

        feed = client.get("/feed.xml")
        assert feed.status_code == 200
        assert feed.headers["content-type"].startswith("application/rss+xml")
        channel = ET.fromstring(feed.content).find("channel")
        items = channel.findall("item")
        assert len(items) >= 6
        assert all(item.findtext("link", "").startswith("http://testserver/") for item in items)


def test_landing_pages_need_verified_data(monkeypatch) -> None:
    """Wie die Karte: ohne geprüfte Daten gibt es Inhalte nur als 503, nie erfunden."""
    import importlib

    import app.main as main_module

    monkeypatch.setenv("KLIMA_ALLOW_UNVERIFIED", "")
    main_module = importlib.reload(main_module)
    try:
        with TestClient(main_module.app) as client:
            for path in ("/klima", "/klima/berlin", "/sitemap.xml", "/feed.xml",
                         "/ueberwintern-in-europa", "/og/home.png"):
                assert client.get(path).status_code == 503, path
    finally:
        # Tests laufen mit geöffnetem Datentor (synthetisches Referenzraster).
        monkeypatch.setenv("KLIMA_ALLOW_UNVERIFIED", "1")
        importlib.reload(main_module)


def test_legal_pages_stay_locked_without_operator_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KLIMA_LEGAL_FILE", raising=False)
    monkeypatch.setenv("KLIMA_LEGAL_FILE", str(tmp_path / "missing.json"))
    with TestClient(app) as client:
        for path in ("/impressum", "/datenschutz"):
            response = client.get(path)
            assert response.status_code == 503
            assert "noch nicht eingerichtet" in response.text
        assert client.get("/api/site").json()["legal_configured"] is False


def test_legal_pages_render_configured_operator(tmp_path: Path, monkeypatch) -> None:
    legal = {
        "schema_version": 1,
        "configured": True,
        "updated": "2026-10-14",
        "operator": {"name": "Testbetrieb GmbH", "legal_form": "GmbH",
                     "street": "Teststraße 1", "zip": "12345", "city": "Teststadt",
                     "country": "Deutschland"},
        "contact": {"email": "hallo@example.org", "phone": "+49 30 000000"},
        "responsible": "Vorname Nachname",
        "hosting": {"provider": "Testhosting", "location": "Deutschland",
                    "request_logs": True, "log_retention_days": 7},
    }
    path = tmp_path / "legal.json"
    path.write_text(json.dumps(legal), encoding="utf-8")
    monkeypatch.setenv("KLIMA_LEGAL_FILE", str(path))
    with TestClient(app) as client:
        impressum = client.get("/impressum")
        assert impressum.status_code == 200
        assert "Testbetrieb GmbH" in impressum.text and "Teststraße 1" in impressum.text

        privacy = client.get("/datenschutz")
        assert privacy.status_code == 200
        assert "keine Cookies" in privacy.text
        assert "Testhosting" in privacy.text
        # Zustandsabhängige Abschnitte: standardmäßig weder Werbung noch Partnerlinks.
        assert "Partner-Links" not in privacy.text
        assert "Anzeigen eigener Geschäftspartner" not in privacy.text
        assert client.get("/api/site").json()["legal_configured"] is True

    # Mit aktivierter Partner-Konfiguration wird der Abschnitt ergänzt.
    partner = tmp_path / "affiliate.json"
    partner.write_text(json.dumps({
        "schema_version": 1, "enabled": True, "partners": [{
            "id": "test", "partner": "Testpartner", "label": "Unterkünfte in {city}",
            "url_template": "https://booking.example.com/search?ss={city}&aid=42",
            "hosts": ["booking.example.com"], "contexts": ["year"], "start": "2026-01-01",
            "end": "2027-01-01",
        }],
    }), encoding="utf-8")
    monkeypatch.setenv("KLIMA_AFFILIATE_FILE", str(partner))
    assert affiliate_enabled() is True
    with TestClient(app) as client:
        privacy = client.get("/datenschutz")
        assert "Partner-Links" in privacy.text
        site = client.get("/api/site").json()
        assert site["affiliate_enabled"] is True


def test_home_page_gets_absolute_origin_markers() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "%%ORIGIN%%" not in page.text
        assert 'rel="canonical" href="http://testserver/"' in page.text
        assert 'content="http://testserver/og/home.png"' in page.text
        assert page.text.count("WebApplication") >= 1
        assert 'href="/klima"' in page.text  # Fußzeile verlinkt die Inhaltsseiten
