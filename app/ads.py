"""First-party contextual sponsor slots; no cookies, user profiles or third-party JS.

Campaigns are operator-controlled JSON, not visitor input. Invalid configuration
fails closed. Publisher networks require a separate CMP/provider integration.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
import random
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTEXTS = {"year", "month", "wq"}
PLACEMENTS = {"sidebar"}
DEFAULT_PATH = Path(__file__).parent / "data" / "ad_campaigns.json"


class Campaign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    advertiser: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=400)
    cta: str = Field(default="Mehr erfahren", min_length=1, max_length=50)
    url: str = Field(max_length=2048)
    contexts: list[str] = Field(default_factory=lambda: sorted(CONTEXTS))
    placements: list[str] = Field(default_factory=lambda: sorted(PLACEMENTS))
    start: date
    end: date
    weight: int = Field(default=1, ge=1, le=1000)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_dates(self):
        if self.end < self.start:
            raise ValueError("Campaign end precedes start")
        return self

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(char.isspace() or ord(char) < 32 for char in value)):
            raise ValueError("Advertising links must use HTTPS without credentials/whitespace")
        return value

    @field_validator("contexts")
    @classmethod
    def valid_contexts(cls, value):
        if not value or not set(value) <= CONTEXTS:
            raise ValueError("Unsupported context")
        return value

    @field_validator("placements")
    @classmethod
    def valid_placements(cls, value):
        if not value or not set(value) <= PLACEMENTS:
            raise ValueError("Unsupported placement")
        return value


class CampaignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    enabled: bool = False
    campaigns: list[Campaign] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({c.id for c in self.campaigns}) != len(self.campaigns):
            raise ValueError("Duplicate campaign IDs")
        return self


def select_ad(context: str, placement: str, today: date | None = None) -> dict | None:
    if context not in CONTEXTS or placement not in PLACEMENTS:
        return None
    path = Path(os.environ.get("KLIMA_AD_CAMPAIGNS_FILE", DEFAULT_PATH))
    try:
        config = CampaignConfig.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None
    if not config.enabled:
        return None
    today = today or datetime.now(timezone.utc).date()
    eligible = [c for c in config.campaigns if c.enabled and c.start <= today <= c.end
                and context in c.contexts and placement in c.placements]
    if not eligible:
        return None
    selected = random.SystemRandom().choices(eligible, weights=[c.weight for c in eligible], k=1)[0]
    return selected.model_dump(include={"id", "advertiser", "title", "body", "cta", "url"})
