"""Betreiberangaben für Impressum und Datenschutzerklärung.

Die Seite kann keine Rechtsberatung ersetzen und erfindet keine Angaben: Ohne
vollständige, ausdrücklich als konfiguriert markierte Betreiberdaten antworten
`/impressum` und `/datenschutz` mit **503** und einer kurzen Einrichtungshilfe.
Die Datenschutzerklärung beschreibt die tatsächliche technische Funktionsweise
dieser App (First-Party-Auslieferung, keine Cookies, keine Nutzerkennung,
aggregierte Zähler, Kartenkacheln direkt vom Kartenanbieter, optionale
Partner-Links) und wird aus derselben Quelle erzeugt wie das Verhalten im Code.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_PATH = Path(__file__).parent / "data" / "legal.json"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


class Operator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    legal_form: str = Field(default="", max_length=60)
    street: str = Field(min_length=2, max_length=120)
    zip: str = Field(min_length=2, max_length=12)
    city: str = Field(min_length=2, max_length=80)
    country: str = Field(default="Deutschland", min_length=2, max_length=60)


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=160)
    phone: str = Field(default="", max_length=40)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if not EMAIL.match(value.strip()):
            raise ValueError("Kontakt-E-Mail ist ungültig")
        return value.strip()


class Hosting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=2, max_length=120)
    location: str = Field(default="", max_length=120)
    request_logs: bool = True
    log_retention_days: int = Field(default=7, ge=1, le=365)


class Legal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    configured: bool = False
    updated: str = Field(default="", max_length=20)
    operator: Operator | None = None
    contact: Contact | None = None
    responsible: str = Field(default="", max_length=120)   # § 18 Abs. 2 MStV
    vat_id: str = Field(default="", max_length=40)
    hosting: Hosting | None = None
    affiliate_note: str = Field(default="", max_length=600)
    notes: str = Field(default="", max_length=1200)

    @field_validator("updated")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if not value:
            return value
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("`updated` muss ein Datum JJJJ-MM-TT sein") from exc
        return value

    @model_validator(mode="after")
    def complete_when_configured(self):
        if not self.configured:
            return self
        missing = []
        if self.operator is None:
            missing.append("operator")
        if self.contact is None:
            missing.append("contact")
        if self.hosting is None:
            missing.append("hosting")
        if not self.responsible.strip():
            missing.append("responsible")
        if not self.updated:
            missing.append("updated")
        if missing:
            raise ValueError("Unvollständige Betreiberangaben: " + ", ".join(missing))
        return self


def config_path() -> Path:
    return Path(os.environ.get("KLIMA_LEGAL_FILE", DEFAULT_PATH))


def load_legal() -> Legal | None:
    """Vollständige Betreiberangaben oder ``None`` (Seiten bleiben gesperrt)."""
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    try:
        legal = Legal.model_validate(raw)
    except ValueError:
        return None
    return legal if legal.configured else None


def legal_configured() -> bool:
    return load_legal() is not None


def notice_html() -> str:
    """Kurze Einrichtungshilfe, solange keine Betreiberangaben hinterlegt sind."""
    return (
        "<h1>Diese Seite ist noch nicht eingerichtet</h1>"
        "<p>Impressum und Datenschutzerklärung werden aus den Betreiberangaben erzeugt. "
        "Sie sind noch nicht vollständig hinterlegt, deshalb bleibt die Seite absichtlich gesperrt – "
        "lieber keine Angaben als falsche.</p>"
        "<p>Zum Aktivieren die Vorlage <code>app/data/legal.json</code> ausfüllen und "
        "<code>configured: true</code> setzen (oder <code>KLIMA_LEGAL_FILE</code> auf eine "
        "gepflegte Datei zeigen lassen). Danach erscheinen beide Seiten und die Fußzeilen-Links.</p>"
    )
