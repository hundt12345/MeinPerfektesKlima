#!/usr/bin/env python3
"""Erzeugt ein fertiges Social-Media-Paket aus den Klimadaten – ohne Handarbeit.

Für die nächsten Wochen entstehen je Beitrag:

* eine 1200×630-Grafik (Karte oder Liste) als PNG-Datei,
* ein fertiger Beitragstext mit Link und Hashtags,
* ein Alt-Text und ein Dateiname, der Datum und Thema enthält.

Alles ist **vorberechnet und reproduzierbar**: gleicher Starttag → gleiches
Paket. Es wird nichts veröffentlicht; das Hochladen übernimmt entweder ein
Planungswerkzeug (Dateien einplanen) oder optional ``scripts/social_post.py``.

Beispiel:
    python scripts/social_pack.py --start 2026-11-01 --weeks 4 --out social
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import seo  # noqa: E402
from app.climate import MONTH_NAMES, ClimateData  # noqa: E402
from app.sharing import render_share_card  # noqa: E402

HASHTAGS = ["#Wohlfühlwetter", "#Klima", "#Europa", "#Winterquartier", "#Reisen"]


@dataclass
class Post:
    id: str
    due: str
    theme: str
    card: str            # "guide" | "city" | "home"
    target: str          # Pfad, z. B. /ueberwintern-in-europa
    title: str
    text: str
    alt: str
    hashtags: list[str]
    image: str
    link_ok: bool


def _base_url(explicit: str | None) -> tuple[str, bool]:
    value = (explicit or os.environ.get("KLIMA_PUBLIC_URL") or "").strip().rstrip("/")
    if value.startswith("http://") or value.startswith("https://"):
        return value, True
    # Bis die echte Domain bekannt ist, bleibt das Paket erzeugbar, aber
    # absichtlich nicht veröffentlichbar (social_post.py verweigert es).
    return "", False


def _load_dataset(data_dir: str) -> ClimateData:
    path = Path(data_dir)
    if not (path / "climate_grid.npz").exists():
        raise SystemExit(
            f"Kein Klimaraster in {path} gefunden. Zuerst den Workflow „Klimadaten bauen“ "
            "ausführen oder --data-dir setzen.")
    return ClimateData(path)


def _season_theme(day: date) -> str:
    month = day.month
    if month in (11, 12, 1, 2):
        return "winterquartier"
    if month == 3:
        return "winter-endspurt"
    if month in (4, 5):
        return "sommer-planen"
    if month in (6, 7, 8):
        return "hitze-entfliehen"
    return "winter-vorbereiten"


def _guide_for(theme: str) -> seo.Guide:
    if theme in {"winterquartier", "winter-endspurt", "winter-vorbereiten"}:
        return seo.GUIDES["ueberwintern-in-europa"]
    return seo.GUIDES["sommerhitze-entfliehen"]


def _city_post(view: seo.DatasetView, day: date, theme: str, base: str, ok: bool,
               index: int) -> Post:
    guide = _guide_for(theme)
    # Bewusst nur Orte mit eigener Profilseite: Jeder Beitrag hat damit ein
    # dauerhaftes Ziel, statt auf die Startseite zurückzufallen.
    counts = view.counts(guide.counts_key)
    ranked = sorted(view.entries, key=lambda item: -float(counts[item.point]))
    if not ranked:
        raise SystemExit("Der Datensatz liefert keine Ortsprofile.")
    entry = ranked[min(index, len(ranked) - 1)]
    row = {"name": entry.name, "country": entry.country, "days": view.days(entry, guide.counts_key)}
    slug = entry.slug
    winter = theme in {"winterquartier", "winter-endspurt", "winter-vorbereiten"}
    profile = seo.PROFILE_WINTER_TEXT if winter else (
        "tagsüber 12–28 °C, nachts nicht über 19 °C, Taupunkt höchstens 15 °C, mindestens 4 h Sonne")
    window = "im Winterfenster Oktober bis April" if winter else "in Juli und August"
    label = "Wohlfühltage im Winterfenster" if winter else "Tage im Juli/August"
    link = f"{base}/klima/{slug}" if slug else f"{base}/klima"
    text = (
        f"{row['name']} ({row['country']}): {seo.de(row['days'], 1)} von "
        f"{seo.de(view.cd.month_limit(guide.months, 'all'), 1)} möglichen Tagen {window} "
        f"passen zu diesem Wetter – {profile}. "
        f"Eigenes Wunschwetter auf der Karte einstellen: {base}/"
    )
    return Post(
        id=f"{day.isoformat()}-stadt-{slug or 'europa'}",
        due=day.isoformat(),
        theme=theme,
        card="city",
        target=f"/klima/{slug}" if slug else "/klima",
        title=f"{row['name']}: {seo.de(row['days'], 1)} {label}",
        text=text.rstrip() + f"\n\nOrtsprofil mit Monatsverlauf: {link}",
        alt=(f"Karte von Mein perfektes Klima mit dem Ergebnis für {row['name']}: "
             f"{seo.de(row['days'], 1)} {label}"),
        hashtags=HASHTAGS if winter else ["#Sommerhitze", "#Klima", "#Europa", "#Reisen"],
        image="",
        link_ok=ok,
    )


def _guide_post(day: date, theme: str, base: str, ok: bool) -> Post:
    guide = _guide_for(theme)
    winter = guide.slug == "ueberwintern-in-europa"
    text = (
        ("Der Winter in Mitteleuropa: grau und nass. Im Süden liegen an einzelnen Orten "
         "über die Hälfte der Tage im Winterfenster im Wohlfühlbereich."
         if winter else
         "Hitze ist nicht nur Temperatur: Ein Taupunkt über 18 °C macht 26 °C drückender "
         "als 32 °C trockene Luft.")
        + " Die Rangliste rechnet mit echten ERA5-Tageswerten der Jahre 2016–2025, "
          "nicht mit Jahresmitteln."
    )
    link = f"{base}/{guide.slug}"
    return Post(
        id=f"{day.isoformat()}-{guide.slug}",
        due=day.isoformat(),
        theme=theme,
        card="guide",
        target=f"/{guide.slug}",
        title=guide.title,
        text=f"{text}\n\n{link}",
        alt=f"Auswertung: {guide.title}",
        hashtags=HASHTAGS if winter else ["#Sommerhitze", "#Klima", "#Europa", "#Reisen"],
        image="",
        link_ok=ok,
    )


def build_plan(view: seo.DatasetView, start: date, weeks: int, base: str, ok: bool) -> list[Post]:
    posts: list[Post] = []
    for week in range(weeks):
        monday = start + timedelta(days=7 * week)
        theme = _season_theme(monday)
        posts.append(_guide_post(monday, theme, base, ok))
        posts.append(_city_post(view, monday + timedelta(days=3), theme, base, ok, week))
    return posts


def render_card(view: seo.DatasetView, post: Post) -> bytes:
    """Je Beitrag die passende Grafik – immer aus den aktuellen Daten."""
    if post.card == "guide":
        guide = seo.GUIDES[post.target.lstrip("/")]
        return seo.render_guide_card(view, guide)
    if post.card == "home":
        return seo.render_home_card(view)
    slug = post.target.split("/")[-1]
    entry, context = seo.city_context_by_slug(view, slug)
    if entry is None or context is None:
        return seo.render_home_card(view)
    state = seo.social_card_state(entry)
    location = f"{entry.name}, {entry.country}" if entry.country else entry.name
    return render_share_card(context["report"], state, location)


def calendar_markdown(posts: list[Post], base: str, *, ok: bool) -> str:
    lines = ["# Social-Kalender (automatisch erzeugt)", "",
             f"Zeitraum: {posts[0].due} bis {posts[-1].due}" if posts else "leer", ""]
    if not ok:
        lines += ["> **Hinweis:** `KLIMA_PUBLIC_URL` (oder `--base-url`) ist nicht gesetzt. "
                  "Die Links sind deshalb leer und `social_post.py` verweigert das "
                  "Veröffentlichen – so landet garantiert kein kaputter Link online.", ""]
    for post in posts:
        lines += [
            f"## {post.due} · {post.theme}", "",
            f"**Bild:** `{post.image}`", "",
            f"**Alt-Text:** {post.alt}", "",
            "**Beitrag:**", "", "```text", post.text.strip(), "", " ".join(post.hashtags), "```", "",
        ]
    lines += ["## Regeln fürs Veröffentlichen", "",
              "* Immer den Orts- oder Ratgeber-Link mitnehmen, nie nur die Grafik.",
              "* Keine Aussagen über Miete, Visum oder Reiseverhalten – die Zahlen beschreiben "
              "nur das Wetter.",
              "* Quellenhinweis (ERA5/Copernicus) steht in der Grafik; bei eigenen Bildern "
              "selbst ergänzen.",
              "* Bei Änderungen an den Daten einfach neu erzeugen – das Paket ist reproduzierbar.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Social-Media-Paket aus den Klimadaten bauen")
    parser.add_argument("--start", default=date.today().isoformat(),
                        help="Erster Tag des Plans (JJJJ-MM-TT), Standard: heute")
    parser.add_argument("--weeks", type=int, default=4, help="Anzahl Wochen (Standard 4)")
    parser.add_argument("--out", default="social", help="Zielordner (Standard: social/)")
    parser.add_argument("--data-dir", default=os.environ.get("KLIMA_DATA_DIR", str(ROOT / "app" / "data")))
    parser.add_argument("--base-url", default=None, help="Öffentliche Basis-URL der Website")
    parser.add_argument("--no-images", action="store_true", help="Nur Kalender und JSON schreiben")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    base, ok = _base_url(args.base_url)
    cd = _load_dataset(args.data_dir)
    view = seo.view_for(cd)
    posts = build_plan(view, start, max(1, args.weeks), base, ok)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for post in posts:
        name = f"{post.id}.png"
        post.image = str((out / name))
        if not args.no_images:
            (out / name).write_bytes(render_card(view, post))
    (out / "plan.json").write_text(json.dumps(
        {"schema_version": 1, "base_url": base, "link_ok": ok,
         "generated_for": start.isoformat(), "posts": [asdict(post) for post in posts]},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "kalender.md").write_text(calendar_markdown(posts, base, ok=ok), encoding="utf-8")

    print(f"{len(posts)} Beiträge in {out} erzeugt"
          + ("" if args.no_images else f" (inkl. {len(posts)} Grafiken)"))
    if not ok:
        print("Hinweis: KLIMA_PUBLIC_URL ist nicht gesetzt – Links fehlen, Veröffentlichen "
              "ist gesperrt (siehe social/kalender.md).", file=sys.stderr)
    print("Erste Beiträge: " + "; ".join(f"{post.due}: {post.title}" for post in posts[:3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
