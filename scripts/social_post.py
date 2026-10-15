#!/usr/bin/env python3
"""Veröffentlicht fällige Beiträge aus dem Social-Paket – nur wenn ausdrücklich erlaubt.

Standard ist **Trockenlauf**: Das Skript zeigt nur, was es tun würde. Erst mit
``--post`` und gesetzten Zugangsdaten wird wirklich gesendet. Ohne echte Domain
(``KLIMA_PUBLIC_URL``) verweigert es die Arbeit, damit nie ein kaputter Link
veröffentlicht wird.

Unterstützte, kostenlose Wege (keine Werbenetzwerke, kein Fremd-Skript):

* **Mastodon**   ``MASTODON_BASE_URL`` (z. B. https://mastodon.social),
                 ``MASTODON_TOKEN`` (App-Token mit ``write:statuses``,
                 ``write:media``)
* **Telegram**   ``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID`` (Kanal oder Gruppe)
* **Bluesky**    ``BLUESKY_HANDLE``, ``BLUESKY_APP_PASSWORD`` (Textbeitrag mit Link;
                 Bilder gehen über die API nur mit zusätzlichem Blob-Upload)

Instagram, TikTok und Facebook/Instagram-Seiten haben keine freie
Automatisierungsschnittstelle ohne App-Prüfung – dort lädt man die fertigen
Dateien aus ``social/`` hoch oder plant sie in einem Werkzeug ein.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _upload_mastodon_media(base: str, token: str, image: Path) -> str | None:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(image.name)[0] or "image/png"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{image.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        image.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"{base}/api/v2/media", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8")).get("id")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Mastodon-Medienupload fehlgeschlagen: {exc}", file=sys.stderr)
        return None


def _send_mastodon(text: str, image: Path | None) -> bool:
    base = os.environ.get("MASTODON_BASE_URL", "").rstrip("/")
    token = os.environ.get("MASTODON_TOKEN", "")
    if not base or not token:
        return False
    media_ids = []
    if image and image.exists():
        media_id = _upload_mastodon_media(base, token, image)
        if media_id:
            media_ids.append(media_id)
    payload = {"status": text, "visibility": "public", "language": "de"}
    if media_ids:
        payload["media_ids"] = media_ids
    _post_json(f"{base}/api/v1/statuses", payload, {"Authorization": f"Bearer {token}"})
    return True


def _send_telegram(text: str, image: Path | None) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    if image and image.exists():
        boundary = uuid.uuid4().hex
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="chat_id"\r\n\r\n', chat.encode(), b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="caption"; \r\n\r\n'.encode(),
            text.encode("utf-8")[:1000], b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="photo"; filename="{image.name}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n", image.read_bytes(), b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendPhoto", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": False})
            .encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60):
        return True


def _send_bluesky(text: str) -> bool:
    handle = os.environ.get("BLUESKY_HANDLE", "")
    password = os.environ.get("BLUESKY_APP_PASSWORD", "")
    if not handle or not password:
        return False
    session = _post_json("https://bsky.social/xrpc/com.atproto.server.createSession",
                         {"identifier": handle, "password": password}, {})
    if not session.get("accessJwt"):
        return False
    _post_json("https://bsky.social/xrpc/com.atproto.repo.createRecord",
               {"repo": session["did"], "collection": "app.bsky.feed.post",
                "record": {"$type": "app.bsky.feed.post", "text": text[:300],
                           "langs": ["de"], "createdAt": _now()}},
               {"Authorization": f"Bearer {session['accessJwt']}"})
    return True


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fällige Social-Beiträge veröffentlichen")
    parser.add_argument("--plan", default="social/plan.json")
    parser.add_argument("--due", default=date.today().isoformat(),
                        help="Nur Beiträge mit diesem Fälligkeitstag (Standard: heute)")
    parser.add_argument("--post", action="store_true", help="wirklich senden (Standard: Trockenlauf)")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"Kein Plan unter {plan_path}. Zuerst scripts/social_pack.py ausführen.",
              file=sys.stderr)
        return 1
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan.get("link_ok"):
        print("Keine öffentliche Basis-URL (KLIMA_PUBLIC_URL) gesetzt – es wird nichts "
              "veröffentlicht.", file=sys.stderr)
        return 2
    due = [post for post in plan.get("posts", []) if post.get("due") == args.due]
    if not due:
        print(f"Keine fälligen Beiträge für {args.due}.")
        return 0

    channels = [name for name, values in {
        "Mastodon": (os.environ.get("MASTODON_BASE_URL"), os.environ.get("MASTODON_TOKEN")),
        "Telegram": (os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")),
        "Bluesky": (os.environ.get("BLUESKY_HANDLE"), os.environ.get("BLUESKY_APP_PASSWORD")),
    }.items() if all(values)]
    if not channels:
        print("Keine Zugangsdaten gefunden (MASTODON_*, TELEGRAM_*, BLUESKY_*). "
              "Trockenlauf zeigt nur den Inhalt:", file=sys.stderr)

    for post in due:
        text = post["text"].strip() + "\n\n" + " ".join(post.get("hashtags", []))
        image = Path(post["image"]) if post.get("image") else None
        print(f"→ {post['due']} {post['title']} ({post['target']})"
              + (f" · Bild: {image.name}" if image and image.exists() else ""))
        if not args.post:
            print("  Trockenlauf – nicht gesendet.")
            continue
        sent = []
        for name, sender in (("Mastodon", lambda: _send_mastodon(text, image)),
                             ("Telegram", lambda: _send_telegram(text, image)),
                             ("Bluesky", lambda: _send_bluesky(text))):
            try:
                if sender():
                    sent.append(name)
            except urllib.error.HTTPError as exc:
                print(f"  {name} fehlgeschlagen: HTTP {exc.code}", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                print(f"  {name} fehlgeschlagen: {exc}", file=sys.stderr)
        print("  gesendet an: " + (", ".join(sent) if sent else "nichts (keine Kanäle konfiguriert)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
