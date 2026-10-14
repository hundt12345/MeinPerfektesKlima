"""Cookieless, first-party page-view counters.

Only a UTC day and one of two coarse page labels are stored. No IP address,
user agent, referrer, cookie, unique visitor, query string, or fragment is read
or persisted. SQLite is intentionally used so the feature needs no analytics
vendor or account.
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
