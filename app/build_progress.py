"""Live-Status des Datenbaus für die Startseite.

Die Website zeigt **nur** echte, geprüfte ERA5-Daten. Solange die fehlen, blendet
sie statt einer Karte eine Statusseite mit Fortschrittsbalken ein. Dieser Baustein
liefert die Zahlen dafür.

Woher kommt der Fortschritt?
---------------------------
Der Datensatz wird von der GitHub-Action „Klimadaten bauen" erzeugt, nicht von
diesem Dienst (Render Free hat weder genug Bauzeit noch persistenten Speicher –
siehe README). Der Lauf schreibt seinen Fortschritt deshalb nach
``app/data/build_progress.json`` und veröffentlicht die Datei laufend auf dem
Zweig ``klima-fortschritt`` – bewusst *nicht* auf ``main``, weil jeder Push nach
``main`` ein Render-Deployment auslösen würde.

Dieses Modul holt die Datei per HTTPS (roh von GitHub, mit Cache-Buster), legt
sie ~20 s im Speicher ab und fällt auf die lokal ausgelieferte Kopie zurück,
wenn das Netz nicht erreichbar ist. Jeder Fehler wird geschluckt: Die
Statusseite muss auch dann antworten, wenn GitHub gerade nicht erreichbar ist.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Lock

CACHE_SECONDS = 20.0
# Nach dieser Zeit ohne Lebenszeichen gilt ein Lauf als eingeschlafen.
STALE_AFTER_SECONDS = 25 * 60
PROGRESS_FILE = "app/data/build_progress.json"

DEFAULT_REPO = os.environ.get("KLIMA_REPO", "hundt12345/MeinPerfektesKlima")
PROGRESS_BRANCH = os.environ.get("KLIMA_PROGRESS_BRANCH", "klima-fortschritt")

_cache: dict = {"at": 0.0, "value": None, "key": None}
_lock = Lock()


def progress_url() -> str | None:
    """URL der Fortschrittsdatei; ``None`` schaltet den Fernabruf ab (Tests)."""
    override = os.environ.get("KLIMA_PROGRESS_URL")
    if override is not None:
        return override.strip() or None
    if not DEFAULT_REPO:
        return None
    # Der Cache-Buster (20-Sekunden-Raster) umgeht das CDN von raw.githubusercontent.
    bust = int(time.time()) // 20
    return (f"https://raw.githubusercontent.com/{DEFAULT_REPO}/{PROGRESS_BRANCH}/"
            f"{PROGRESS_FILE}?cb={bust}")


def _read_url(url: str, timeout: float = 8.0) -> dict | None:
    if url.startswith("file://"):
        try:
            return json.loads(Path(url[7:]).read_text())
        except Exception:  # noqa: BLE001
            return None
    if not url.startswith(("http://", "https://")):
        try:
            return json.loads(Path(url).read_text())
        except Exception:  # noqa: BLE001
            return None
    req = urllib.request.Request(url, headers={"User-Agent": "meinperfektesklima-statusseite",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def local_progress(data_dir: Path) -> dict | None:
    """Fortschritt aus dem deployten Datenordner (Stand des letzten Deployments)."""
    path = Path(data_dir) / "build_progress.json"
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _age_seconds(progress: dict) -> float | None:
    stamp = progress.get("updated_at")
    if not stamp:
        return None
    try:
        text = str(stamp).replace("Z", "+00:00")
        from datetime import datetime
        return max(0.0, time.time() - datetime.fromisoformat(text).timestamp())
    except Exception:  # noqa: BLE001
        return None


def classify(progress: dict | None, source: str) -> dict:
    """Zustand + Alter aus dem Fortschrittsdatensatz ableiten."""
    if not progress:
        return {"state": "not_started", "stale": True, "age_seconds": None,
                "progress_source": source}
    age = _age_seconds(progress)
    status = str(progress.get("status") or "").lower()
    out = dict(progress)
    out["age_seconds"] = round(age, 1) if age is not None else None
    out["progress_source"] = source
    if status in ("done", "finished", "complete", "completed", "success"):
        out["state"] = "finished"          # Daten sind gebaut, Deployment steht aus
    elif status in ("failed", "failure", "error", "gate_failed", "cancelled", "aborted"):
        out["state"] = "failed"
    elif age is not None and age > STALE_AFTER_SECONDS:
        out["state"] = "stalled"           # kein Lebenszeichen mehr
    else:
        out["state"] = "building"
    out["stale"] = out["state"] in ("stalled", "not_started")
    return out


def build_status(data_dir: Path, force_refresh: bool = False) -> dict:
    """Fortschritt des Datenbaus (ferngeladen, sonst lokal, sonst „unbekannt")."""
    now = time.monotonic()
    url = progress_url()
    key = (url, str(data_dir))
    with _lock:
        fresh = (_cache["value"] is not None and _cache["key"] == key
                 and (now - _cache["at"]) < CACHE_SECONDS)
        if fresh and not force_refresh:
            return dict(_cache["value"])

    result: dict | None = None
    source = "keine"
    if url:
        result = _read_url(url)
        if result is not None:
            source = "fortschrittszweig" if "raw.githubusercontent" in url else "konfiguriert"
    if result is None:
        result = local_progress(data_dir)
        source = "deployter Stand" if result else "keine"

    status = classify(result, source)
    status.setdefault("percent", None)
    status.setdefault("message", "")
    status.setdefault("phase", "")
    status["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _lock:
        _cache["at"] = now
        _cache["key"] = key
        _cache["value"] = status
    return dict(status)
