"""Partner-/Affiliate-Link-Adapter – serverseitig konfiguriert, standardmäßig aus.

Die App kennt nur *einen* Link pro Ort und Kontext. Es gibt **keinen** eigenen
Redirect-Dienst, keine Pixel, kein Besucher-Tracking und keine freie
Weiterleitungs-URL aus Besuchereingaben. Die Konfiguration ist eine
Betreiber-Datei (`app/data/affiliate.json` oder `KLIMA_AFFILIATE_FILE`), kein
öffentliches Formular.

Fail-closed heißt hier konkret: fehlt die Datei, ist sie ausgeschaltet, ist der
Vertrag ungültig oder zeigt die erzeugte URL nicht auf einen erlaubten Host,
wird **kein** Call-to-Action angezeigt. Ein ungültiger Eintrag deaktiviert die
gesamte Konfiguration – lieber kein Link als ein falscher.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTEXTS = {"year", "month", "wq"}
# Platzhalter, die in `url_template` erlaubt sind. Bewusst nur Ortsangaben –
# keine Reisedaten, keine Gästezahl, keine Besucherkennungen. An-/Abreise und
# Personenzahl werden laut docs/monetarisierung.md nur nach ausdrücklicher
# Bestätigung übergeben und stehen hier deshalb noch nicht zur Verfügung.
PLACEHOLDERS = {"city", "country", "lat", "lon", "slug"}
DEFAULT_PATH = Path(__file__).parent / "data" / "affiliate.json"
MAX_TEMPLATE_LENGTH = 1024
DISCLOSURE_DEFAULT = (
    "Bei einer Buchung über diesen Link können wir eine Provision erhalten. "
    "Der Klima-Vergleich bleibt davon unabhängig; Preise und Verfügbarkeit "
    "prüfst du beim Anbieter."
)


class Partner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    partner: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=90)
    note: str = Field(default="", max_length=240)
    url_template: str = Field(max_length=MAX_TEMPLATE_LENGTH)
    hosts: list[str] = Field(min_length=1, max_length=10)
    contexts: list[str] = Field(default_factory=lambda: sorted(CONTEXTS))
    countries: list[str] = Field(default_factory=list, max_length=60)  # optional: nur diese Länder
    start: date | None = None
    end: date | None = None
    enabled: bool = True

    @field_validator("hosts")
    @classmethod
    def valid_hosts(cls, value: list[str]) -> list[str]:
        cleaned = []
        for host in value:
            host = host.strip().lower().rstrip(".")
            if (not host or "/" in host or ":" in host or any(ch.isspace() for ch in host)
                    or host.startswith("*") or "." not in host):
                raise ValueError("Hosts müssen exakte Hostnamen ohne Platzhalter sein")
            cleaned.append(host)
        if not cleaned:
            raise ValueError("Mindestens ein erlaubter Host nötig")
        return cleaned

    @field_validator("contexts")
    @classmethod
    def valid_contexts(cls, value: list[str]) -> list[str]:
        if not value or not set(value) <= CONTEXTS:
            raise ValueError("Unbekannter Kontext")
        return value

    @field_validator("url_template")
    @classmethod
    def safe_template(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(ch.isspace() or ord(ch) < 32 for ch in value)):
            raise ValueError("Partner-Links müssen HTTPS ohne Zugangsdaten/Leerzeichen sein")
        unknown = {part for part in _placeholders(value)} - PLACEHOLDERS
        if unknown:
            raise ValueError(f"Unbekannte Platzhalter: {', '.join(sorted(unknown))}")
        return value

    @field_validator("label")
    @classmethod
    def safe_label(cls, value: str) -> str:
        if {part for part in _placeholders(value)} - {"city", "country"}:
            raise ValueError("Unbekannte Platzhalter im Linktext")
        return value

    @model_validator(mode="after")
    def valid_contract(self):
        if self.end is not None and self.start is not None and self.end < self.start:
            raise ValueError("Kampagnen-Ende liegt vor dem Start")
        if self.end is not None and self.start is None:
            raise ValueError("Enddatum ohne Startdatum ist nicht eindeutig")
        host = (urlsplit(self.url_template).hostname or "").lower()
        if host not in {h.lower() for h in self.hosts}:
            raise ValueError("Der Host der Vorlage muss in `hosts` stehen")
        if "{city}" not in self.url_template and "{slug}" not in self.url_template:
            raise ValueError("Die Vorlage muss den Ort über {city} oder {slug} enthalten")
        return self


def _placeholders(template: str) -> list[str]:
    out, rest = [], template
    while True:
        start = rest.find("{")
        if start < 0:
            return out
        end = rest.find("}", start)
        if end < 0:
            return out
        out.append(rest[start + 1:end])
        rest = rest[end + 1:]


class AffiliateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    enabled: bool = False
    disclosure: str = Field(default=DISCLOSURE_DEFAULT, max_length=400)
    partners: list[Partner] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({p.id for p in self.partners}) != len(self.partners):
            raise ValueError("Doppelte Partner-IDs")
        return self


def config_path() -> Path:
    return Path(os.environ.get("KLIMA_AFFILIATE_FILE", DEFAULT_PATH))


def load_config() -> AffiliateConfig | None:
    """Validierte Betreiber-Konfiguration oder ``None`` (dann kein Link)."""
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    try:
        return AffiliateConfig.model_validate(raw)
    except ValueError:
        return None


def affiliate_enabled() -> bool:
    config = load_config()
    return bool(config and config.enabled and config.partners)


def _render_url(template: str, city: str, country: str, lat: float, lon: float, slug: str) -> str:
    """Platzhalter einsetzen – jeder Wert URL-kodiert, keine freie Struktur."""
    return (template
            .replace("{city}", quote(city or "", safe=""))
            .replace("{country}", quote(country or "", safe=""))
            .replace("{slug}", quote(slug or "", safe=""))
            .replace("{lat}", f"{lat:.4f}")
            .replace("{lon}", f"{lon:.4f}"))


def select_affiliate(context: str, *, city: str, country: str, lat: float, lon: float,
                     slug: str = "", today: date | None = None) -> dict | None:
    """Ein passender Partner-Link für einen Ort/Kontext – oder ``None``.

    Auswahl: aktivierte Partner im Zeitfenster, Kontext und ggf. Land passend;
    danach deterministisch der erste Treffer in Konfigurationsreihenfolge (kein
    Zufall – derselbe Ort zeigt reproduzierbar denselben Link).
    """
    if context not in CONTEXTS:
        return None
    config = load_config()
    if not config or not config.enabled:
        return None
    today = today or datetime.now(timezone.utc).date()
    for partner in config.partners:
        if not partner.enabled or context not in partner.contexts:
            continue
        if partner.start is not None and today < partner.start:
            continue
        if partner.end is not None and today > partner.end:
            continue
        if partner.countries and country not in partner.countries:
            continue
        try:
            url = _render_url(partner.url_template, city, country, lat, lon, slug)
        except (KeyError, ValueError):
            continue
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(ch.isspace() or ord(ch) < 32 for ch in url)):
            continue
        if parsed.hostname.lower() not in {h.lower() for h in partner.hosts}:
            continue
        try:
            label = partner.label.format(city=city or "", country=country or "")
        except (KeyError, ValueError):
            return None
        return {
            "id": partner.id,
            "partner": partner.partner,
            "label": label,
            "note": partner.note,
            "url": url,
            "disclosure": config.disclosure,
        }
    return None
