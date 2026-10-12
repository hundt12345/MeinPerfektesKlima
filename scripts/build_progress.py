#!/usr/bin/env python3
"""Fortschrittsmeldung des Datenbaus – für den Balken auf der Startseite.

Der Datenlauf schreibt ``app/data/build_progress.json`` und veröffentlicht die
Datei zusätzlich auf dem Git-Zweig ``klima-fortschritt`` (nicht ``main``: jeder
Push nach ``main`` würde ein Render-Deployment auslösen). Die Web-App liest die
Datei über ``raw.githubusercontent.com`` und zeigt daraus den Fortschritt.

Ohne ``GITHUB_TOKEN`` (lokale Läufe, Tests) wird nur die Datei geschrieben.

Als Modul:  from build_progress import Progress
Als Skript: python scripts/build_progress.py --status done --message "fertig"
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
PROGRESS_PATH = "app/data/build_progress.json"
DEFAULT_BRANCH = "klima-fortschritt"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _request(method: str, url: str, payload: dict | None = None, token: str | None = None,
             accept: str = "application/vnd.github+json", timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": accept, "User-Agent": "meinperfektesklima-datenbau",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


class Progress:
    """Schreibt (und veröffentlicht) den Baufortschritt – fehlertolerant.

    Kein Fehler beim Veröffentlichen darf den Datenlauf abbrechen: Der Balken
    ist nett, der Datensatz ist wichtig.
    """

    def __init__(self, out_dir: Path | str = ROOT / "app" / "data",
                 repo: str | None = None, branch: str | None = None,
                 token: str | None = None, run_url: str | None = None,
                 publish_every: float = 150.0, log_tail: int = 8,
                 tail_source: Path | str | None = None, verbose: bool = True,
                 inherit: bool = True):
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / "build_progress.json"
        self.repo = repo if repo is not None else os.environ.get("GITHUB_REPOSITORY", "")
        self.branch = branch or os.environ.get("KLIMA_PROGRESS_BRANCH", DEFAULT_BRANCH)
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.run_url = run_url or os.environ.get("GITHUB_RUN_URL", "")
        self.publish_every = float(publish_every)
        self.log_tail_len = int(log_tail)
        self.tail_source = Path(tail_source) if tail_source else None
        self.verbose = verbose
        self.started_at = now_iso()
        self._last_publish = 0.0
        self._branch_ready = False
        self._file_sha: str | None = None
        self.state: dict = {
            "schema": 1, "status": "running", "phase": "Start", "percent": 0.0,
            "message": "Datenlauf gestartet", "blocks_done": 0, "blocks_total": 0,
            "rate": "", "eta_minutes": None, "source": "", "started_at": self.started_at,
            "updated_at": self.started_at, "run_url": self.run_url, "log_tail": [],
        }
        if inherit:
            self._inherit_existing()

    def _inherit_existing(self) -> None:
        """Vorhandenen Stand übernehmen (Prozent, Blöcke) – ein nachträglicher
        Abschluss- oder Neustart-Aufruf soll den Balken nicht auf 0 % werfen."""
        try:
            alt = json.loads(self.path.read_text())
        except Exception:  # noqa: BLE001
            return
        if not isinstance(alt, dict) or alt.get("schema") != 1:
            return
        merged = dict(self.state)
        merged.update(alt)
        merged.update({"started_at": self.started_at, "updated_at": self.started_at,
                       "status": "running", "log_tail": alt.get("log_tail", []),
                       "run_url": self.run_url or alt.get("run_url", "")})
        self.state = merged

    # ------------------------------------------------------------------ Inhalt
    def update(self, force: bool = False, **kwargs) -> None:
        self.state.update({k: v for k, v in kwargs.items() if v is not None})
        self.state["updated_at"] = now_iso()
        if self.tail_source and self.tail_source.exists():
            try:
                lines = self.tail_source.read_text(errors="replace").splitlines()
                self.state["log_tail"] = [l[:220] for l in lines[-self.log_tail_len:]]
            except OSError:
                pass
        self.write()
        if self.token and self.repo and (force or
                                        time.monotonic() - self._last_publish >= self.publish_every):
            self.publish()

    def finish(self, status: str, message: str, percent: float | None = None, **kwargs) -> None:
        self.update(force=True, status=status, message=message, phase=kwargs.pop("phase", "Fertig"),
                    percent=percent if percent is not None else self.state.get("percent"), **kwargs)

    def write(self) -> None:
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False))
            os.replace(tmp, self.path)
        except OSError as exc:
            if self.verbose:
                print(f"  ! Fortschritt konnte nicht geschrieben werden: {exc}", flush=True)

    # ------------------------------------------------------- GitHub (optional)
    def _ensure_branch(self) -> bool:
        if self._branch_ready:
            return True
        base = f"{API}/repos/{self.repo}"
        try:
            _request("GET", f"{base}/git/ref/heads/{self.branch}", token=self.token)
            self._branch_ready = True
            return True
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return False
        except Exception:  # noqa: BLE001
            return False
        sha = os.environ.get("GITHUB_SHA", "")
        try:
            if not sha:
                sha = _request("GET", f"{base}/commits/main", token=self.token).get("sha", "")
            if not sha:
                return False
            _request("POST", f"{base}/git/refs",
                     {"ref": f"refs/heads/{self.branch}", "sha": sha}, token=self.token)
            self._branch_ready = True
            return True
        except Exception:  # noqa: BLE001
            return False

    def publish(self) -> bool:
        """Fortschrittsdatei auf den Fortschrittszweig legen (ein Datei-Commit)."""
        if not (self.token and self.repo):
            return False
        if not self._ensure_branch():
            if self.verbose:
                print("  ! Fortschrittszweig nicht erreichbar – Balken nutzt den deployten Stand",
                      flush=True)
            self.token = ""          # nicht bei jedem Block erneut versuchen
            return False
        base = f"{API}/repos/{self.repo}/contents/{PROGRESS_PATH}?ref={self.branch}"
        try:
            if self._file_sha is None:
                try:
                    self._file_sha = _request("GET", base, token=self.token).get("sha")
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    self._file_sha = ""
            payload = {
                "message": f"Datenbau: {self.state.get('percent', 0):.0f} % – "
                           f"{str(self.state.get('message', ''))[:80]}",
                "content": base64.b64encode(
                    json.dumps(self.state, indent=2, ensure_ascii=False).encode()).decode(),
                "branch": self.branch,
            }
            if self._file_sha:
                payload["sha"] = self._file_sha
            res = _request("PUT", f"{API}/repos/{self.repo}/contents/{PROGRESS_PATH}",
                           payload, token=self.token)
            self._file_sha = (res.get("content") or {}).get("sha") or self._file_sha
            self._last_publish = time.monotonic()
            return True
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:200]
            except Exception:  # noqa: BLE001
                pass
            if self.verbose:
                print(f"  ! Fortschritt nicht veröffentlicht (HTTP {exc.code}): {detail}", flush=True)
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                print(f"  ! Fortschritt nicht veröffentlicht: {type(exc).__name__}: {exc}", flush=True)
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", default="running")
    ap.add_argument("--phase", default=None)
    ap.add_argument("--message", default="")
    ap.add_argument("--percent", type=float, default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--branch", default=None)
    ap.add_argument("--out", default=str(ROOT / "app" / "data"))
    ap.add_argument("--tail", default=None, help="Protokolldatei für die letzten Zeilen")
    args = ap.parse_args()

    prog = Progress(out_dir=args.out, branch=args.branch,
                    tail_source=args.tail if args.tail else None)
    prog.finish(args.status, args.message or args.status, percent=args.percent,
                phase=args.phase, source=args.source)
    print(json.dumps(prog.state, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
