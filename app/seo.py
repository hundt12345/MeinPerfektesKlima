"""Indexierbare Inhaltsseiten: Ortsprofile, Ratgeber, Sitemap, Feed, Kennzahl-Badge.

Die Startseite ist eine Karte – für Suchmaschinen und Social Media praktisch
unsichtbar. Dieses Modul erzeugt deshalb **serverseitig gerenderte**, dauerhaft
verlinkbare Seiten aus denselben geprüften ERA5-Daten:

* ``/klima``            Verzeichnis aller Ortsprofile
* ``/klima/{ort}``     Ortsprofil mit echten Zahlen, Monatsprofil, Vergleich,
                       Zukunftswert, FAQ, Teilen-Links und optionalem Partner-Link
* ``/ueberwintern-in-europa``  Ratgeber „milder Winter“ (aktualisiert sich mit den Daten)
* ``/sommerhitze-entfliehen``  Ratgeber „milder Sommer“
* ``/sitemap.xml``, ``/robots.txt``, ``/feed.xml``, ``/badge/{ort}.svg``

Bewusste Grenzen: Es entstehen nur Seiten für Orte, die im Klimaraster liegen
und entweder mindestens ``KLIMA_LANDING_MIN_POP`` Einwohner haben (Standard
100.000) oder in der kuratierten Winterliste stehen. Alle Texte entstehen aus
Zahlen, die dieselbe Pipeline wie die Karte berechnet; es gibt keine erfundenen
Sätze, keine austauschbaren Textbausteine ohne Datenbezug und keine
Ortsseiten für Orte außerhalb des Rasters.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .affiliate import select_affiliate
from .climate import MONTH_NAMES, ClimateData, Params, months_label
from .legal import legal_configured
from .names import fold
from .sharing import encode_share_state

SITE_NAME = "Mein perfektes Klima"
TAGLINE = "Dein Wohlfühlwetter in Europa"
SITE_DESCRIPTION = (
    "Interaktive Europakarte für Wohlfühlwetter: Wie viele Tage im Jahr passen "
    "Temperatur, Schwüle, Sonne und Regen zu deinen Einstellungen? Mit "
    "Winterquartier-Suche und Klimablick auf 2030 und 2050."
)
GRID_NOTE = "ERA5-Reanalyse (Copernicus/ECMWF), Raster 0,75° (rund 50–80 km)"

# ------------------------------------------------------------------ Wetterprofile
# „Ausgewogen mild“ entspricht der Voreinstellung der Karte (PRESETS „mild“).
PROFILE_MILD = Params(tmax_min=18, tmax_max=28, tmin_min=8, tmin_max=20,
                      tdew_max=17, sun_min=3, prec_max=99)
PROFILE_MILD_NAME = "ausgewogen mild"
PROFILE_MILD_TEXT = ("tagsüber 18–28 °C, nachts nicht kälter als 8 °C, "
                     "Taupunkt höchstens 17 °C, mindestens 3 h Sonne")
# Winterquartier: das klassische „Winter im Warmen“.
PROFILE_WINTER = Params(tmax_min=10, tmax_max=20, tmin_min=3, tmin_max=15,
                        tdew_max=16, sun_min=3, prec_max=99)
PROFILE_WINTER_NAME = "milder Winter"
PROFILE_WINTER_TEXT = ("tagsüber 10–20 °C, nachts über 3 °C, "
                       "Taupunkt höchstens 16 °C, mindestens 3 h Sonne")
PROFILE_WINTER_WARM = Params(tmax_min=16, tmax_max=26, tmin_min=10, tmin_max=20,
                             tdew_max=17, sun_min=5, prec_max=99)
PROFILE_WINTER_WARM_NAME = "warmer Winter"
# Sommer: „nicht schwitzen“ – warm, aber trocken, und die milde Variante für
# alle, die der Hitze ausweichen wollen.
PROFILE_SUMMER = Params(tmax_min=24, tmax_max=33, tmin_min=14, tmin_max=24,
                        tdew_max=15, sun_min=5, prec_max=99)
PROFILE_SUMMER_NAME = "warm & schweißfrei"
PROFILE_SUMMER_MILD = Params(tmax_min=12, tmax_max=28, tmin_min=5, tmin_max=19,
                             tdew_max=15, sun_min=4, prec_max=99)
PROFILE_SUMMER_MILD_NAME = "milder Sommer"

WINTER_MONTHS = (9, 10, 11, 0, 1, 2, 3)          # Oktober – April
SUMMER_MONTHS = (6, 7)                            # Juli – August
SUMMER_DRY_MONTHS = (5, 6, 7)                     # Juni – August

# Zukunftszeitraum für den Ortsvergleich (mittlerer Weg, SSP2-4.5).
FUTURE_PERIOD = "2050"
FUTURE_LABEL = "2050 · Mittlerer Weg"

MIN_POP = max(0, int(os.environ.get("KLIMA_LANDING_MIN_POP", "100000") or 0))
REFERRER_POLICY = os.environ.get("KLIMA_REFERRER_POLICY", "no-referrer").strip() or "no-referrer"
_REFERRER_VALUES = {"no-referrer", "strict-origin-when-cross-origin", "same-origin",
                    "origin", "unsafe-url", "no-referrer-when-downgrade"}

# Ländercodes für eindeutige Orts-Slugs (nur für die Liste der Städte ≥ 50.000).
COUNTRY_CODES = {
    "Albania": "al", "Armenia": "am", "Austria": "at", "Belarus": "by", "Belgium": "be",
    "Bosnia and Herzegovina": "ba", "Bulgaria": "bg", "Croatia": "hr", "Cyprus": "cy",
    "Czechia": "cz", "Denmark": "dk", "Estonia": "ee", "Finland": "fi", "France": "fr",
    "Georgia": "ge", "Germany": "de", "Greece": "gr", "Hungary": "hu", "Iceland": "is",
    "Ireland": "ie", "Italy": "it", "Kosovo": "xk", "Latvia": "lv", "Lithuania": "lt",
    "Moldova": "md", "Montenegro": "me", "Netherlands": "nl", "North Macedonia": "mk",
    "Norway": "no", "Poland": "pl", "Portugal": "pt", "Romania": "ro", "Russia": "ru",
    "Serbia": "rs", "Slovakia": "sk", "Slovenia": "si", "Spain": "es", "Sweden": "se",
    "Switzerland": "ch", "Turkey": "tr", "Ukraine": "ua", "United Kingdom": "gb",
}

# Kuratierte Zusatzorte: klein, aber für Überwinterung, Sonne und Küste gesucht.
CURATED_CITIES = (
    # Kleine, aber gesuchte Winter- und Küstenorte unterhalb der Einwohnergrenze.
    # Auswahl nach Nachfrage (Überwintern, Sonne, Küste), keine Wertung.
    "Faro", "Portimão", "Cascais", "Sesimbra", "Estepona", "Marbella", "Fuengirola",
    "Torremolinos", "Benidorm", "Roquetas de Mar", "Syrakus", "Chania", "Rethymno",
    "Paphos", "Larnaca", "Kyrenia", "Bodrum", "Marmaris", "Fethiye", "Alanya",
    "Zadar", "Pula", "Ajaccio", "Bastia", "Vlorë", "Ohrid", "Gibraltar",
)

# Referenzorte für die Ratgeber (möglichst bekannte Vergleichsstädte).
REFERENCE_CITIES = ("Berlin", "München", "Hamburg", "Köln", "Wien", "Zürich")


# ---------------------------------------------------------------------- Hilfen
def de(value: float, digits: int = 0) -> str:
    """Zahl mit deutschem Dezimaltrennzeichen und Tausenderpunkt."""
    text = f"{float(value):,.{digits}f}"
    return text.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def slugify(name: str) -> str:
    base = fold(name)
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def _origin(values: Mapping[str, Any], key: str = "origin") -> str:
    return str(values.get(key) or "").rstrip("/")


def _abs(origin: str, path: str) -> str:
    return f"{origin}{path}" if origin else path


def _affiliate_for(context: str, *, name: str, country: str, lat: float, lon: float,
                   slug: str) -> dict | None:
    """Partner-Link für Inhaltsseiten – nur wenn vertraglich konfiguriert."""
    return select_affiliate(context, city=name, country=country, lat=lat, lon=lon, slug=slug)


# ------------------------------------------------------------------ Datenbestand
@dataclass(frozen=True)
class Entry:
    slug: str
    name: str
    country: str          # deutscher Ländername
    country_en: str
    lat: float
    lon: float
    pop: int
    point: int
    city_index: int = -1

    @property
    def label(self) -> str:
        return f"{self.name} ({self.country})" if self.country else self.name


@dataclass
class DatasetView:
    signature: str
    cd: ClimateData
    entries: list[Entry]
    by_slug: dict[str, Entry]
    ranking: dict[str, int]      # slug -> Platz unter den Ortsprofilen (1-basiert, Profil „mild“)
    _counts: dict[str, np.ndarray]

    def counts(self, key: str) -> np.ndarray:
        """Tageszahlen **je Stadt** (höhenkorrigiert), indiziert wie ``cd.cities``."""
        if key in self._counts:
            return self._counts[key]
        cd = self.cd
        array = {
            "mild": lambda: cd.city_counts(PROFILE_MILD, None, "all"),
            "future": lambda: cd.city_counts(PROFILE_MILD, None, FUTURE_PERIOD),
            "winter": lambda: cd.city_counts(PROFILE_WINTER, WINTER_MONTHS, "all"),
            "winter_warm": lambda: cd.city_counts(PROFILE_WINTER_WARM, WINTER_MONTHS, "all"),
            "summer": lambda: cd.city_counts(PROFILE_SUMMER, SUMMER_DRY_MONTHS, "all"),
            "summer_mild": lambda: cd.city_counts(PROFILE_SUMMER_MILD, SUMMER_MONTHS, "all"),
        }[key]()
        self._counts[key] = array
        return array

    def days(self, entry: Entry, key: str) -> float:
        return round(float(self.counts(key)[entry.city_index]), 1)

    def by_name(self, name: str) -> Entry | None:
        wanted = fold(name)
        for entry in self.entries:
            if fold(entry.name) == wanted:
                return entry
        return None

    def neighbours(self, entry: Entry, limit: int = 5) -> list[Entry]:
        ranked = sorted(self.entries, key=lambda other: _distance_km(entry, other))
        return [other for other in ranked if other.slug != entry.slug][:limit]


_VIEWS: dict[str, DatasetView] = {}


def view_for(cd: ClimateData) -> DatasetView:
    """Ortsprofile je Datensatz – einmal gebaut, bei Datenwechsel neu."""
    signature = "|".join(str(cd.meta.get(key, "")) for key in
                         ("generated_at", "start_date", "end_date", "n_points"))
    cached = _VIEWS.get(signature)
    if cached is not None and cached.cd is cd:
        return cached
    entries = _build_entries(cd)
    by_slug = {entry.slug: entry for entry in entries}
    mild = cd.city_counts(PROFILE_MILD, None, "all")
    order = sorted(entries, key=lambda entry: -float(mild[entry.city_index]))
    ranking = {entry.slug: index + 1 for index, entry in enumerate(order)}
    view = DatasetView(signature=signature, cd=cd, entries=entries, by_slug=by_slug,
                       ranking=ranking, _counts={"mild": mild})
    _VIEWS[signature] = view
    return view


# Zielländer für die Sommer-Liste: EU/EWR, Schweiz, Vereinigtes Königreich,
# EU-Beitrittskandidaten und die Türkei. Russland und Belarus bleiben außen vor
# (Einreise-/Flugrealität für die Zielgruppe), der Kaukasus ebenso. Das ist eine
# redaktionelle Einschränkung, keine Aussage über das Klima dort – sie steht als
# Hinweis unter der Tabelle.
REACHABLE_COUNTRIES = (
    "Albania", "Austria", "Belgium", "Bosnia and Herzegovina", "Bulgaria", "Croatia",
    "Cyprus", "Czechia", "Denmark", "Estonia", "Finland", "France", "Germany", "Greece",
    "Hungary", "Iceland", "Ireland", "Italy", "Kosovo", "Latvia", "Lithuania", "Luxembourg",
    "Malta", "Moldova", "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland",
    "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland",
    "Turkey", "Ukraine", "United Kingdom",
)


def ranked_places(view: "DatasetView", counts_key: str, limit: int, *,
                  distinct: bool = True,
                  countries: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Ortsrangliste zu einem Profil – optional mit eindeutigen Tagessprüngen.

    Ein Rasterfeld von 0,75° umfasst viele Orte mit **identischen** Werten. In
    Listen und Grafiken wirkt das wie ein Fehler („viermal 152,9“). Mit
    ``distinct`` steht je Tagessprung nur der größte Ort; die Seite nennt den
    Grund im Kleingedruckten.
    """
    counts = view.counts(counts_key)
    allowed = set(countries)
    pool = [item for item in view.entries if not allowed or item.country_en in allowed]
    ordered = sorted(pool, key=lambda item: (-float(counts[item.city_index]), -item.pop))
    out: list[dict[str, Any]] = []
    seen: set[float] = set()
    for entry in ordered:
        value = round(float(counts[entry.city_index]), 1)
        if distinct and value in seen:
            continue
        seen.add(value)
        out.append({"name": entry.name, "country": entry.country, "days": value,
                    "lat": entry.lat, "lon": entry.lon, "pop": entry.pop, "slug": entry.slug})
        if len(out) >= max(1, limit):
            break
    return out


def _distance_km(a: Entry, b: Entry) -> float:
    return 111.0 * math.hypot(a.lat - b.lat, (a.lon - b.lon) * math.cos(math.radians(a.lat)))


def _build_entries(cd: ClimateData) -> list[Entry]:
    bbox = cd.bbox or {}
    city_index_by_id = {id(city): index for index, city in enumerate(cd.cities)}
    inside = [
        city for city in cd.cities
        if bbox.get("lat_min", -90) <= city["lat"] <= bbox.get("lat_max", 90)
        and bbox.get("lon_min", -180) <= city["lon"] <= bbox.get("lon_max", 180)
    ]
    inside.sort(key=lambda city: -int(city.get("pop", 0)))
    selected: list[dict] = [city for city in inside if int(city.get("pop", 0)) >= MIN_POP]
    chosen = {fold(city["name"]) for city in selected}
    by_name: dict[str, dict] = {}
    for city in inside:
        by_name.setdefault(fold(city["name"]), city)
    for name in CURATED_CITIES:
        city = by_name.get(fold(name))
        if city is not None and fold(city["name"]) not in chosen:
            selected.append(city)
            chosen.add(fold(city["name"]))

    # Eindeutige, stabile Slugs: Basisname, bei Kollision Ländercode, dann Ort.
    used: dict[str, int] = {}
    entries: list[Entry] = []
    taken: set[str] = set()
    for city in sorted(selected, key=lambda item: (-int(item.get("pop", 0)), fold(item["name"]))):
        base = slugify(city["name"]) or "ort"
        slug = base
        if slug in taken:
            code = COUNTRY_CODES.get(city.get("country") or "", slugify(city.get("country") or "xx"))
            slug = f"{base}-{code}"
        if slug in taken:
            used[base] = used.get(base, 1) + 1
            slug = f"{base}-{used[base]}"
        taken.add(slug)
        entries.append(Entry(
            slug=slug, name=city["name"], country=city.get("name_de") or city.get("country") or "",
            country_en=city.get("country") or "", lat=float(city["lat"]), lon=float(city["lon"]),
            pop=int(city.get("pop", 0)), point=cd.point_index(float(city["lat"]), float(city["lon"])),
            city_index=city_index_by_id.get(id(city), -1),
        ))
    return sorted(entries, key=lambda entry: fold(entry.name))


# --------------------------------------------------------------- HTML-Bausteine
def _nav(origin: str) -> str:
    return (
        '<nav class="site-nav">'
        f'<a href="{_abs(origin, "/")}">Karte</a>'
        f'<a href="{_abs(origin, "/klima")}">Orte</a>'
        f'<a href="{_abs(origin, "/ueberwintern-in-europa")}">Überwintern</a>'
        f'<a href="{_abs(origin, "/sommerhitze-entfliehen")}">Hitze entfliehen</a>'
        '</nav>'
    )


def _footer(origin: str, *, extra: str = "") -> str:
    legal = ""
    if legal_configured():
        legal = (f'<a href="{_abs(origin, "/impressum")}">Impressum</a> · '
                 f'<a href="{_abs(origin, "/datenschutz")}">Datenschutz</a> · ')
    return (
        '<footer class="site-foot">'
        f'<p>{extra}</p>'
        f'<p>{legal}<a href="{_abs(origin, "/klima")}">Ortsverzeichnis</a></p>'
        '<p class="fine">Klimadaten: enthält modifizierte Copernicus-Climate-Change-Service-'
        'Informationen (ERA5 / ECMWF), gelesen aus der offenen ARCO-ERA5-Kopie. '
        'Grundkarte: © OpenStreetMap-Mitwirkende. Projektionen: CMIP6 / IPCC AR6 Atlas '
        '(CC BY 4.0), regional auf ERA5 übertragen.</p>'
        '<p class="fine">Die Auswertung beruht auf einem 0,75°-Raster (rund 50–80 km) und ist '
        'kein Stationsmesswert. 2030/2050 sind modellbasierte Näherungen, keine Messwerte. '
        'Die Seiten ersetzen keine Miet-, Visum- oder Reiseberatung.</p>'
        '</footer>'
    )


_COPY_SCRIPT = """<script>
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;
  var text = button.getAttribute("data-copy");
  var done = function () { button.textContent = button.getAttribute("data-done") || "Kopiert"; };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, done);
  } else {
    var area = document.createElement("textarea");
    area.value = text; document.body.appendChild(area); area.select();
    try { document.execCommand("copy"); } catch (err) {}
    area.remove(); done();
  }
  navigator.sendBeacon && navigator.sendBeacon("/api/analytics/event?name=" +
    encodeURIComponent(button.getAttribute("data-event") || "share_copy"));
});
</script>"""


def layout(*, title: str, description: str, canonical: str, origin: str, body: str,
           image: str | None = None, structured: Sequence[Mapping[str, Any]] = (),
           extra_head: str = "", og_type: str = "website") -> str:
    """Ein schlankes, vollständig serverseitiges HTML-Dokument (kein JS nötig)."""
    policy = REFERRER_POLICY if REFERRER_POLICY in _REFERRER_VALUES else "no-referrer"
    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(title)}</title>",
        f'<meta name="description" content="{e(description)}">',
        f'<link rel="canonical" href="{e(canonical)}">',
        '<meta name="robots" content="index, follow, max-image-preview:large">',
        f'<meta name="referrer" content="{policy}">',
        '<meta name="theme-color" content="#0b1220">',
        f'<meta property="og:type" content="{e(og_type)}">',
        f'<meta property="og:site_name" content="{e(SITE_NAME)}">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(description)}">',
        f'<meta property="og:url" content="{e(canonical)}">',
        f'<meta property="og:locale" content="de_DE">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(title)}">',
        f'<meta name="twitter:description" content="{e(description)}">',
        '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' '
        "viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='7' fill='%23ffb454'/%3E%3C/svg%3E\">",
        '<link rel="preload" as="font" type="font/woff2" crossorigin '
        'href="/static/vendor/inter/inter-latin-wght-normal.woff2">',
        '<link rel="stylesheet" href="/static/content.css">',
        f'<link rel="alternate" type="application/rss+xml" title="{e(SITE_NAME)} – Aktualisierungen" '
        'href="/feed.xml">',
    ]
    if image:
        head += [
            f'<meta property="og:image" content="{e(image)}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            f'<meta name="twitter:image" content="{e(image)}">',
        ]
    for block in structured:
        head.append('<script type="application/ld+json">'
                    + json.dumps(block, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    if extra_head:
        head.append(extra_head)
    return ("<!doctype html>\n<html lang=\"de\">\n<head>\n" + "\n".join(head)
            + "\n</head>\n<body>\n"
            + f'<header class="site-head"><a class="brand" href="{_abs(origin, "/")}">'
              f'<span class="dot" aria-hidden="true"></span>{e(SITE_NAME)}</a>{_nav(origin)}</header>\n'
              f"<main>\n{body}\n</main>\n{_footer(origin)}\n{_COPY_SCRIPT}\n</body>\n</html>\n")


def _bars(values: Sequence[float], limit: float, *, highlight: Sequence[int] = ()) -> str:
    rows = []
    for index, value in enumerate(values):
        width = 0.0 if limit <= 0 else max(0.0, min(100.0, float(value) / limit * 100.0))
        css = " class=\"highlight\"" if index in highlight else ""
        rows.append(
            f'<div class="bar-row"><span class="bar-name">{MONTH_NAMES[index][:3]}</span>'
            f'<span class="bar-track"><span class="bar-fill"{css} style="width:{width:.1f}%"></span></span>'
            f'<span class="bar-value">{de(value, 1)}</span></div>'
        )
    return '<div class="bars">' + "".join(rows) + "</div>"


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]], *, caption: str = "",
           klass: str = "") -> str:
    head = "".join(f'<th scope="col">{e(cell)}</th>' for cell in headers)
    body = "".join("<tr>" + "".join(
        f'<th scope="row">{cell}</th>' if position == 0 else f"<td>{cell}</td>"
        for position, cell in enumerate(row)) + "</tr>" for row in rows)
    caption_html = f"<caption>{e(caption)}</caption>" if caption else ""
    return (f'<div class="table-wrap"><table class="{e(klass)}">{caption_html}'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


def _shares(origin: str, url: str, text: str, *, event: str = "share_target") -> str:
    """Netzwerk-Links als normale Links – kein Fremd-JavaScript, kein Pixel."""
    encoded = html.escape(url, quote=True)
    quoted = html.escape(text, quote=True)
    targets = (
        ("WhatsApp", f"https://wa.me/?text={quoted}%20{encoded}"),
        ("Telegram", f"https://t.me/share/url?url={encoded}&text={quoted}"),
        ("X", f"https://twitter.com/intent/tweet?text={quoted}&url={encoded}"),
        ("Facebook", f"https://www.facebook.com/sharer/sharer.php?u={encoded}"),
        ("Bluesky", f"https://bsky.app/intent/compose?text={quoted}%20{encoded}"),
        ("Reddit", f"https://www.reddit.com/submit?url={encoded}&title={quoted}"),
        ("Pinterest", "https://www.pinterest.com/pin/create/button/?url=" + encoded),
        ("E-Mail", f"mailto:?subject={quoted}&body={encoded}"),
    )
    links = "".join(
        f'<a class="chip" href="{href}" target="_blank" rel="noopener noreferrer" '
        f'data-event="{e(event)}">{e(label)}</a>' for label, href in targets)
    return (f'<div class="share-row">{links}'
            f'<button type="button" class="chip" data-copy="{encoded}" '
            f'data-done="Link kopiert" data-event="share_copy">Link kopieren</button></div>')


def _faq(items: Sequence[tuple[str, str]], *, heading: str = "Häufige Fragen") -> str:
    blocks = "".join(
        f'<details open><summary>{e(question)}</summary><p>{answer}</p></details>'
        for question, answer in items)
    return f'<section class="card"><h2>{e(heading)}</h2>{blocks}</section>'


def _faq_jsonld(items: Sequence[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": _plain(question),
             "acceptedAnswer": {"@type": "Answer", "text": _plain(answer)}}
            for question, answer in items
        ],
    }


def _plain(text: str) -> str:
    """HTML für strukturierte Daten auf Klartext reduzieren."""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _breadcrumbs(origin: str, trail: Sequence[tuple[str, str]]) -> str:
    items = "".join(f'<li><a href="{e(_abs(origin, path))}">{e(label)}</a></li>'
                    for label, path in trail)
    return f'<nav class="breadcrumbs" aria-label="Brotkrumen"><ol>{items}</ol></nav>'


def _breadcrumbs_jsonld(origin: str, trail: Sequence[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": index + 1, "name": label,
             "item": _abs(origin, path)}
            for index, (label, path) in enumerate(trail)
        ],
    }


def _affiliate_box(link: Mapping[str, Any] | None, *, heading: str) -> str:
    if not link:
        return ""
    return (
        '<section class="card partner"><h2>' + e(heading) + '</h2>'
        '<p class="fine">Anzeige · ' + e(link["partner"]) + '</p>'
        f'<p><a class="cta" href="{e(link["url"])}" target="_blank" '
        'rel="sponsored nofollow noopener noreferrer" data-event="affiliate_click">'
        + e(link["label"]) + '</a></p>'
        + (f'<p class="fine">{e(link["note"])}</p>' if link.get("note") else "")
        + f'<p class="fine">{e(link["disclosure"])}</p></section>'
    )


# ------------------------------------------------------------------- Ortsprofile
def _period_years(period: Mapping[str, Any]) -> str:
    """„2016–2025“ aus den realen Datumsgrenzen – nie aus einer Beschriftung raten."""
    start = str(period.get("start_date") or "")[:4]
    end = str(period.get("end_date") or "")[:4]
    if start and end and start != end:
        return f"{start}–{end}"
    return str(period.get("label") or "2016–2025")


def _month_limits(cd: ClimateData, period: str = "all") -> list[float]:
    return [cd.month_limit(month, period) for month in range(12)]


def city_context(view: DatasetView, entry: Entry) -> dict[str, Any]:
    """Alle Zahlen eines Ortsprofils – eine Quelle für HTML, FAQ und JSON-LD."""
    cd = view.cd
    report = cd.point_report(entry.lat, entry.lon, PROFILE_MILD, None, "all")
    clim = report["climate"] or {}
    month_days = [float(value) for value in report["days_per_month"]]
    limits = _month_limits(cd)
    best = sorted(range(12), key=lambda month: -month_days[month])[:3]
    future_days = None
    future_info = None
    try:
        future = cd.point_report(entry.lat, entry.lon, PROFILE_MILD, None, FUTURE_PERIOD)
        future_days = float(future["days_per_year"])
        future_info = future.get("period") or None
    except Exception:  # Projektionen fehlen/falsch → Abschnitt bleibt weg.
        future_days, future_info = None, None
    return {
        "entry": entry,
        "report": report,
        "climate": clim,
        "month_days": month_days,
        "month_limits": limits,
        "best_months": best,
        "days": float(report["days_per_year"]),
        "percentile": float(report["percentile"]),
        "rank": view.ranking.get(entry.slug),
        "total": len(view.entries),
        "future_days": future_days,
        "future_info": future_info,
        "neighbours": view.neighbours(entry, 5),
    }


def city_context_by_slug(view: DatasetView, slug: str) -> tuple[Entry | None, dict | None]:
    """Ort und Kennzahlen zu einem Slug – ohne HTML zu rendern (z. B. für Grafiken)."""
    entry = view.by_slug.get(slug)
    if entry is None:
        return None, None
    return entry, city_context(view, entry)


def city_page(view: DatasetView, origin: str, slug: str) -> tuple[str, dict] | None:
    """HTML und Kontext eines Ortsprofils (``None`` = kein solcher Ort)."""
    entry, context = city_context_by_slug(view, slug)
    if entry is None or context is None:
        return None
    return render_city(view, origin, context), context


def render_city(view: DatasetView, origin: str, context: Mapping[str, Any]) -> str:
    cd = view.cd
    entry: Entry = context["entry"]
    report = context["report"]
    climate = context["climate"]
    days = context["days"]
    yearly = climate.get("year") or {}
    best = [MONTH_NAMES[month] for month in context["best_months"]]
    best_values = [context["month_days"][month] for month in context["best_months"]]
    period = report.get("period") or {}
    data_years = _period_years(period)
    app_link = _app_link(entry, origin)
    card = _abs(origin, f"/klima/{entry.slug}/card.png")
    canonical = _abs(origin, f"/klima/{entry.slug}")

    lead = (
        f"<p><strong>{e(entry.name)}</strong> ({e(entry.country)}) erreicht bei diesem Wohlfühlwetter "
        f"im Mittel <strong>{de(days)} Tage pro Jahr</strong>. Das ist mehr als an "
        f"{de(context['percentile'])} % der {de(len(cd.cities))} verglichenen Städte in Europa"
        + (f" und Platz {de(context['rank'])} von {de(context['total'])} Ortsprofilen."
           if context.get("rank") else ".")
        + f" Grundlage sind die täglichen ERA5-Werte der Jahre {e(data_years)}, ausgewertet auf "
          f"einem 0,75°-Raster (rund 50–80 km); die Temperaturen sind auf die Höhe des "
          f"Ortes umgerechnet.</p>"
    )

    criteria = (
        '<section class="card"><h2>Deine Kriterien</h2>'
        f'<p>Profil „{e(PROFILE_MILD_NAME)}“: {e(PROFILE_MILD_TEXT)}. Ein Tag zählt nur, '
        'wenn <em>alle</em> Kriterien zugleich erfüllt sind.</p>'
        f'<p><a class="cta" href="{e(app_link)}" data-event="app_open">Eigene Werte in der '
        'Karte ausprobieren</a></p></section>'
    )

    best_sentence = ""
    if best_values and best_values[0] > 0:
        best_sentence = (f"<p>Am häufigsten passt das Wetter im {e(best[0])} "
                         f"({de(best_values[0], 1)} Tage), danach im {e(best[1])} "
                         f"({de(best_values[1], 1)}) und im {e(best[2])} ({de(best_values[2], 1)}). "
                         f"Im schwächsten Monat ({e(MONTH_NAMES[min(range(12), key=lambda m: context['month_days'][m])])}) "
                         f"sind es {de(min(context['month_days']), 1)} Tage.</p>")
    else:
        best_sentence = ("<p>In keinem Monat erreicht dieser Ort das Profil – die Karte zeigt, "
                         "welche Orte in Europa näher herankommen.</p>")

    bars = (
        '<section class="card"><h2>Schöne Tage je Monat</h2>'
        + _bars(context["month_days"], max(context["month_limits"]))
        + best_sentence
        + '<p class="fine">Balkenlänge bezogen auf die Tage des jeweiligen Monats; '
          'die Werte sind Monatsmittel über ' + e(data_years) + '.</p></section>'
    )

    monthly = climate.get("months") or {}
    rows = []
    for index, month in enumerate(MONTH_NAMES):
        rows.append([
            e(month),
            f"{de(monthly.get('tmax', [0] * 12)[index], 1)} °C",
            f"{de(monthly.get('tmin', [0] * 12)[index], 1)} °C",
            f"{de(monthly.get('sun', [0] * 12)[index], 1)} h",
            f"{de(monthly.get('prec', [0] * 12)[index], 1)} mm",
        ])
    rows.append([
        "<strong>Jahr</strong>",
        f"<strong>{de(yearly.get('tmax', 0), 1)} °C</strong>",
        f"<strong>{de(yearly.get('tmin', 0), 1)} °C</strong>",
        f"<strong>{de(yearly.get('sun', 0), 1)} h</strong>",
        f"<strong>{de(yearly.get('prec', 0), 0)} mm</strong>",
    ])
    profile = (
        '<section class="card"><h2>Klimaprofil</h2>'
        + _table(("Monat", "Ø Höchstwert", "Ø Tiefstwert", "Sonne", "Niederschlag"), rows)
        + '<ul class="facts">'
        + f"<li>Sommertage (≥ 25 °C): <strong>{de(climate.get('summer_days', 0), 1)}</strong></li>"
        + f"<li>Hitzetage (&gt; 30 °C): <strong>{de(climate.get('hot_days', 0), 1)}</strong></li>"
        + f"<li>Schwüle Tage (Taupunkt &gt; 15 °C): <strong>{de(climate.get('sultry_days', 0), 1)}</strong></li>"
        + f"<li>Frosttage (Tiefstwert ≤ 0 °C): <strong>{de(climate.get('frost_days', 0), 1)}</strong></li>"
        + f"<li>Regentage (≥ 1 mm): <strong>{de(climate.get('wet_days', 0), 1)}</strong></li>"
        + f"<li>Sonnentage (&gt; 4 h): <strong>{de(climate.get('sunny_days', 0), 1)}</strong></li>"
        + "</ul>"
        '<p class="fine">Monatsmittel, keine Extremwerte. In Bergen und an Küsten kann der '
        'Rasterwert um ein paar Grad vom Ort abweichen.</p></section>'
    )

    future_html = ""
    if context.get("future_days") is not None:
        info = context.get("future_info") or {}
        delta = context["future_days"] - days
        direction = "mehr" if delta >= 0 else "weniger"
        models = int(info.get("model_count") or 31)
        scenario = str(info.get("scenario_name") or "Mittlerer Weg")
        future_html = (
            '<section class="card"><h2>Wie sieht es 2050 aus?</h2>'
            f'<p>Im Szenario „{e(scenario)}“ ({e(info.get("scenario_code") or "SSP2-4.5")}) '
            f'bleiben bei gleichem Wunschwetter im Mittel '
            f'<strong>{de(context["future_days"])} Tage</strong> pro Jahr – '
            f'{de(abs(delta))} Tage {direction} als heute. Grundlage sind {de(models)} CMIP6-Modelle '
            'aus dem regional aggregierten IPCC-Atlas, deren monatliche Temperaturänderung auf die '
            'heutigen Tage übertragen wird.</p>'
            '<p class="fine">Modellbasierte Näherung, keine Messung: Es werden keine lokalen '
            'Extremverteilungen simuliert, Taupunkt und Sonne werden aus den heutigen Werten '
            'abgeleitet.</p></section>'
        )

    neighbours = context["neighbours"]
    neighbour_rows = [[
        f'<a href="{e(_abs(origin, f"/klima/{other.slug}"))}">{e(other.name)}</a>',
        e(other.country),
        f'{de(view.days(other, "mild"))} Tage',
        f'{de(view.days(other, "winter"))} Tage',
        f'{de(_distance_km(entry, other))} km',
    ] for other in neighbours]
    compare = (
        '<section class="card"><h2>Orte in der Nähe</h2>'
        + (_table(("Ort", "Land", "Wohlfühltage/Jahr", "im Winterfenster (Okt–Apr)", "Entfernung"),
                  neighbour_rows) if neighbour_rows else "<p>Keine Vergleichsorte im Datensatz.</p>")
        + '<p class="fine">Winterfenster: Tage von Oktober bis April, an denen das Profil '
          '„milder Winter“ erfüllt ist. Entfernung als Luftlinie.</p></section>'
    )

    winter_days = view.days(entry, "winter")
    winter_warm = view.days(entry, "winter_warm")
    summer_days = view.days(entry, "summer")
    share_url = canonical
    share_text = f"{entry.name}: {de(days)} Wohlfühltage pro Jahr"
    faq_items = [
        (f"Wie viele Wohlfühltage hat {entry.name} im Jahr?",
         f"Bei den Kriterien {_plain(PROFILE_MILD_TEXT)} werden in {entry.name} im Mittel "
         f"{de(days)} von rund 365 Tagen erfüllt. Das entspricht dem {de(context['percentile'])}. "
         f"Perzentil der verglichenen Städte in Europa (Basis: {_plain(data_years)}, ERA5)."),
        (f"Wann ist die beste Zeit für {entry.name}?",
         f"Die meisten passenden Tage liegen im {best[0]} ({de(best_values[0], 1)}), "
         f"im {best[1]} ({de(best_values[1], 1)}) und im {best[2]} ({de(best_values[2], 1)}). "
         f"Im Juli-August-Durchschnitt sind es {de(summer_days, 1)} Tage mit dem Profil "
         f"„{PROFILE_SUMMER_NAME}“."),
        (f"Eignet sich {entry.name} als Winterquartier?",
         f"Im Winterfenster von Oktober bis April erfüllt {entry.name} das Profil "
         f"„{PROFILE_WINTER_NAME}“ an {de(winter_days, 1)} Tagen. Mit dem strengeren Profil "
         f"„{PROFILE_WINTER_WARM_NAME}“ (tagsüber 16–26 °C, nachts über 10 °C) sind es "
         f"{de(winter_warm, 1)} Tage. Der Vergleich mit deinem Wohnort lohnt: Im Norden liegen "
         f"diese Zahlen oft unter 20."),
        (f"Wie genau sind die Zahlen für {entry.name}?",
         f"Grundlage sind tägliche ERA5-Reanalysewerte von {_plain(data_years)} auf einem "
         f"0,75°-Raster (rund 50–80 km). Der nächste Rasterpunkt liegt "
         f"{de(report.get('grid_distance_km', 0))} km von den Stadtkoordinaten entfernt; seine "
         f"Temperaturen sind auf die Höhe des Ortes umgerechnet. ERA5 ist "
         f"ein Modell, kein Stationsmesswert – in Bergen und an Küsten sind Abweichungen möglich."),
    ]
    if context.get("future_days") is not None:
        faq_items.append((
            f"Bleibt das Klima in {entry.name} bis 2050 angenehm?",
            f"Im mittleren Szenario (SSP2-4.5) sinkt oder steigt die Zahl der Wohlfühltage bis 2050 "
            f"auf {de(context['future_days'])} Tage pro Jahr (heute {de(days)}). Das ist eine "
            f"modellbasierte Näherung aus 31 CMIP6-Modellen, keine Messung."))

    affiliate = _affiliate_for("year", name=entry.name, country=entry.country, lat=entry.lat,
                              lon=entry.lon, slug=entry.slug)
    affiliate_box = _affiliate_box(affiliate, heading=f"Unterkünfte in {entry.name}")

    badge_url = _abs(origin, f"/badge/{entry.slug}.svg")
    badge_snippet = (f'<a href="{canonical}"><img src="{badge_url}" width="300" height="76" '
                     f'alt="{share_text}"></a>')
    badge = (
        '<section class="card"><h2>Kennzahl einbetten</h2>'
        '<p>Blogs und Websites können die Zahl als Bild einbinden – die Grafik verlinkt '
        'zurück auf dieses Ortsprofil.</p>'
        + f'<p><img src="{e(badge_url)}" width="300" height="76" alt="{e(share_text)}"></p>'
        + "<pre><code>" + e(badge_snippet) + "</code></pre>"
        + f'<button type="button" class="chip" data-copy="{e(badge_snippet)}" '
          'data-done="Code kopiert" data-event="badge_copy">Einbettungs-Code kopieren</button>'
        "</section>"
    )

    body = (
        _breadcrumbs(origin, [("Karte", "/"), ("Orte", "/klima"), (entry.name, f"/klima/{entry.slug}")])
        + f'<h1>{e(entry.name)}: Wohlfühltage, Klimaprofil und beste Monate</h1>'
        + f'<div class="hero"><p class="kicker">{e(entry.country)} · {e(GRID_NOTE)}</p>'
        + lead
        + f'<p><a class="cta" href="{e(app_link)}" data-event="app_open">Auf der Karte öffnen</a> '
        + f'<a class="chip" href="{e(_abs(origin, "/ueberwintern-in-europa"))}">Überwintern in Europa</a></p>'
        + _shares(origin, share_url, share_text, event="share_target")
        + "</div>"
        + criteria + bars + profile + future_html + compare
        + _faq(faq_items) + affiliate_box + badge
    )
    structured = [
        _breadcrumbs_jsonld(origin, [("Karte", "/"), ("Orte", "/klima"), (entry.name, f"/klima/{entry.slug}")]),
        _faq_jsonld(faq_items),
        {
            "@context": "https://schema.org",
            "@type": "Place",
            "name": entry.name,
            "geo": {"@type": "GeoCoordinates", "latitude": entry.lat, "longitude": entry.lon},
            "address": {"@type": "PostalAddress", "addressCountry": entry.country},
            "description": _plain(f"{entry.name}: {de(days)} Wohlfühltage pro Jahr ({PROFILE_MILD_TEXT})."),
        },
    ]
    title = f"{entry.name}: {de(days)} Wohlfühltage pro Jahr – Klimaprofil und beste Monate"
    description = (f"{entry.name} ({entry.country}) bei {PROFILE_MILD_NAME}: {de(days)} Tage pro Jahr, "
                   f"beste Monate: {best[0]}, {best[1]}, {best[2]}. Monatsklima, Wintereignung und "
                   f"Vergleich mit Orten in der Nähe.")
    return layout(title=title, description=description, canonical=canonical, origin=origin,
                  body=body, image=card, structured=structured)


def _app_link(entry: Entry, origin: str, *, mode: str = "year", month: int = 7,
              profile: Params = PROFILE_MILD, period: str = "all") -> str:
    """Deep-Link in die App: derselbe Hash, den „Ergebnis teilen“ erzeugt."""
    token = encode_share_state(mode, params=profile.query(), name=entry.name,
                               country=entry.country, lat=entry.lat, lon=entry.lon,
                               period=period, month=month)
    return _abs(origin, f"/?state={token}")


# --------------------------------------------------------------------- Ratgeber
@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    h1: str
    description: str
    kicker: str
    intro: tuple[str, ...]
    profile: Params
    profile_name: str
    profile_text: str
    months: tuple[int, ...]
    counts_key: str
    countries: tuple[str, ...]
    second_key: str
    second_label: str
    column_label: str
    limit: int
    faq: tuple[tuple[str, str], ...]
    method: str
    affiliate_context: str


GUIDES: dict[str, Guide] = {
    "ueberwintern-in-europa": Guide(
        slug="ueberwintern-in-europa",
        title="Überwintern in Europa: die mildesten Orte im Vergleich",
        h1="Überwintern in Europa: wo der Winter mild bleibt",
        description=("Welche Orte in Europa im Winterfenster Oktober bis April die meisten "
                     "Tage mit mildem Wohlfühlwetter haben – berechnet aus ERA5-Tageswerten "
                     "2016–2025, mit Vergleich zu deutschen Städten."),
        kicker="Winterquartier · Oktober bis April",
        intro=(
            "Wer den Winter nicht im Grau verbringen will, sucht keine Rekordtemperaturen, "
            "sondern verlässlich angenehme Tage: tagsüber 10–20 °C, nachts über 3 °C, ein paar "
            "Stunden Sonne. Genau das zählt diese Seite – nicht die Höchsttemperatur, sondern "
            "die Zahl der Tage, an denen ein Tag durchgehend passt.",
            "Die Rangliste wird bei jedem Datenlauf neu berechnet. Sie zeigt Orte, die im "
            "Raster liegen (keine Kanaren, kein Nordafrika – außerhalb des Datenfensters von "
            "34–72° N / 25° W–45° O) und mindestens 25.000 Einwohner haben. Die Zahl in "
            "Klammern ist die Anzahl der Treffer unter allen Städten des Datensatzes.",
        ),
        profile=PROFILE_WINTER,
        profile_name=PROFILE_WINTER_NAME,
        profile_text=PROFILE_WINTER_TEXT,
        months=WINTER_MONTHS,
        counts_key="winter",
        countries=(),
        second_key="winter_warm",
        second_label=PROFILE_WINTER_WARM_NAME,
        column_label="Tage im Winterfenster",
        limit=30,
        faq=(
            ("Was heißt „milder Winter“ genau?",
             f"{PROFILE_WINTER_TEXT}. Alle Kriterien müssen am selben Tag erfüllt sein; "
             "ein einzelner warmer Nachmittag zählt also nicht."),
            ("Warum fehlen die Kanaren und Nordafrika?",
             "Der ausgewertete Datensatz deckt 34–72° N und 25° W–45° O ab. Kanaren und "
             "Nordafrika liegen außerhalb; für sie müsste der Datenlauf erweitert werden."),
            ("Sind das Empfehlungen zum Auswandern?",
             "Nein. Die Zahlen beschreiben nur das Wetter. Miete, Visum, Krankenversicherung, "
             "Internet und Anreise prüft man getrennt – die Seite ersetzt keine Beratung."),
            ("Wie unterscheiden sich die beiden Profile?",
             f"„{PROFILE_WINTER_NAME}“ ist der breite Kompromiss, "
             f"„{PROFILE_WINTER_WARM_NAME}“ verlangt 16–26 °C am Tag und über 10 °C in der "
             "Nacht. Der Unterschied zwischen beiden Spalten zeigt, wie streng der Ort wirkt."),
        ),
        method=("Gezählt werden Tage von Oktober bis April (212,9 mögliche Tage), an denen "
                "Tageshöchstwert, Tagesminimum, Taupunkt und Sonnenstunden gleichzeitig im "
                "Wunschbereich liegen. ERA5-Reanalyse 2016–2025, Raster 0,75°, Mittel über "
                "10 Jahre; keine offizielle 30-jährige Klimanormalperiode."),
        affiliate_context="wq",
    ),
    "sommerhitze-entfliehen": Guide(
        slug="sommerhitze-entfliehen",
        title="Der Sommerhitze entfliehen: milde Orte für Juli und August",
        h1="Der Hitze entfliehen: wo der Sommer erträglich bleibt",
        description=("Orte in Europa, an denen Juli und August mild bleiben: 12–28 °C, "
                     "Taupunkt höchstens 15 °C, mindestens 4 Stunden Sonne – aus "
                     "ERA5-Tageswerten 2016–2025."),
        kicker="Sommerfrische · Juli und August",
        intro=(
            "Hitze ist nicht nur eine Frage der Temperatur: Ein Taupunkt über 18 °C macht "
            "26 °C unangenehmer als 32 °C trockene Luft. Diese Liste zählt deshalb Tage, an "
            "denen es tagsüber 12–28 °C bleibt, nachts nicht über 19 °C geht, die Luft trocken "
            "bleibt (Taupunkt höchstens 15 °C) und trotzdem mindestens vier Stunden Sonne "
            "scheint.",
            "Das ist bewusst kein Hitze-Ranking, sondern ein Erträglichkeits-Ranking: Küsten "
            "und Höhenlagen gewinnen, klassische Mittelmeerziele verlieren in Juli und August "
            "deutlich. Wer warmes Wasser sucht, findet über die Karte Orte mit eigenem Profil.",
        ),
        profile=PROFILE_SUMMER_MILD,
        profile_name=PROFILE_SUMMER_MILD_NAME,
        profile_text=("tagsüber 12–28 °C, nachts nicht über 19 °C, Taupunkt höchstens 15 °C, "
                      "mindestens 4 h Sonne"),
        months=SUMMER_MONTHS,
        counts_key="summer_mild",
        countries=REACHABLE_COUNTRIES,
        second_key="summer",
        second_label=PROFILE_SUMMER_NAME,
        column_label="Tage im Juli/August",
        limit=30,
        faq=(
            ("Warum steht kein Mittelmeerort ganz oben?",
             "Im Juli und August überschreiten viele Mittelmeerorte die 28 °C-Grenze oder die "
             "Nachtwerte. Genau deshalb ist diese Liste für alle gedacht, die Hitze vermeiden "
             "wollen – nicht für Badeurlaub."),
            ("Was bedeutet der Taupunkt für mich?",
             "Der Taupunkt beschreibt die Schwüle. Über 18 °C fühlt sich warme Luft drückend "
             "an, unter 15 °C trocken. Die Kriterien hier verlangen höchstens 15 °C."),
            ("Gilt das auch für den ganzen Sommer?",
             "Gezählt werden nur Juli und August (62 Tage). Wer Mai, Juni und September "
             "einschließen will, nutzt die Karte und wählt dort mehrere Monate."),
            ("Wie belastbar sind die Werte?",
             "Es sind Mittel über zehn Jahre auf einem 0,75°-Raster. Einzelne Hitzejahre "
             "können deutlich darüber liegen; die Karte lässt sich auf die letzten fünf Jahre "
             "umschalten, um diese Streuung zu sehen."),
        ),
        method=("Gezählt werden Tage im Juli und August, an denen Tageshöchstwert, "
                "Tagestiefstwert, Taupunkt und Sonnenschein gleichzeitig dem Profil "
                "entsprechen. ERA5-Reanalyse 2016–2025, Raster 0,75°, Mittel über 10 Jahre. "
                "Die Spalte „warm & schweißfrei“ zeigt zum Vergleich das anspruchsvollere "
                "Warmprofil."),
        affiliate_context="month",
    ),
}


def guide_page(view: DatasetView, origin: str, slug: str) -> tuple[str, dict] | None:
    guide = GUIDES.get(slug)
    if guide is None:
        return None
    return render_guide(view, origin, guide), {"guide": guide}


def render_guide(view: DatasetView, origin: str, guide: Guide) -> str:
    cd = view.cd
    rows = ranked_places(view, guide.counts_key, guide.limit, countries=guide.countries)
    limit = cd.month_limit(guide.months, "all")
    table_rows = []
    for row in rows:
        entry = view.by_slug.get(row["slug"])
        href = e(_abs(origin, f"/klima/{row['slug']}"))
        place = f'<a href="{href}">{e(row["name"])}</a>' if entry else e(row["name"])
        second = de(view.days(entry, guide.second_key), 1) if entry else "–"
        table_rows.append([place, e(row["country"]), de(row["days"], 1), second])
    references = []
    for name in REFERENCE_CITIES:
        entry = view.by_name(name)
        if entry is None:
            continue
        references.append([e(entry.name), e(entry.country),
                           de(view.days(entry, guide.counts_key), 1),
                           de(view.days(entry, guide.second_key), 1)])

    guide_url = _abs(origin, f"/{guide.slug}")
    intro_html = "".join(f"<p>{paragraph}</p>" for paragraph in guide.intro)
    share_text = guide.title
    affiliate = _affiliate_for(guide.affiliate_context, name="Europa", country="", lat=48.0,
                               lon=10.0, slug="europa")
    top = rows[0] if rows else None
    lead = ""
    if top:
        lead = (f"<p><strong>Vorn liegt derzeit {e(top['name'])} ({e(top['country'])}) mit "
                f"{de(top['days'], 1)} von {de(limit, 1)} möglichen Tagen</strong> im "
                f"Zeitfenster {e(months_label(guide.months))}.</p>")

    body = (
        _breadcrumbs(origin, [("Karte", "/"), (guide.h1, f"/{guide.slug}")])
        + f"<h1>{e(guide.h1)}</h1>"
        + f'<div class="hero"><p class="kicker">{e(guide.kicker)} · {e(GRID_NOTE)}</p>'
        + lead + intro_html
        + f'<p><a class="cta" href="{e(_abs(origin, "/"))}" data-event="app_open">'
          'Eigenes Wunschwetter in der Karte einstellen</a></p>'
        + _shares(origin, guide_url, share_text, event="share_target")
        + "</div>"
        + '<section class="card"><h2>Die besten Orte</h2>'
        + _table(("Ort", "Land", guide.column_label, guide.second_label), table_rows,
                 caption=f"Profil „{guide.profile_name}“ · {months_label(guide.months)}")
        + '<p class="fine">Sortiert nach der ersten Tagesspalte. Klick auf einen Ort öffnet '
          'das Ortsprofil mit Monatsverlauf, Klimawerten und Vergleichsorte.</p></section>'
        + '<section class="card"><h2>Vergleich: deutsche Städte</h2>'
        + _table(("Ort", "Land", guide.column_label, guide.second_label), references)
        + '<p class="fine">Dieselbe Rechnung für bekannte Wohnorte – so lässt sich der '
          'Unterschied in Tagen ablesen.</p></section>'
        + '<section class="card"><h2>Wie gerechnet wird</h2><p>' + e(guide.method) + "</p>"
        + f'<p class="fine">Profil „{e(guide.profile_name)}“: ' + e(guide.profile_text)
        + ". Alle Werte kommen aus derselben Pipeline wie die Karte; es werden keine Zahlen "
          "geschätzt oder aus fremden Quellen übernommen.</p></section>"
        + _faq(guide.faq)
        + _affiliate_box(affiliate, heading="Unterkünfte für längere Aufenthalte")
    )
    structured = [
        _breadcrumbs_jsonld(origin, [("Karte", "/"), (guide.h1, f"/{guide.slug}")]),
        _faq_jsonld(guide.faq),
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": guide.title,
            "description": guide.description,
            "inLanguage": "de",
            "isAccessibleForFree": True,
            "dateModified": str(cd.meta.get("generated_at") or cd.meta.get("end_date") or ""),
            "publisher": {"@type": "Organization", "name": SITE_NAME},
        },
    ]
    return layout(title=guide.title, description=guide.description, canonical=guide_url,
                  origin=origin, body=body, image=_abs(origin, f"/{guide.slug}/card.png"),
                  structured=structured, og_type="article")


def index_page(view: DatasetView, origin: str) -> str:
    groups: dict[str, list[Entry]] = {}
    for entry in view.entries:
        groups.setdefault(entry.country or "Ohne Land", []).append(entry)
    sections = []
    for country in sorted(groups, key=fold):
        entries = sorted(groups[country], key=lambda item: fold(item.name))
        links = " · ".join(
            f'<a href="{e(_abs(origin, f"/klima/{entry.slug}"))}">{e(entry.name)}</a>'
            for entry in entries)
        sections.append(f'<section class="card"><h2>{e(country)} '
                        f'<span class="count">{de(len(entries))}</span></h2><p class="links">{links}</p></section>')
    total = len(view.entries)
    body = (
        _breadcrumbs(origin, [("Karte", "/"), ("Orte", "/klima")])
        + "<h1>Ortsprofile: Wohlfühltage in Europa</h1>"
        + '<div class="hero"><p class="kicker">' + e(GRID_NOTE) + "</p>"
        + f"<p>{de(total)} Orte aus dem Klimaraster haben eine eigene Seite: Wohlfühltage pro "
          "Jahr und Monat, Klimaprofil, Wintereignung, Vergleichsorte und der Blick auf 2050. "
          "Ausgewertet wird das Profil „" + e(PROFILE_MILD_NAME) + "“ (" + e(PROFILE_MILD_TEXT)
        + "). Andere Werte lassen sich direkt in der Karte einstellen.</p>"
        + f'<p><a class="cta" href="{e(_abs(origin, "/"))}" data-event="app_open">Zur Karte</a> '
        + f'<a class="chip" href="{e(_abs(origin, "/ueberwintern-in-europa"))}">Überwintern</a> '
        + f'<a class="chip" href="{e(_abs(origin, "/sommerhitze-entfliehen"))}">Hitze entfliehen</a></p></div>'
        + "".join(sections)
    )
    description = (f"{de(total)} Ortsprofile mit Wohlfühltagen, Monatsklima, Wintereignung und "
                   "Klimablick auf 2050 – berechnet aus ERA5-Tagesdaten.")
    structured = [_breadcrumbs_jsonld(origin, [("Karte", "/"), ("Orte", "/klima")])]
    return layout(title="Ortsprofile: Wohlfühltage in Europa – Mein perfektes Klima",
                  description=description, canonical=_abs(origin, "/klima"), origin=origin,
                  body=body, structured=structured)


# --------------------------------------------------------------- Technikseiten
def robots_txt(origin: str) -> str:
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /analytics",
        "Disallow: /healthz",
        "Disallow: /share?",
        "",
        f"Sitemap: {_abs(origin, '/sitemap.xml')}",
        "",
    ]
    return "\n".join(lines)


def sitemap_xml(view: DatasetView, origin: str) -> str:
    today = date.today().isoformat()
    lastmod = str(view.cd.meta.get("generated_at") or today)[:10] or today
    entries: list[tuple[str, str, str, str]] = [
        ("/", lastmod, "weekly", "1.0"),
        ("/klima", lastmod, "weekly", "0.8"),
        ("/ueberwintern-in-europa", lastmod, "weekly", "0.9"),
        ("/sommerhitze-entfliehen", lastmod, "weekly", "0.9"),
    ]
    entries += [(f"/klima/{entry.slug}", lastmod, "monthly", "0.6") for entry in view.entries]
    if legal_configured():
        entries += [("/impressum", today, "yearly", "0.2"), ("/datenschutz", today, "yearly", "0.2")]
    urls = "".join(
        "<url>"
        f"<loc>{e(_abs(origin, path))}</loc>"
        f"<lastmod>{e(mod)}</lastmod>"
        f"<changefreq>{e(freq)}</changefreq>"
        f"<priority>{e(priority)}</priority>"
        "</url>" for path, mod, freq, priority in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + urls + "</urlset>\n")


def feed_xml(view: DatasetView, origin: str) -> str:
    """RSS 2.0 – damit Newsletter- und Social-Werkzeuge ohne Handarbeit laufen."""
    now = datetime.now(timezone.utc)
    build = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
    month = now.month - 1
    # Großzügig ziehen: Nicht jeder Top-Ort hat ein Ortsprofil (Einwohnerschwelle);
    # übersprungen wird trotzdem, aber der Feed soll nicht leer laufen.
    ranking = view.cd.ranking_city_counts(view.cd.city_counts(PROFILE_MILD, month, "all"), 40)
    by_name = {entry.name: entry.slug for entry in view.entries}

    def item(title: str, link: str, description: str) -> str:
        return ("<item>"
                f"<title>{e(title)}</title>"
                f"<link>{e(link)}</link>"
                f'<guid isPermaLink="true">{e(link)}</guid>'
                f"<description>{e(description)}</description>"
                f"<pubDate>{build}</pubDate>"
                "</item>")

    month_label = MONTH_NAMES[month]
    items = [
        item(GUIDES["ueberwintern-in-europa"].title,
             _abs(origin, "/ueberwintern-in-europa"),
             "Die mildesten Orte im Winterfenster Oktober–April, berechnet aus ERA5-Tageswerten; "
             "inklusive Vergleich mit deutschen Städten."),
        item(GUIDES["sommerhitze-entfliehen"].title,
             _abs(origin, "/sommerhitze-entfliehen"),
             "Wo Juli und August erträglich bleiben: 12–28 °C, Taupunkt höchstens 15 °C, "
             "mindestens 4 h Sonne."),
        item(f"Beste Orte im {month_label}",
             _abs(origin, "/klima"),
             "Die zehn Städte mit den meisten passenden Tagen im " + month_label +
             " bei " + PROFILE_MILD_TEXT + "."),
        item("Ortsverzeichnis", _abs(origin, "/klima"),
             f"{de(len(view.entries))} Ortsprofile mit Wohlfühltagen, Monatsklima, "
             "Wintereignung und Blick auf 2050."),
    ]
    added = 0
    for row in ranking:
        slug = by_name.get(row["name"])
        if not slug:
            continue
        items.append(item(
            f"{row['name']}: {de(row['days'], 1)} Wohlfühltage im {month_label}",
            _abs(origin, f"/klima/{slug}"),
            f"Wohlfühltage im {month_label} in {row['name']} ({row['country']}) bei "
            f"{PROFILE_MILD_TEXT}: {de(row['days'], 1)} von rund "
            f"{de(365.25 / 12, 1)} möglichen Tagen."))
        added += 1
        if added >= 6:
            break
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            f"<title>{e(SITE_NAME)} – Aktualisierungen</title>"
            f"<link>{e(_abs(origin, '/'))}</link>"
            f"<description>{e(SITE_DESCRIPTION)}</description>"
            "<language>de-de</language>"
            f"<lastBuildDate>{build}</lastBuildDate>"
            f'<atom:link href="{e(_abs(origin, "/feed.xml"))}" rel="self" type="application/rss+xml"/>'
            + "".join(items) + "</channel></rss>\n")


def badge_svg(entry: Entry, days: float, *, label: str = "Wohlfühltage/Jahr") -> str:
    """Kleine, einbettbare Kennzahl-Grafik (kein Fremdwerkzeug, keine Anfrage)."""
    name = entry.name if len(entry.name) <= 18 else entry.name[:17] + "…"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="76" viewBox="0 0 300 76" '
        'role="img" aria-label="' + e(f"{entry.name}: {de(days)} {label}") + '">'
        '<rect x="0.5" y="0.5" width="299" height="75" rx="14" fill="#0f1a2e" stroke="#26364f"/>'
        '<circle cx="34" cy="38" r="11" fill="#ffb454"/>'
        '<g stroke="#ffb454" stroke-width="2" stroke-linecap="round">'
        '<path d="M34 14v7M34 55v7M10 38h7M51 38h7M18 22l5 5M45 49l5 5M50 22l-5 5M23 49l-5 5"/></g>'
        f'<text x="60" y="32" fill="#eef3f9" font-family="Inter, system-ui, sans-serif" '
        f'font-size="17" font-weight="600">{e(name)}</text>'
        f'<text x="60" y="55" fill="#98a9be" font-family="Inter, system-ui, sans-serif" '
        f'font-size="14">{e(f"{de(days)} {label}")}</text>'
        "</svg>"
    )


# ------------------------------------------------------------- Social-Kartenpaket
def social_card_state(entry: Entry, *, mode: str = "year", month: int = 7,
                      profile: Params = PROFILE_MILD) -> dict[str, Any]:
    """Zustandsobjekt für :func:`app.sharing.render_share_card` (Social-Grafiken)."""
    return {
        "mode": mode,
        "month": month,
        "period": "all",
        "params": profile.query(),
        "selected": {"lat": entry.lat, "lon": entry.lon, "name": entry.name,
                     "country": entry.country},
    }


def guide_choices() -> list[dict[str, Any]]:
    return [{"slug": guide.slug, "title": guide.title, "description": guide.description,
             "profile_name": guide.profile_name, "months": list(guide.months),
             "counts_key": guide.counts_key, "limit": guide.limit}
            for guide in GUIDES.values()]


def page_paths(view: DatasetView) -> list[str]:
    """Alle indexierbaren Pfade (für Prüfungen und Werkzeuge)."""
    return (["/", "/klima", "/ueberwintern-in-europa", "/sommerhitze-entfliehen"]
            + [f"/klima/{entry.slug}" for entry in view.entries])


# ------------------------------------------------------------------ Ratgeber-Grafik
def _card_canvas():
    """1200×630-Grundfläche mit Markenverlauf und Sonnenzeichen."""
    from PIL import Image, ImageDraw

    from .sharing import load_font

    width, height = 1200, 630
    image = Image.new("RGB", (width, height), (11, 18, 32))
    draw = ImageDraw.Draw(image)
    for x in range(width):
        ratio = x / (width - 1)
        color = tuple(round((11, 18, 32)[i] + ((19, 50, 65)[i] - (11, 18, 32)[i]) * ratio)
                      for i in range(3))
        draw.line((x, 0, x, height), fill=color)
    glyphs = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyphs)
    glyph_draw.ellipse((820, -220, 1400, 360), fill=(255, 180, 84, 20))
    glyph_draw.ellipse((40, 420, 520, 900), fill=(63, 201, 172, 22))
    image = Image.alpha_composite(image.convert("RGBA"), glyphs).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.ellipse((70, 58, 108, 96), fill=(255, 180, 84))
    draw.text((128, 61), "MEIN PERFEKTES KLIMA", font=load_font(23, True), fill=(238, 243, 249))
    draw.text((128, 92), TAGLINE, font=load_font(18), fill=(152, 169, 190))
    return image, draw


def _card_footer(draw, text: str) -> None:
    from .sharing import load_font

    draw.rectangle((72, 596, 1128, 598), fill=(255, 180, 84))
    draw.text((74, 604), text, font=load_font(18), fill=(152, 169, 190))


def render_home_card(view: DatasetView) -> bytes:
    """Startseiten-Grafik für Social-Previews – mit Zahlen aus dem Datensatz."""
    import io

    from .sharing import load_font, text_width

    image, draw = _card_canvas()
    title_font = load_font(48, True)
    body_font = load_font(26)
    body_bold = load_font(26, True)

    lines, current = [], ""
    for word in "Wo in Europa ist das Wetter so, wie du es magst?".split():
        candidate = f"{current} {word}".strip()
        if text_width(draw, candidate, title_font) > 1000 and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    y = 158
    for line in lines[:2]:
        draw.text((72, y), line, font=title_font, fill=(238, 243, 249))
        y += 58

    winter = ranked_places(view, "winter", 3)
    summer = ranked_places(view, "summer_mild", 3)
    start_year = str(view.cd.meta.get("start_date") or "")[:4]
    end_year = str(view.cd.meta.get("end_date") or "")[:4]
    years = f"{start_year}–{end_year}" if start_year and end_year else ""
    rows = [
        (f"{de(view.cd.n_points)} Rasterpunkte · {de(view.cd.years)} Jahre ERA5-Tage", years),
        (f"Milder Winter: {', '.join(row['name'] for row in winter)}", "Tage Okt–Apr"),
        (f"Hitze entfliehen: {', '.join(row['name'] for row in summer)}", "Tage Jul/Aug"),
        ("Winterquartier-Suche", "Wohnort + Saison"),
    ]
    y = max(y + 24, 300)
    for index, (left, right) in enumerate(rows):
        line_y = y + index * 46
        text = left if len(left) <= 46 else left[:45] + "…"
        draw.text((74, line_y), text, font=body_bold if index == 0 else body_font,
                  fill=(238, 243, 249) if index == 0 else (215, 226, 238))
        draw.text((1128, line_y), right[:26], font=body_font, fill=(152, 169, 190), anchor="ra")
    _card_footer(draw, "Quelle: ERA5 · Copernicus / ECMWF · Raster 0,75° (50–80 km) · kostenlos")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_guide_card(view: DatasetView, guide: Guide) -> bytes:
    """1200×630-Grafik für einen Ratgeber: Titel plus aktuelle Top-5-Liste.

    Wird bei jedem Aufruf aus den aktuellen Daten gezeichnet – die Vorschau in
    sozialen Netzwerken zeigt also den Stand des letzten Datenlaufs, kein
    eingefrorenes Bild.
    """
    import io

    from .sharing import load_font, text_width

    image, draw = _card_canvas()
    small = load_font(18)
    title_font = load_font(46, True)
    row_font = load_font(26)
    row_bold = load_font(26, True)

    title = guide.h1
    lines, current = [], ""
    for word in title.split():
        candidate = f"{current} {word}".strip()
        if text_width(draw, candidate, title_font) > 1000 and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    y = 170
    for line in lines[:2]:
        draw.text((72, y), line, font=title_font, fill=(238, 243, 249))
        y += 56
    draw.text((74, y + 6), f"{guide.profile_name} · {months_label(guide.months)}",
              font=small, fill=(255, 190, 105))

    rows = ranked_places(view, guide.counts_key, 5, countries=guide.countries)
    by_name = {entry.name: entry for entry in view.entries}
    y = max(y + 60, 330)
    draw.text((74, y - 34), "Aktuell vorn (Tage im Zeitfenster)", font=small, fill=(152, 169, 190))
    for index, row in enumerate(rows):
        line_y = y + index * 46
        draw.text((74, line_y), f"{index + 1}.", font=row_font, fill=(120, 138, 160))
        entry = by_name.get(row["name"])
        label = row["name"] if len(row["name"]) <= 26 else row["name"][:25] + "…"
        draw.text((116, line_y), label, font=row_bold, fill=(238, 243, 249))
        draw.text((520, line_y), f"{de(row['days'], 1)} Tage", font=row_font, fill=(255, 190, 105))
        country = entry.country if entry else row["country"]
        draw.text((700, line_y), str(country)[:28], font=row_font, fill=(152, 169, 190))

    _card_footer(draw, "Quelle: ERA5 · Copernicus / ECMWF · Raster 0,75° (50–80 km)")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


# ---------------------------------------------------------------- Rechtsseiten
def render_impressum(legal: Any, origin: str) -> str:
    operator = legal.operator
    contact = legal.contact
    hosting = legal.hosting
    rows = [
        ("Anbieter", operator.name + (f" ({operator.legal_form})" if operator.legal_form else "")),
        ("Anschrift", f"{operator.street}, {operator.zip} {operator.city}, {operator.country}"),
        ("E-Mail", contact.email),
    ]
    if contact.phone:
        rows.append(("Telefon", contact.phone))
    if legal.vat_id:
        rows.append(("Umsatzsteuer-ID", legal.vat_id))
    rows.append(("Inhaltlich verantwortlich", legal.responsible))
    rows.append(("Hosting", hosting.provider + (f", {hosting.location}" if hosting.location else "")))
    body = (
        "<h1>Impressum</h1>"
        + _table(("Angabe", "Wert"), [[e(label), e(value)] for label, value in rows])
        + (f'<section class="card"><h2>Hinweise des Betreibers</h2><p>{e(legal.notes)}</p></section>'
           if legal.notes else "")
        + '<section class="card"><h2>Inhalt und Haftung</h2>'
          "<p>Die Klima-Auswertungen entstehen automatisch aus der ERA5-Reanalyse (Copernicus/ECMWF) "
          "und einem regionalen Klimaprojektions-Datensatz. Es sind modellbasierte Mittelwerte auf "
          "einem Raster von 0,75° (rund 50–80 km), keine Stationsmesswerte und keine Vorhersage. "
          "Die Seiten sind keine Miet-, Visum-, Finanz- oder Rechtsberatung.</p>"
          "<p>Für Inhalte externer Seiten, auf die verlinkt wird, sind deren Betreiber "
          "verantwortlich.</p></section>"
        + '<p class="fine">Stand: ' + e(legal.updated) + "</p>"
    )
    return layout(title="Impressum – " + SITE_NAME, description="Impressum und Anbieterkennzeichnung.",
                  canonical=_abs(origin, "/impressum"), origin=origin, body=body)


def render_datenschutz(legal: Any, origin: str, *, analytics_on: bool, ads_on: bool,
                       affiliate_on: bool) -> str:
    """Datenschutzerklärung aus dem tatsächlichen Verhalten der Anwendung."""
    hosting = legal.hosting
    sections = [
        "<h1>Datenschutzerklärung</h1>",
        '<section class="card"><h2>Verantwortlich</h2>'
        f"<p>{e(legal.operator.name)}, {e(legal.operator.street)}, "
        f"{e(legal.operator.zip)} {e(legal.operator.city)}, {e(legal.operator.country)} · "
        f'E-Mail: <a href="mailto:{e(legal.contact.email)}">{e(legal.contact.email)}</a></p></section>',
        '<section class="card"><h2>Hosting und Serverprotokolle</h2>'
        f"<p>Die Anwendung läuft bei {e(hosting.provider)}"
        + (f" (Standort: {e(hosting.location)})" if hosting.location else "")
        + ". Beim Aufruf verarbeitet der Hoster technisch notwendige Verbindungsdaten "
          "(IP-Adresse, Zeitpunkt, angeforderte Adresse, User-Agent). Diese Protokolle dienen "
          "dem sicheren Betrieb und werden nach den Vorgaben des Hosters "
        + (f"in der Regel nach {de(hosting.log_retention_days)} Tagen" if hosting.request_logs
           else "nur bei Störungen")
        + " gelöscht. Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO (sicherer Betrieb)."
          "</p></section>",
        '<section class="card"><h2>Keine Cookies, keine Nutzerkennung</h2>'
        "<p>Die App setzt keine Cookies, speichert nichts im Browser-Speicher und legt keine "
        "Nutzerkonten an. Deine Wettereingaben und der gewählte Ort stecken nur in der Adresszeile "
        "(Hash) und werden nicht an uns übertragen, außer du öffnest einen geteilten Link.</p>"
        "</section>",
        '<section class="card"><h2>Reichweitenmessung</h2>'
        + ("<p>Wir zählen Seitenaufrufe und einige Funktionsereignisse (etwa: Ergebnis geteilt, "
           "Partnerlink geöffnet) als reine Tages-Summen in einer eigenen Datenbank. Gespeichert "
           "werden nur der UTC-Tag, eine grobe Seitenbezeichnung und der Name des Ereignisses aus "
           "einer festen Liste. Es gibt keine Cookies, keine Nutzer-IDs, keine Wiedererkennung "
           "über mehrere Aufrufe, keine Speicherung von IP-Adresse, User-Agent, Referrer, "
           "Suchbegriff, Ort oder Filtern. Alte Tageszähler werden nach 400 Tagen gelöscht. "
           "Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO (Reichweite ohne Personenbezug); "
           "die Auswertung ist auf den Betreiber beschränkt.</p>"
           if analytics_on else
           "<p>Es findet derzeit keine Reichweitenmessung in der Anwendung statt.</p>")
        + "</section>",
        '<section class="card"><h2>Ortssuche</h2>'
        "<p>Gibst du einen Ortsnamen ein, wird dieser Text an unseren Server geschickt, um "
        "Vorschläge und Werte zu berechnen. Die App speichert Suchbegriffe nicht; sie können "
        "jedoch in den technischen Zugriffsprotokollen des Hosters aufscheinen (siehe oben)."
        "</p></section>",
        '<section class="card"><h2>Kartenkacheln</h2>'
        "<p>Die Kartenansicht lädt Kacheln direkt von OpenStreetMap (© OpenStreetMap-Mitwirkende). "
        "Dabei wird deine IP-Adresse an den Kartenanbieter übertragen. Die App selbst liefert "
        "Schriften und Bibliotheken von eigenen Servern aus und ruft dabei keine externen "
        "Dienste, keine Schriftarten-CDNs und keine Werbenetzwerke auf.</p></section>",
    ]
    if ads_on:
        sections.append(
            '<section class="card"><h2>Anzeigen eigener Geschäftspartner</h2>'
            "<p>Es kann ein unaufdringlicher Anzeigenplatz mit Text und Link eines direkt "
            "gewonnenen Sponsors erscheinen. Dieser Platz wird aus unserem eigenen Server "
            "ausgeliefert: keine Werbenetzwerk-Skripte, keine Cookies, keine Pixel, kein "
            "Besucherprofil. Der Sponsor erfährt nur dann etwas über dich, wenn du den Link "
            "anklickst.</p></section>")
    if affiliate_on:
        sections.append(
            '<section class="card"><h2>Partner-Links (Empfehlungen)</h2>'
            "<p>Auf manchen Seiten kann ein Link zu einem Buchungs- oder Vermittlungsangebot "
            "stehen. Klickst du ihn an, wird der Zielanbieter aufgerufen; er erhält dabei deine "
            "IP-Adresse und technische Angaben wie bei jedem Webseitenbesuch. Eine Empfehlung "
            "kann für uns vergütet werden; auf das Klima-Ergebnis hat das keinen Einfluss. "
            "Für die Datenverarbeitung auf der Zielseite gilt deren Datenschutzerklärung."
            "</p>"
            + (f"<p>{e(legal.affiliate_note)}</p>" if legal.affiliate_note else "")
            + "</section>")
    sections.append(
        '<section class="card"><h2>Deine Rechte</h2>'
        "<p>Du hast nach Art. 15–21 DSGVO das Recht auf Auskunft, Berichtigung, Löschung, "
        "Einschränkung, Datenübertragbarkeit und Widerspruch. Da wir keine personenbezogenen "
        "Datenbestände über Besucher führen, betrifft das in der Praxis vor allem die "
        "Serverprotokolle des Hosters. Beschwerden kannst du bei der zuständigen "
        "Datenschutz-Aufsichtsbehörde einlegen. Automatisierte Entscheidungsfindung und "
        "Profiling finden nicht statt.</p></section>")
    body = "".join(sections) + '<p class="fine">Stand: ' + e(legal.updated) + "</p>"
    return layout(title="Datenschutz – " + SITE_NAME,
                  description="Wie diese Website mit Daten umgeht: keine Cookies, keine Nutzerkennung, "
                              "aggregierte Reichweitenmessung.",
                  canonical=_abs(origin, "/datenschutz"), origin=origin, body=body)
