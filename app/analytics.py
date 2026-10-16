"""Cookieless, first-party page-view and funnel counters.

Only a UTC day, one of two coarse page labels, and a fixed set of event names
from an allow-list are stored. No IP address, user agent, referrer, cookie,
unique visitor, query string, fragment, place, or free-form label is read or
persisted. Alongside plain page views the events close the funnel gap named in
`docs/monetarisierung.md`: how often a result was shared, how often a partner
link was opened, and how often the content pages were used. SQLite is
intentionally used so the feature needs no analytics vendor or account.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("/tmp/klima-analytics.sqlite3")
PAGES = ("/", "/share")
# Feste Ereignisnamen – der Client kann nur diese senden, nichts Freies.
# Sie sind grob genug, um keine Person zu beschreiben (kein Ort, kein Filter,
# kein Suchbegriff, kein Kampagnenwert).
EVENTS = (
    "share_click",      # nativer Teilen-Dialog oder Teilen-Menü benutzt
    "share_copy",       # Ergebnis-Link kopiert
    "share_target",     # Klick auf einen konkreten Netzwerk-Link
    "compare_start",    # bewusster Start eines Zwei-Orte-Vergleichs
    "compare_complete", # zweiter Ort ausgewählt und Werte berechnet
    "explanation_open", # deterministische Ergebnis-Erklärung geöffnet
    "affiliate_click",  # Partner-/Unterkunftslink geöffnet
    "ad_click",         # selbst konfigurierter Sponsorplatz geklickt
    "badge_copy",       # Einbettungs-Code kopiert
    "city_page",        # Inhaltsseite eines Ortes aufgerufen
    "guide_page",       # Ratgeberseite aufgerufen
    "app_open",         # Start der Klimasuche aus einer Inhaltsseite heraus
)


def is_enabled() -> bool:
    value = os.environ.get("KLIMA_ANALYTICS_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def database_path() -> Path:
    """Configured SQLite path; defaults outside the checked-out source tree."""
    configured = os.environ.get("KLIMA_ANALYTICS_DB", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_DB


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS daily_pageviews (
            day TEXT NOT NULL,
            page TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, page)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS daily_events (
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, name)
        )
    """)
    return connection


def record_pageview(page: str) -> None:
    """Increment one aggregate counter. Unknown labels collapse to the home page."""
    normalized = "/share" if page == "/share" else "/"
    today = datetime.now(timezone.utc).date().isoformat()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    with closing(_connect()) as connection, connection:
        connection.execute("""
            INSERT INTO daily_pageviews (day, page, views) VALUES (?, ?, 1)
            ON CONFLICT (day, page) DO UPDATE SET views = views + 1
        """, (today, normalized))
        connection.execute("DELETE FROM daily_pageviews WHERE day < ?", (cutoff,))


def record_event(name: str) -> bool:
    """Increment one aggregate counter for an allow-listed event name.

    Returns ``False`` for unknown names: nothing is stored and nothing is
    logged, so a manipulated client cannot smuggle data into the database.
    """
    if name not in EVENTS:
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    with closing(_connect()) as connection, connection:
        connection.execute("""
            INSERT INTO daily_events (day, name, count) VALUES (?, ?, 1)
            ON CONFLICT (day, name) DO UPDATE SET count = count + 1
        """, (today, name))
        connection.execute("DELETE FROM daily_events WHERE day < ?", (cutoff,))
    return True


def event_summary(days: int = 30) -> dict[str, Any]:
    """Daily totals per allow-listed event; zero-filled, no user-level data."""
    days = max(1, min(365, int(days)))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    counts: dict[tuple[str, str], int] = {}
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            "SELECT day, name, count FROM daily_events WHERE day >= ? ORDER BY day, name",
            (start.isoformat(),),
        ).fetchall()
    for day, name, count in rows:
        if name in EVENTS:
            counts[(day, name)] = int(count)
    daily = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        key = day.isoformat()
        names = {name: counts.get((key, name), 0) for name in EVENTS}
        daily.append({"date": key, "events": names, "total": sum(names.values())})
    return {
        "days": days,
        "from": start.isoformat(),
        "through": today.isoformat(),
        "total": sum(row["total"] for row in daily),
        "events": {name: sum(row["events"][name] for row in daily) for name in EVENTS},
        "daily": daily,
    }


def pageview_summary(days: int = 30) -> dict[str, Any]:
    """Daily totals with zero-filled dates; deliberately no visitor-level data."""
    days = max(1, min(365, int(days)))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    counts: dict[tuple[str, str], int] = {}
    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            "SELECT day, page, views FROM daily_pageviews WHERE day >= ? ORDER BY day, page",
            (start.isoformat(),),
        ).fetchall()
    for day, page, views in rows:
        if page in PAGES:
            counts[(day, page)] = int(views)

    daily = []
    for offset in range(days):
        day: date = start + timedelta(days=offset)
        key = day.isoformat()
        pages = {page: counts.get((key, page), 0) for page in PAGES}
        daily.append({"date": key, "views": sum(pages.values()), "pages": pages})
    return {
        "days": days,
        "from": start.isoformat(),
        "through": today.isoformat(),
        "total": sum(row["views"] for row in daily),
        "pages": {page: sum(row["pages"][page] for row in daily) for page in PAGES},
        "daily": daily,
    }
