"""Helpers for validated, reproducible social-share links and preview cards."""
from __future__ import annotations

import base64
import binascii
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from .climate import MONTH_NAMES, Params

SHARE_VERSION = 1
MAX_SHARE_TOKEN_LENGTH = 4096
PARAM_LIMITS: dict[str, tuple[float, float]] = {
    "tmax_min": (-40, 45), "tmax_max": (-40, 45),
    "tmin_min": (-45, 40), "tmin_max": (-45, 40),
    "tdew_max": (-20, 30), "sun_min": (0, 16), "prec_max": (0, 99),
}
FONT_DIR = Path(__file__).resolve().parent / "static" / "vendor" / "dejavu"


def decode_share_state(token: str) -> dict[str, Any]:
    """Decode the compact URL payload and reject unexpected/oversized values."""
    if not token or len(token) > MAX_SHARE_TOKEN_LENGTH:
        raise ValueError("Der Teilen-Link ist ungültig oder zu lang.")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Der Teilen-Link enthält keine gültigen Klimadaten.") from exc
    if not isinstance(payload, dict) or payload.get("v") != SHARE_VERSION:
        raise ValueError("Diese Version des Teilen-Links wird nicht unterstützt.")

    mode = payload.get("mode", "year")
    if not isinstance(mode, str) or mode not in {"year", "month", "wq"}:
        raise ValueError("Der Modus im Teilen-Link ist ungültig.")
    period = payload.get("period", "all")
    if not isinstance(period, str) or len(period) > 32:
        raise ValueError("Der Zeitraum im Teilen-Link ist ungültig.")

    params_value = payload.get("params") or {}
    if not isinstance(params_value, dict):
        raise ValueError("Die Wetterfilter im Teilen-Link sind ungültig.")
    defaults = Params().query()
    params: dict[str, float] = {}
    for key, default in defaults.items():
        value = params_value.get(key, default)
        if isinstance(value, bool):
            raise ValueError("Ein Wetterfilter im Teilen-Link ist ungültig.")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Ein Wetterfilter im Teilen-Link ist ungültig.") from exc
        low, high = PARAM_LIMITS[key]
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError("Ein Wetterfilter im Teilen-Link liegt außerhalb des erlaubten Bereichs.")
        params[key] = number
    if params["tmax_min"] > params["tmax_max"]:
        params["tmax_min"], params["tmax_max"] = params["tmax_max"], params["tmax_min"]
    if params["tmin_min"] > params["tmin_max"]:
        params["tmin_min"], params["tmin_max"] = params["tmin_max"], params["tmin_min"]

    selected = payload.get("selected")
    if not isinstance(selected, dict):
        raise ValueError("Im Teilen-Link fehlt der ausgewählte Ort.")
    try:
        lat, lon = float(selected["lat"]), float(selected["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Die Koordinaten im Teilen-Link sind ungültig.") from exc
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Die Koordinaten im Teilen-Link liegen außerhalb des gültigen Bereichs.")

    name = selected.get("name") or ""
    country = selected.get("country") or ""
    if not isinstance(name, str) or not isinstance(country, str):
        raise ValueError("Der Ortsname im Teilen-Link ist ungültig.")
    selected = {"lat": lat, "lon": lon, "name": name[:100], "country": country[:100]}

    month = payload.get("month", 7)
    try:
        month = int(month)
    except (TypeError, ValueError) as exc:
        raise ValueError("Der Monat im Teilen-Link ist ungültig.") from exc
    if not 1 <= month <= 12:
        raise ValueError("Der Monat im Teilen-Link ist ungültig.")

    return {"v": SHARE_VERSION, "mode": mode, "period": period, "month": month,
            "params": params, "selected": selected}


def share_headline(report: Mapping[str, Any], state: Mapping[str, Any], location: str) -> tuple[str, str, int]:
    """Create Open Graph title/description and the exact displayed day count."""
    mode = state["mode"]
    month = int(state["month"])
    if mode == "month":
        days = int(round(report["days_per_month"][month - 1]))
        unit = f"im {MONTH_NAMES[month - 1]}"
    else:
        days = int(round(report["days_per_year"]))
        unit = "pro Jahr"
    title = f"{location}: {days:,}".replace(",", ".") + f" schöne Tage {unit}"
    period = report.get("period") or {}
    period_label = period.get("label") or "ERA5-Klimadaten"
    params = state["params"]
    description = (
        f"Finde dein Wohlfühlwetter: {days} schöne Tage {unit} in {location}. "
        f"Dein Filter: tagsüber {params['tmax_min']:g}–{params['tmax_max']:g} °C, "
        f"nachts {params['tmin_min']:g}–{params['tmin_max']:g} °C. "
        f"Auswertung: {period_label}."
    )
    return title, description[:300], days


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bundled = FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    if bundled.is_file():
        try:
            return ImageFont.truetype(str(bundled), size=size)
        except OSError:
            pass
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow versions with scalable built-in font
    except TypeError:
        return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def render_share_card(report: Mapping[str, Any], state: Mapping[str, Any], location: str) -> bytes:
    """Render a 1200 × 630 branded preview with the monthly result profile."""
    width, height = 1200, 630
    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)

    # A restrained blue-to-green background; no external images or requests.
    left, right = (11, 18, 32), (19, 50, 65)
    for x in range(width):
        ratio = x / (width - 1)
        color = tuple(round(left[i] + (right[i] - left[i]) * ratio) for i in range(3))
        draw.line((x, 0, x, height), fill=color)
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((760, -250, 1370, 360), fill=(255, 180, 84, 22))
    glow_draw.ellipse((730, 260, 1260, 790), fill=(63, 201, 172, 24))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)

    regular = _font(22)
    small = _font(17)
    medium = _font(23, True)
    title_font = _font(45, True)
    number_font = _font(136, True)

    # Sun mark and product name.
    draw.ellipse((70, 58, 108, 96), fill=(255, 180, 84))
    for angle in range(0, 360, 45):
        from math import cos, radians, sin
        a = radians(angle)
        x1, y1 = 89 + cos(a) * 25, 77 + sin(a) * 25
        x2, y2 = 89 + cos(a) * 33, 77 + sin(a) * 33
        draw.line((x1, y1, x2, y2), fill=(255, 180, 84), width=3)
    draw.text((128, 61), "MEIN PERFEKTES KLIMA", font=medium, fill=(238, 243, 249))
    draw.text((128, 92), "Dein Wohlfühlwetter in Europa", font=small, fill=(152, 169, 190))

    country = (state.get("selected") or {}).get("country") or ""
    place = location if not country or country in location else f"{location}, {country}"
    if _text_width(draw, place, title_font) > 545:
        title_font = _font(38, True)
    if _text_width(draw, place, title_font) > 545:
        title_font = _font(32, True)
    draw.text((72, 166), place[:70], font=title_font, fill=(238, 243, 249))

    mode = state["mode"]
    month = int(state["month"])
    if mode == "month":
        days = int(round(report["days_per_month"][month - 1]))
        unit = f"schöne Tage im {MONTH_NAMES[month - 1]}"
        highlighted_month = month - 1
    else:
        days = int(round(report["days_per_year"]))
        unit = "schöne Tage pro Jahr"
        highlighted_month = -1
    number = f"{days:,}".replace(",", ".")
    while _text_width(draw, number, number_font) > 520 and number_font.size > 90:
        number_font = _font(number_font.size - 8, True)
    draw.text((68, 214), number, font=number_font, fill=(255, 190, 105), stroke_width=1)
    draw.text((80, 365), unit, font=medium, fill=(238, 243, 249))
    period = report.get("period") or {}
    period_label = period.get("label") or "Klimadaten"
    draw.text((80, 406), f"bei deinem Wohlfühlwetter · {period_label}"[:56], font=small,
              fill=(152, 169, 190))

    params = state["params"]
    conditions = [
        f"Tagsüber {params['tmax_min']:g}–{params['tmax_max']:g} °C",
        f"Nachts {params['tmin_min']:g}–{params['tmin_max']:g} °C",
        f"Sonne ab {params['sun_min']:g} h/Tag",
    ]
    draw.rounded_rectangle((72, 472, 620, 524), radius=16, fill=(24, 35, 58))
    draw.text((92, 486), "   ·   ".join(conditions)[:70], font=small, fill=(201, 213, 227))
    draw.rounded_rectangle((72, 560, 625, 562), radius=1, fill=(255, 180, 84))

    # A compact monthly bar chart makes the result legible in social previews.
    chart_box = (670, 116, 1135, 524)
    draw.rounded_rectangle(chart_box, radius=28, fill=(18, 27, 45), outline=(57, 77, 102), width=2)
    draw.text((710, 147), "Schöne Tage je Monat", font=medium, fill=(238, 243, 249))
    draw.text((710, 181), "Tagesmittel über den gewählten Zeitraum", font=small,
              fill=(152, 169, 190))
    values = list(report.get("days_per_month") or [0] * 12)
    maximum = max(1.0, max((float(v) for v in values), default=0))
    chart_left, chart_right, chart_top, chart_bottom = 710, 1093, 235, 422
    gap = 8
    bar_width = (chart_right - chart_left - gap * 11) / 12
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=(65, 83, 105), width=2)
    labels = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
    for i, raw_value in enumerate(values[:12]):
        value = max(0.0, float(raw_value))
        bar_height = max(5, (value / maximum) * (chart_bottom - chart_top))
        x0 = chart_left + i * (bar_width + gap)
        x1 = x0 + bar_width
        y0 = chart_bottom - bar_height
        color = (255, 180, 84) if i == highlighted_month else (68, 166, 164)
        draw.rounded_rectangle((round(x0), round(y0), round(x1), chart_bottom - 1),
                               radius=6, fill=color)
        draw.text((x0 + bar_width / 2, 438), labels[i], font=small, fill=(152, 169, 190), anchor="mt")
    draw.text((710, 479), "Quelle: ERA5 · Copernicus / ECMWF", font=small, fill=(152, 169, 190))
    draw.text((710, 550), "mein perfektes klima", font=small, fill=(201, 213, 227))

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
