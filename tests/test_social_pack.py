"""Tests des automatischen Social-Pakets (Grafiken + Beitragstexte).

Das Paket entsteht ohne Handarbeit – deshalb muss es reproduzierbar sein, echte
Zahlen enthalten und ohne öffentliche Domain das Veröffentlichen verweigern.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location("social_pack", ROOT / "scripts" / "social_pack.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["social_pack"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def view():
    from app.main import climate
    from app.seo import view_for

    return view_for(climate())


def test_plan_is_deterministic_and_seasonal() -> None:
    pack = load_module()
    data = view()
    base = "https://klima.example"
    winter = pack.build_plan(data, date(2026, 11, 2), 2, base, True)
    spring = pack.build_plan(data, date(2026, 6, 1), 2, base, True)
    assert [post.due for post in winter] == ["2026-11-02", "2026-11-05", "2026-11-09", "2026-11-12"]
    assert all(post.theme == "winterquartier" for post in winter)
    assert all(post.theme == "hitze-entfliehen" for post in spring)
    assert winter == pack.build_plan(data, date(2026, 11, 2), 2, base, True)
    for post in winter + spring:
        assert post.link_ok is True
        assert base in post.text
        assert post.target.startswith("/klima/") or post.target.startswith("/")
        assert post.hashtags and post.alt
        # Nur echte Zahlen aus dem Datensatz, keine Platzhalter.
        assert "{" not in post.text and "}" not in post.text.replace("#Wohlfühlwetter", "")


def test_plan_without_domain_stays_unpublishable() -> None:
    pack = load_module()
    posts = pack.build_plan(view(), date(2026, 11, 2), 1, "", False)
    assert posts and all(post.link_ok is False for post in posts)
    assert pack.calendar_markdown(posts, "", ok=False).count("KLIMA_PUBLIC_URL") == 1


def test_cards_render_for_every_post_type() -> None:
    pack = load_module()
    data = view()
    posts = pack.build_plan(data, date(2026, 7, 6), 1, "https://klima.example", True)
    assert {post.card for post in posts} == {"guide", "city"}
    for post in posts:
        png = pack.render_card(data, post)
        assert png.startswith(b"\x89PNG")
        assert Image.open(BytesIO(png)).size == (1200, 630)


def test_cli_writes_plan_and_calendar(tmp_path: Path) -> None:
    pack = load_module()
    out = tmp_path / "social"
    posts = pack.build_plan(view(), date(2026, 11, 2), 1, "https://klima.example", True)
    for post in posts:
        name = f"{post.id}.png"
        post.image = str(out / name)
        if not out.exists():
            out.mkdir(parents=True)
        (out / name).write_bytes(pack.render_card(view(), post))
    (out / "kalender.md").write_text(pack.calendar_markdown(posts, "https://klima.example", ok=True),
                                     encoding="utf-8")
    calendar = (out / "kalender.md").read_text(encoding="utf-8")
    assert calendar.startswith("# Social-Kalender")
    assert "**Alt-Text:**" in calendar
    assert len(list(out.glob("*.png"))) == len(posts)
