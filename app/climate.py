"""Datenzugriff, Tageszählung und Karten-Rendering für „Mein perfektes Klima“.

Der Datensatz (``app/data/climate_grid.npz``) enthält tägliche ERA5-Werte
(2016–2025) für ~2.250 Landpunkte über Europa in 0,75°-Auflösung:

    tmax, tmin  – Tageshöchst-/Tiefsttemperatur (int8, 0,5 °C-Schritte)
    tdew        – mittlerer Tages-Taupunkt (int8, 0,5 °C-Schritte)
    sun         – Sonnenscheindauer pro Tag (int8, 0,25 h-Schritte)
    prec        – Niederschlagssumme pro Tag (int8, 0,5 mm-Schritte)

Gezählt werden die Tage, die **alle** Kriterien gleichzeitig erfüllen – exakt
über die tägliche Zeitreihe, nicht über Monatsmittel.

Neben „ganzes Jahr“ und „Urlaubsmonat“ gibt es den **Winterquartier-Modus**
(:class:`Filter` mit ``kind='combo'``): Hier treffen zwei Wetterprofile
aufeinander – ein *Sommer*-Profil für das Sommerfenster und ein *Winter*-Profil
(Winterquartier-Wetter) für die übrigen Monate. Die Karte zeigt dann die Summe
der Wohlfühl-Tage beider Jahreszeiten an einem Ort („Ganzjahres-Optimum“), und
:meth:`ClimateData.season_split_report` sucht zusätzlich die beste Aufteilung
des Jahres (welche Monate Sommer-, welche Winterquartier sind).
:meth:`ClimateData.pair_report` beantwortet die Zwei-Orte-Frage: „Ich habe mein
Sommerquartier – wo ist das beste Winter-Gegenstück?“
"""
from __future__ import annotations

import io
import json
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree

from .names import search_key
from .projections import ProjectionCatalog

VARS = ("tmax", "tmin", "tdew", "sun", "prec")
# Skalen: int8-Schritte je Einheit der Rohdaten. Temperatur/Taupunkt kommen in °C
# (0,5-°C-Schritte), Sonnenschein kommt in *Sekunden* (0,25-h-Schritte = 900 s),
# Niederschlag in mm (0,5-mm-Schritte).
SCALES = {"tmax": 2.0, "tmin": 2.0, "tdew": 2.0, "sun": 4.0 / 3600.0, "prec": 2.0}
SUN_SECONDS_PER_HOUR = 3600.0
# Faktor int8-Rohwert -> Einheit der Anzeige: °C, Stunden bzw. mm
UNIT_FACTORS = {
    "tmax": 1.0 / SCALES["tmax"],
    "tmin": 1.0 / SCALES["tmin"],
    "tdew": 1.0 / SCALES["tdew"],
    "sun": 1.0 / SCALES["sun"] / SUN_SECONDS_PER_HOUR,
    "prec": 1.0 / SCALES["prec"],
}
DATA_START_LAT = None  # wird aus der Meta-Datei gelesen

MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni",
               "Juli", "August", "September", "Oktober", "November", "Dezember"]

# Alle Tageskennzahlen, die in der vollständigen Städteübersicht erscheinen.
# Grenzwerte sind absichtlich explizit (inkl. > bzw. ≥), damit die Werte
# zwischen Detailkarte, API und Tabelle dieselbe Bedeutung haben.
CITY_DAY_INDICATORS = (
    ("hot_days", "Hitzetage", "Tageshöchsttemperatur > 30 °C"),
    ("very_hot_days", "Sehr heiße Tage", "Tageshöchsttemperatur ≥ 35 °C"),
    ("summer_days", "Sommertage", "Tageshöchsttemperatur ≥ 25 °C"),
    ("winter_days", "Wintertage", "Tagestiefsttemperatur < 0 °C"),
    ("ice_days", "Eistage", "Tageshöchsttemperatur < 0 °C"),
    ("tropical_nights", "Tropennächte", "Tagestiefsttemperatur ≥ 20 °C"),
    ("sultry_days", "Schwüle Tage", "mittlerer Taupunkt > 15 °C"),
    ("wet_days", "Regentage", "Tagesniederschlag ≥ 1 mm"),
    ("heavy_rain_days", "Starkregentage", "Tagesniederschlag ≥ 10 mm"),
    ("sunny_days", "Sonnentage", "Sonnenschein > 4 Stunden/Tag"),
)

# Farbskala: wenige Tage (tiefes Blau) -> viele Tage (Rot).
# Sieben Stützstellen; die Farbtöne sind so gewählt, dass die Stufen auch auf
# dunklem Untergrund klar getrennt wirken und die Legende exakt zur Karte passt
# (die Legende liest dieselbe Liste über /api/meta).
COLOR_STOPS = np.array([
    [0.00, 10, 32, 76],      # #0a204c  kaum Wohlfühltage
    [0.15, 22, 78, 150],     # #164e96
    [0.32, 28, 134, 166],    # #1c86a6
    [0.50, 44, 166, 124],    # #2ca67c
    [0.68, 150, 190, 74],    # #96be4a
    [0.84, 240, 166, 46],    # #f0a62e
    [1.00, 214, 54, 44],     # #d6362c
])


@dataclass(frozen=True)
class Params:
    """Filter für „Wohlfühlwetter“ (alle Angaben in °C, Stunden bzw. mm)."""

    tmax_min: float = 18.0
    tmax_max: float = 30.0
    tmin_min: float = 5.0
    tmin_max: float = 20.0
    tdew_max: float = 18.0
    sun_min: float = 3.0
    prec_max: float = 99.0

    def query(self) -> dict[str, float]:
        return {
            "tmax_min": self.tmax_min, "tmax_max": self.tmax_max,
            "tmin_min": self.tmin_min, "tmin_max": self.tmin_max,
            "tdew_max": self.tdew_max, "sun_min": self.sun_min,
            "prec_max": self.prec_max,
        }


# Vorgabe für das Winterquartier-Profil: „milder Winter im Warmen“ – der Klassiker
# (Algarve, Andalusien, Sizilien, Kreta) gegen graue Winter im Norden.
WINTER_DEFAULTS = Params(tmax_min=14.0, tmax_max=26.0, tmin_min=6.0, tmin_max=18.0,
                         tdew_max=17.0, sun_min=4.0, prec_max=99.0)
# Vorgabe für das Sommerfenster: Mai – September (0-basiert).
DEFAULT_SUMMER_MONTHS = (4, 5, 6, 7, 8)


def norm_months(month) -> tuple[int, ...] | None:
    """Monatsangabe → aufsteigende, eindeutige 0-basierte Monatsliste.

    Akzeptiert ``None``/leer (= ganzes Jahr), eine einzelne Monatsnummer (0–11)
    oder eine Liste/Tupel von Monatsnummern. Ein volles Jahr wird zu ``None``
    normalisiert: nur so ist es mit ``slice(None)`` gleichbedeutend und die
    Zwischenspeicher greifen.
    """
    if month is None:
        return None
    if isinstance(month, (int, np.integer)):
        ms: tuple[int, ...] = (int(month),)
    else:
        ms = tuple(int(m) for m in month)
    if not ms:
        return None
    out = tuple(sorted({m % 12 for m in ms}))
    return None if len(out) == 12 else out


def cyclic_window(ms: tuple[int, ...]) -> tuple[int, int] | None:
    """Start/Ende eines zusammenhängenden Monatsfensters – auch über den Jahreswechsel.

    ``(9, 10, 11, 0, 1, 2, 3)`` (Oktober – April) ist zusammenhängend, obwohl die
    sortierte Liste eine Lücke zeigt; ``None`` bedeutet „keine zusammenhängende
    Spanne“ (z. B. Mai, Juli, November).
    """
    if not ms or len(ms) >= 12:
        return None
    members = set(ms)
    starts = [m for m in ms if (m - 1) % 12 not in members]
    if len(starts) != 1:
        return None
    start = starts[0]
    return start, (start + len(ms) - 1) % 12


def months_label(months) -> str:
    """„Juli“, „Mai – September“, „Oktober – April“ bzw. „ganzes Jahr“."""
    ms = norm_months(months)
    if ms is None:
        return "ganzes Jahr"
    if len(ms) == 1:
        return MONTH_NAMES[ms[0]]
    span = cyclic_window(ms)
    if span:
        return f"{MONTH_NAMES[span[0]]} – {MONTH_NAMES[span[1]]}"
    return ", ".join(MONTH_NAMES[m] for m in ms)


def cyclic_months(start: int, length: int) -> tuple[int, ...]:
    """Zusammenhängendes Monatsfenster (0-basiert) ab ``start`` über ``length`` Monate."""
    return tuple((int(start) + i) % 12 for i in range(int(length)))


@dataclass(frozen=True)
class Filter:
    """Was die Karte zeigt – der Normalmodus oder ein Winterquartier-Modus.

    * ``kind='plain'``  – Tage im Monatsfenster ``months`` (``None`` = ganzes Jahr)
    * ``kind='summer'`` – Tage im Sommerfenster ``summer_months`` nach ``p``
    * ``kind='winter'`` – Tage in den übrigen Monaten nach ``winter``
    * ``kind='combo'``  – Summe aus Sommer- und Winterquartier-Tagen (ein Ort,
      den man das ganze Jahr bewohnen kann)

    Alle Felder sind unveränderlich und hashbar: Der Filter dient zugleich als
    Schlüssel des Karten-Caches in ``main.py``.
    """

    kind: str = "plain"
    p: Params = Params()
    winter: Params = WINTER_DEFAULTS
    months: tuple[int, ...] | None = None
    summer_months: tuple[int, ...] = DEFAULT_SUMMER_MONTHS
    period: str = "all"

    @property
    def winter_months(self) -> tuple[int, ...]:
        """Die Monate, die *nicht* zum Sommerfenster gehören."""
        rest = [m for m in range(12) if m not in self.summer_months]
        return tuple(rest)

    @property
    def is_winterquarters(self) -> bool:
        return self.kind != "plain"

    def counts(self, cd: "ClimateData") -> np.ndarray:
        """Tage/Jahr (bzw. Tage im Monatsfenster) je Rasterpunkt."""
        if self.kind == "combo":
            return cd.comfort_counts(self.p, self.winter, self.summer_months, self.period)
        if self.kind == "summer":
            return cd.counts(self.p, self.summer_months, self.period)
        if self.kind == "winter":
            return cd.counts(self.winter, self.winter_months, self.period)
        return cd.counts(self.p, self.months, self.period)

    def days_limit(self, cd: "ClimateData") -> float:
        """Höchstzahl, die die Skala annehmen kann (365,25 bzw. Fensterlänge)."""
        if self.is_winterquarters:
            return 365.25
        return cd.month_limit(self.months, self.period)

    def label(self) -> str:
        """Kurzbezeichnung für Legende/Tooltip, z. B. „Mai – September“."""
        if self.kind == "combo":
            return f"{months_label(self.summer_months)} + {months_label(self.winter_months)}"
        if self.kind == "summer":
            return f"Sommerquartier {months_label(self.summer_months)}"
        if self.kind == "winter":
            return f"Winterquartier {months_label(self.winter_months)}"
        return months_label(self.months)

    def unit_label(self) -> str:
        """Einheit der Kartenwerte („Tage pro Jahr“, „Tage im Juli“, …)."""
        if self.kind == "combo":
            return "Tage/Jahr Wohlfühlklima (Sommer + Winter)"
        if self.kind == "summer":
            return f"Tage im Sommerfenster ({months_label(self.summer_months)})"
        if self.kind == "winter":
            return f"Tage im Winterquartier ({months_label(self.winter_months)})"
        ms = self.months
        if ms is None:
            return "Tage pro Jahr"
        if len(ms) == 1:
            return f"Tage im {MONTH_NAMES[ms[0]]}"
        return f"Tage {months_label(ms)}"

    def single_month(self) -> int | None:
        """0-basierter Monat, wenn genau ein Monat im Normalmodus gewählt ist."""
        if self.kind != "plain" or not self.months or len(self.months) != 1:
            return None
        return int(self.months[0])


# Breite des Gitters, auf dem das Feld interpoliert wird. Das Ergebnis wird
# danach bilinear auf die angeforderte PNG-Breite skaliert – das Feld ist glatt,
# deshalb sieht man davon nichts, spart aber auf kleinen Render-Instanzen
# (0,1 CPU / 512 MB) mehrere hundert Millisekunden pro Anfrage.
FIELD_WIDTH = 640
MAX_WIDTH = 1400
# Am Rand des Datengebiets (z. B. 45° O, 34° N) läuft die Klimafläche über so
# viele Grad weich aus, statt mit einer harten geraden Kante zu enden.
EDGE_FEATHER_DEG = 0.75
# Pixel, die weiter als dieser Betrag (Einheitskugel, ~380 km) von jedem
# Datenpunkt entfernt liegen, gelten als „keine Daten“ (offenes Meer).
FAR_DISTANCE = 0.06


def _recent_five_calendar_years(day_dates: np.ndarray) -> tuple[slice, bool]:
    """Liefert die letzten fünf vollständigen Kalenderjahre und deren Verfügbarkeit."""
    dates = np.asarray(day_dates, dtype="datetime64[D]")
    if dates.size == 0:
        return slice(0, 0), False

    last_date = dates[-1]
    last_year = int(str(last_date)[:4])
    # Ein laufendes/abgeschnittenes letztes Jahr zählt nicht als vollständiges Jahr.
    last_complete_year = last_year if str(last_date)[5:] == "12-31" else last_year - 1
    first_day = np.datetime64(f"{last_complete_year - 4:04d}-01-01", "D")
    after_last_day = np.datetime64(f"{last_complete_year + 1:04d}-01-01", "D")
    start = int(np.searchsorted(dates, first_day, side="left"))
    stop = int(np.searchsorted(dates, after_last_day, side="left"))
    last_day = after_last_day - np.timedelta64(1, "D")
    available = bool(dates[0] <= first_day and last_date >= last_day)

    # Bei einem Datensatz, der vollständig vor dem ermittelten Fünfjahresfenster
    # endet, bleibt die nicht verfügbare Option trotzdem mit echten Grenzen beschreibbar.
    if stop <= start:
        return slice(0, len(dates)), False
    return slice(start, stop), available


class ClimateData:
    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        self.meta = json.loads((data_dir / "climate_meta.json").read_text())
        npz = np.load(data_dir / "climate_grid.npz")
        self.lat = npz["lat"].astype(np.float64)
        self.lon = npz["lon"].astype(np.float64)
        self.raw = {k: npz[k] for k in VARS}
        self.n_points, self.n_days = self.raw["tmax"].shape

        start = np.datetime64(self.meta["start_date"])
        days = start + np.arange(self.n_days)
        self.day_dates = days
        self.day_month = days.astype("datetime64[M]").astype(int) % 12  # 0=Januar
        self.years = self.n_days / 365.25

        # Die Messreihe wird nicht dupliziert: Zeiträume sind nur Ausschnitte
        # derselben täglichen ERA5-Werte. So kann die Fünfjahresansicht auch auf
        # dem kleinen Render-Free-Service ohne zusätzliche Datendateien laufen.
        recent5_slice, self._has_recent5 = _recent_five_calendar_years(days)
        self._period_slices = {
            "all": slice(0, self.n_days),
            "recent5": recent5_slice,
        }
        self._period_years = {
            key: (part.stop - part.start) / 365.25
            for key, part in self._period_slices.items()
        }

        bbox = self.meta["bbox"]
        self.bbox = bbox
        mstep = float(self.meta["land_mask"]["step"])
        shape = tuple(int(v) for v in self.meta["land_mask"]["shape"])
        packed = npz["land_mask_packed"]
        self.land_mask = np.unpackbits(packed)[: shape[0] * shape[1]].reshape(shape)
        self.land_mask = self.land_mask.astype(bool)
        self.mask_step = mstep
        self.mask_soft = gaussian_filter(self.land_mask.astype(np.float32), 1.3)

        self.xyz = self._to_xyz(self.lat, self.lon)
        self.tree = cKDTree(self.xyz)
        self.repaired = self._repair_sentinels()
        self.season_delta = self._season_sign()
        self.projections = None
        self.projection_error = None
        projection_file = Path(os.environ.get("KLIMA_PROJECTIONS_FILE", data_dir / "projections.json"))
        if not projection_file.exists() and not os.environ.get("KLIMA_PROJECTIONS_FILE"):
            projection_file = Path(__file__).parent / "data" / "projections.json"
        try:
            if str(days[0])[5:] != "01-01" or str(days[-1])[5:] != "12-31":
                raise ValueError("Projection analog basis must contain full calendar years")
            self.projections = ProjectionCatalog(projection_file, self.lat, self.lon,
                                                 int(str(days[0])[:4]), int(str(days[-1])[:4]))
        except (OSError, ValueError, KeyError, TypeError) as error:
            # A broken/mismatched manifest never silently enables fake projections.
            self.projection_error = str(error)
        self._counts_cache: dict = {}
        self._annual_summary_cache: dict[str, dict[str, np.ndarray]] = {}
        self._city_climate_cache: dict[str, list[dict]] = {}
        self._alpha_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._idw_cache: "OrderedDict[tuple, tuple]" = OrderedDict()

        # Qualität des Datensatzes (von scripts/check_grid.py geschrieben)
        report = data_dir / "quality_report.json"
        self.quality = json.loads(report.read_text()) if report.exists() else None

        cities_file = data_dir / "cities.json"
        self.cities = json.loads(cities_file.read_text()) if cities_file.exists() else []
        # Suchschlüssel immer frisch bilden (auch für ältere cities.json ohne Umschrift).
        self.city_search_keys = [search_key(c) for c in self.cities]
        if self.cities:
            c_xyz = self._to_xyz(
                np.array([c["lat"] for c in self.cities]),
                np.array([c["lon"] for c in self.cities]),
            )
            self.city_tree = cKDTree(c_xyz)
            dist, self.city_point_idx = self.tree.query(c_xyz, k=1)
            # Chord-Distanz auf der Einheitskugel -> km (für kurze Strecken genau genug)
            self.city_point_km = dist * 6371.0
        else:
            self.city_tree = None
            self.city_point_idx = np.zeros(0, dtype=int)
            self.city_point_km = np.zeros(0)

    @property
    def is_synthetic(self) -> bool:
        """True, wenn das Raster nicht aus echten ERA5-Daten stammt."""
        model = str(self.meta.get("model", "")).lower()
        source = str(self.meta.get("source", "")).lower()
        return model != "era5" or "synthetisch" in source or "synthetic" in source

    def _period_slice(self, period: str = "all") -> slice:
        """Tagesausschnitt für die gewählte historische Bezugsperiode."""
        if self.projections and str(period) in self.projections.records:
            return self._period_slices["all"]  # historical analog sample, not simulated future dates
        try:
            return self._period_slices[str(period)]
        except KeyError as exc:
            raise ValueError("period muss 'all' oder 'recent5' sein") from exc

    def years_in_period(self, period: str = "all") -> float:
        """Länge des Ausschnitts in mittleren Jahren (365,25 Tage)."""
        self._period_slice(period)  # validiert die ID
        return float(self._period_years["all" if self.is_projection(period) else str(period)])

    def period_info(self, period: str = "all") -> dict:
        """Beschriftung und reale Datumsgrenzen einer verfügbaren Zeitreihe."""
        if self.is_projection(period):
            return {**self.projections.records[period], "analog_sample_years": round(self.years, 2)}
        part = self._period_slice(period)
        start_date = str(self.day_dates[part.start])
        end_date = str(self.day_dates[part.stop - 1])
        start_year, end_year = start_date[:4], end_date[:4]
        years = self.years_in_period(period)
        if period == "recent5":
            available = self._has_recent5
            label = (f"Letzte 5 Jahre ({start_year}–{end_year})" if available
                     else f"Letzte 5 Jahre (nicht im Datensatz)")
            short_label = "Letzte 5 Jahre"
            description = (
                f"Mittelwert der jüngsten fünf Kalenderjahre im Datensatz ({start_date} bis {end_date}). "
                "Diese kurze Periode reagiert stärker auf einzelne warme oder kalte Jahre."
                if available else
                "Im vorhandenen Datensatz liegen keine fünf vollständigen Jahre für diesen Vergleich vor."
            )
            summary = (f"Nur {start_year}–{end_year}. Einzelne warme oder kalte Jahre zählen hier stärker."
                       if available else "Nicht im Datensatz.")
        else:
            available = True
            label = f"Heute ({start_year}–{end_year})"
            short_label = "Heute"
            description = (
                f"Mittelwert über alle vorhandenen Tage von {start_date} bis {end_date} "
                f"({years:.1f} Jahre)."
            )
            if years < 30:
                description += " Das ist keine offizielle 30-jährige Klimanormalperiode."
            summary = f"Wetter der Jahre {start_year}–{end_year} (ERA5), Mittel über {years:.0f} Jahre."
        return {
            "id": period,
            "label": label,
            "short_label": short_label,
            "group": "now",
            "summary": summary,
            "start_date": start_date,
            "end_date": end_date,
            "years": round(years, 2),
            "available": available,
            "description": description,
        }

    def is_projection(self, period: str) -> bool:
        return bool(self.projections and period in self.projections.records)

    def period_options(self) -> list[dict]:
        """Historical windows plus validated regional model approximations."""
        options = [self.period_info("all"), self.period_info("recent5")]
        if self.projections:
            for year in ("2030", "2050"):
                for suffix in ("-ssp126", "", "-ssp585"):
                    options.append(self.period_info(year + suffix))
        else:
            for year in ("2030", "2050"):
                options.append({"id": year, "label": f"{year} (nicht verfügbar)",
                                "short_label": year, "group": year,
                                "summary": "Klimamodell-Daten sind gerade nicht verfügbar.",
                                "available": False, "projection": True, "years": None,
                                "description": "Klimamodell-Daten nicht verfügbar oder nicht zur ERA5-Basis passend: "
                                    + str(self.projection_error)})
        return options

    def _raw_for(self, name: str, sel, period: str, point: int | slice | None = None) -> np.ndarray:
        """Raw-unit values; future values are unquantized floats, never int8-clipped."""
        points = slice(None) if point is None else point if isinstance(point, slice) else slice(point, point + 1)
        base = self.raw[name][points, sel]
        if not self.is_projection(period) or name == "sun":
            return base
        months = self.day_month[sel]
        temperature, precipitation = self.projections.tables[period]
        delta = temperature[points][:, months]
        if name in ("tmax", "tmin"):
            return base.astype(np.float32) + 2.0 * delta
        if name == "prec":
            return base.astype(np.float32) * precipitation[points][:, months]
        if name == "tdew":
            from .projections import estimate_dewpoint
            tmean = (self.raw["tmax"][points, sel].astype(np.float32)
                     + self.raw["tmin"][points, sel].astype(np.float32)) / 4.0
            return 2.0 * estimate_dewpoint(tmean, base.astype(np.float32) / 2.0, delta)
        raise ValueError(name)

    def _season_sign(self) -> float:
        """Ø (Juli − Januar) der Tageshöchstwerte im Süden – muss positiv sein.

        Billiger Selbsttest beim Laden: Ein umgekehrter Jahresgang ist das
        sicherste Zeichen für einen kaputten Datensatz (passierte bereits, als
        versehentlich ein Testraster mit invertierter Saison committet wurde).
        """
        south = np.flatnonzero(self.lat < 42.0)
        if south.size == 0:
            return 0.0
        tmax = self.raw["tmax"][south].astype(np.float32) / SCALES["tmax"]
        jul = float(tmax[:, self.day_month == 6].mean())
        jan = float(tmax[:, self.day_month == 0].mean())
        return jul - jan

    @property
    def season_ok(self) -> bool:
        return self.season_delta > 8.0

    @property
    def coverage(self) -> float:
        """Anteil der Punkt-Tage mit echten Werten (1,0 = vollständig)."""
        cov = self.meta.get("coverage")
        if isinstance(cov, dict) and "fraction" in cov:
            return float(cov["fraction"])
        return float((self.raw["tmax"] > -128).mean())


    # ---------------------------------------------------------------- Geometrie
    @staticmethod
    def _to_xyz(lat, lon):
        la = np.radians(np.asarray(lat, dtype=np.float64))
        lo = np.radians(np.asarray(lon, dtype=np.float64))
        return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)

    # ------------------------------------------------------- Lücken im Raster
    def _repair_sentinels(self) -> dict:
        """Ersetzt fehlende Werte (-128) durch den nächsten gültigen Nachbarpunkt.

        Ein unvollständig gebauter Datensatz würde sonst stillschweigend zu
        wenigen Wohlfühl-Tagen führen (fehlende Tage erfüllen kein Kriterium) –
        genau die „unplausiblen“ Werte, die man auf der Karte sieht. Lieber
        sichtbar nachbarschaftsgeschätzt als falsch gezählt.
        """
        info = {"missing_point_days": 0, "filled_from_neighbour": 0, "filled_from_climatology": 0}
        miss_any = np.zeros((self.n_points, self.n_days), dtype=bool)
        for name in VARS:
            miss_any |= (self.raw[name] == -128)
        n_missing = int(miss_any.sum())
        if n_missing == 0:
            return info
        info["missing_point_days"] = n_missing
        _, nbr = self.tree.query(self.xyz, k=min(9, self.n_points))
        nbr = np.atleast_2d(nbr)[:, 1:]
        pi, di = np.nonzero(miss_any)
        for name in VARS:
            arr = self.raw[name]
            need = arr[pi, di] == -128
            for k in range(nbr.shape[1]):
                if not need.any():
                    break
                rows = nbr[pi[need], k]
                cand = arr[rows, di[need]]
                ok = cand != -128
                if not ok.any():
                    continue
                take = np.flatnonzero(need)[ok]
                arr[pi[take], di[take]] = cand[ok]
                need[take] = False
                info["filled_from_neighbour"] += int(ok.sum())
            if need.any():
                # Kein Nachbar hat Daten für diesen Tag: Monatsklimatologie des Punkts
                rest = np.flatnonzero(need)
                months = self.day_month[di[rest]]
                for m in range(12):
                    sel = rest[months == m]
                    if sel.size == 0:
                        continue
                    same = np.flatnonzero(self.day_month == m)
                    good = arr[pi[sel][:, None], same[None, :]]
                    good = good[good != -128]
                    fill = int(np.rint(good.mean())) if good.size else 0
                    arr[pi[sel], di[sel]] = fill
                info["filled_from_climatology"] += int(rest.size)
        return info

    # ------------------------------------------------------------- Zähl-Kern
    @staticmethod
    def _q(value: float, scale: float, mode: str) -> int:
        """Schwellwert in den quantisierten Wertebereich übertragen."""
        x = value * scale
        if mode == "min":           # Wert >= value  <=>  q >= ceil(x)
            return int(math.ceil(x - 1e-9))
        if mode == "max":           # Wert <= value  <=>  q <= floor(x)
            return int(math.floor(x + 1e-9))
        raise ValueError(mode)

    def days_mask(self, p: Params, month=None, point: int | slice | None = None,
                  period: str = "all") -> np.ndarray:
        """Boolesche Matrix (Punkte, Tage) bzw. (Tage,) für einen Einzelpunkt.

        ``month`` darf ``None`` (ganzes Jahr), eine Monatsnummer (0–11) oder eine
        Liste von Monaten sein – siehe :func:`norm_months`. ``period`` wählt den
        gesamten Datensatz oder dessen jüngste fünf Kalenderjahre.
        """
        if self.is_projection(period) and point is None:
            # Bound peak memory on small hosting plans: dewpoint estimation
            # otherwise creates several full P×D float arrays at once.
            return np.concatenate([
                self.days_mask(p, month, slice(start, min(start + 128, self.n_points)), period)
                for start in range(0, self.n_points, 128)
            ], axis=0)
        sel = self._day_sel(month, period)

        def get(name):
            return self._raw_for(name, sel, period, point)

        # Model-shifted values are floats. Rounding thresholds would reintroduce
        # the historical 0.5-degree quantization error into projected comparisons.
        q = (lambda value, scale, mode: value * scale) if self.is_projection(period) else self._q

        ok = (get("tmax") >= q(p.tmax_min, SCALES["tmax"], "min"))
        ok &= (get("tmax") <= q(p.tmax_max, SCALES["tmax"], "max"))
        ok &= (get("tmin") >= q(p.tmin_min, SCALES["tmin"], "min"))
        ok &= (get("tmin") <= q(p.tmin_max, SCALES["tmin"], "max"))
        ok &= (get("tdew") <= q(p.tdew_max, SCALES["tdew"], "max"))
        # sun_min wird in Stunden angegeben, die Rohdaten in Sekunden
        ok &= (get("sun") >= q(p.sun_min * SUN_SECONDS_PER_HOUR, SCALES["sun"], "min"))
        if p.prec_max < 90:
            ok &= (get("prec") <= q(p.prec_max, SCALES["prec"], "max"))
        return ok

    def _day_sel(self, month=None, period: str = "all") -> slice | np.ndarray:
        """Tagesindex für ein Monatsfenster innerhalb der gewählten Periode."""
        part = self._period_slice(period)
        ms = norm_months(month)
        if ms is None:
            return part
        selected_months = self.day_month[part]
        if len(ms) == 1:
            return np.flatnonzero(selected_months == ms[0]) + part.start
        return np.flatnonzero(np.isin(selected_months, ms)) + part.start

    def counts(self, p: Params, month=None, period: str = "all") -> np.ndarray:
        """Mittlere Tage pro Jahr bzw. im Monatsfenster je Rasterpunkt."""
        key = (p, norm_months(month), str(period))
        hit = self._counts_cache.get(key)
        if hit is None:
            hit = (self.days_mask(p, month, period=period).sum(axis=1).astype(np.float64)
                   / self.years_in_period(period))
            if len(self._counts_cache) > 400:
                self._counts_cache.clear()
            self._counts_cache[key] = hit
        return hit

    def monthly_counts(self, p: Params, period: str = "all") -> np.ndarray:
        """(12, Punkte) – Tage je Kalendermonat im gewählten Zeitraum."""
        out = np.zeros((12, self.n_points), dtype=np.float64)
        for m in range(12):
            out[m] = self.counts(p, month=m, period=period)
        return out

    def month_limit(self, month=None, period: str = "all") -> float:
        """Maximal erreichbare Tageszahl im Zeitraum (365,25 im Jahresmodus)."""
        ms = norm_months(month)
        if ms is None:
            return 365.25
        part = self._period_slice(period)
        return float(np.isin(self.day_month[part], ms).sum() / self.years_in_period(period))

    def comfort_counts(self, summer: Params, winter: Params, summer_months=None,
                       period: str = "all") -> np.ndarray:
        """Wohlfühl-Tage/Jahr an *einem* Ort mit Sommer- und Winterquartier-Profil.

        Sommer- und Winterfenster sind überschneidungsfrei, deshalb ist die Summe
        beider Zählungen die Zahl der Tage im Jahr, an denen an diesem Ort *eines*
        der beiden Wunschwetter herrscht – die Kennzahl für die Frage „Welcher Ort
        trägt mich das ganze Jahr?“.
        """
        ms = norm_months(summer_months)
        if ms is None:
            ms = DEFAULT_SUMMER_MONTHS
        rest = tuple(m for m in range(12) if m not in ms)
        return self.counts(summer, ms, period) + self.counts(winter, rest, period)

    def point_monthly(self, p: Params, idx: int, period: str = "all") -> np.ndarray:
        """(12,) – mittlere Wohlfühl-Tage je Monat an einem Rasterpunkt."""
        out = np.empty(12, dtype=np.float64)
        years = self.years_in_period(period)
        for m in range(12):
            out[m] = self.days_mask(p, month=m, point=int(idx), period=period)[0].sum() / years
        return out

    # --------------------------------------------------------------- Rendering
    def _pixel_grid(self, width: int) -> tuple[np.ndarray, int, int]:
        """Pixelkoordinaten in Web-Mercator + zugehörige Lat/Lon-Werte."""
        bbox = self.bbox
        lon_min, lon_max = bbox["lon_min"], bbox["lon_max"]
        lat_min, lat_max = bbox["lat_min"], bbox["lat_max"]

        def merc_y(lat):
            return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

        y_min, y_max = merc_y(lat_min), merc_y(lat_max)
        x_span = math.radians(lon_max - lon_min)
        height = max(2, int(round(width * (y_max - y_min) / x_span)))
        xs = np.linspace(math.radians(lon_min), math.radians(lon_max), width)
        ys = np.linspace(y_max, y_min, height)
        lon2, lat2 = np.meshgrid(np.degrees(xs), np.degrees(2 * np.arctan(np.exp(ys)) - math.pi / 2))
        return np.stack([lat2, lon2], axis=-1), height, width

    def _idw_weights(self, width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
        """Nachbarn und Gewichte der IDW-Interpolation für ein Pixelgitter.

        Hängt nur von der Gitterbreite ab, **nicht** von den Filterparametern –
        deshalb wird das Ergebnis zwischengespeichert. Die KD-Tree-Suche ist der
        mit Abstand teuerste Teil des Renderings; ohne Cache kostet jede
        Parameteränderung auf einer kleinen Instanz schnell eine Sekunde.
        """
        key = int(width)
        hit = self._idw_cache.get(key)
        if hit is not None:
            self._idw_cache.move_to_end(key)
            return hit
        ll, height, width = self._pixel_grid(key)
        lat_px = ll[..., 0].reshape(-1)
        lon_px = ll[..., 1].reshape(-1)
        k = int(min(8, self.n_points))
        n = lat_px.size
        idx = np.empty((n, k), dtype=np.int32)
        wgt = np.empty((n, k), dtype=np.float32)
        far = np.empty(n, dtype=bool)
        block = 200_000
        for start in range(0, n, block):
            stop = min(start + block, n)
            q = self._to_xyz(lat_px[start:stop], lon_px[start:stop]).astype(np.float64)
            dist, ii = self.tree.query(q, k=k, workers=-1)
            dist = np.maximum(dist, 1e-9)
            w = (1.0 / dist ** 3.0).astype(np.float32)
            w /= w.sum(axis=1, keepdims=True)
            idx[start:stop] = ii
            wgt[start:stop] = w
            far[start:stop] = dist[:, 0] > FAR_DISTANCE
        out = (idx, wgt, far, height, width)
        self._idw_cache[key] = out
        while len(self._idw_cache) > 2:      # ~25 MB je Eintrag
            self._idw_cache.popitem(last=False)
        return out

    def _field(self, counts: np.ndarray, width: int) -> tuple[np.ndarray, int, int]:
        """Interpoliertes Tageszahl-Feld (float32) auf einem Pixelgitter."""
        idx, wgt, far, height, width = self._idw_weights(width)
        cf = counts.astype(np.float32)
        vals = (wgt * cf[idx]).sum(axis=1, dtype=np.float32)
        field = vals.reshape(height, width)
        if far.any():
            # Offenes Meer / weit weg von jedem Datenpunkt: neutraler Wert,
            # später über die Landmaske wieder ausgeblendet.
            floor = float(np.nanmin(counts)) if np.isfinite(counts).any() else 0.0
            field = np.where(far.reshape(height, width), np.float32(floor), field)
        return gaussian_filter(field, 1.0), height, width

    @staticmethod
    def _to_rgb(norm: np.ndarray) -> np.ndarray:
        rgb = np.empty(norm.shape + (3,), dtype=np.uint8)
        for ch in range(3):
            rgb[..., ch] = np.interp(norm, COLOR_STOPS[:, 0], COLOR_STOPS[:, 1 + ch]).astype(np.uint8)
        return rgb

    def render(self, p: Params, month: int | None = None, width: int = 1100,
               fixed_scale: bool = False) -> tuple[bytes, dict]:
        """Erzeugt das Kartenbild (PNG, Web-Mercator) und Statistikdaten."""
        return self.render_counts(self.counts(p, month), width=width,
                                  fixed_scale=fixed_scale, limit=self.month_limit(month),
                                  meta={"months": norm_months(month)})

    def render_counts(self, counts: np.ndarray, width: int = 1100, fixed_scale: bool = False,
                      limit: float | None = None, meta: dict | None = None) -> tuple[bytes, dict]:
        """Kartenbild aus einer beliebigen Tageszahl-Folge.

        Getrennt von :meth:`render`, weil der Winterquartier-Modus seine Zahlen
        aus zwei Profilen zusammensetzt (siehe :meth:`comfort_counts`) und der
        Filter selbst nur die Feldwerte liefert.
        """
        width = int(max(320, min(MAX_WIDTH, width)))
        if limit is None:
            limit = self.month_limit(None)

        # Das Feld wird auf einem festen, gröberen Gitter interpoliert und erst
        # danach (bilinear, in C) auf die Zielbreite skaliert; die Landmaske wird
        # weiter in voller Auflösung ausgewertet, die Küsten bleiben also scharf.
        fwidth = min(FIELD_WIDTH, width)
        field, fheight, fwidth = self._field(counts, fwidth)
        ll, height, width = self._pixel_grid(width)

        vmax_data = float(np.nanmax(counts)) if np.isfinite(counts).any() else 1.0
        if fixed_scale:
            vmax = limit
        else:
            p99 = float(np.nanpercentile(counts, 99))
            vmax = min(max(p99, vmax_data * 0.5, 1.0), limit)
        vmin = 0.0

        norm = np.clip(field / max(vmax - vmin, 1e-9), 0.0, 1.0)
        coarse = Image.fromarray(self._to_rgb(norm), mode="RGB")
        if (fwidth, fheight) != (width, height):
            coarse = coarse.resize((width, height), Image.BILINEAR)
        rgb = np.asarray(coarse)

        alpha = self._alpha_for(ll, (height, width))
        rgba = np.dstack([rgb, alpha])
        img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=6)

        info = {
            "vmin": round(vmin, 1),
            "vmax": round(vmax, 1),
            "data_max": round(vmax_data, 1),
            "limit": round(limit, 1),
            "fixed_scale": bool(fixed_scale),
            "width": width,
            "height": height,
            "bounds": [[self.bbox["lat_min"], self.bbox["lon_min"]],
                       [self.bbox["lat_max"], self.bbox["lon_max"]]],
            "mean": round(float(np.nanmean(counts)), 1),
            "n_points": int(self.n_points),
            "values": np.rint(counts).astype(int).tolist(),
        }
        info.update(meta or {})
        return buf.getvalue(), info

    def _alpha_for(self, ll: np.ndarray, size: tuple[int, int] | None = None) -> np.ndarray:
        """Deckkraft (0..255) aus der Landmaske – bilinear abgetastet, weiche Küsten.

        Wichtig: die Maske wird *geglättet* und bilinear ausgewertet. Ein
        Nearest-Neighbour-Zugriff würde bei 0,05°-Maske und ~0,06°/Pixel ein
        unruhiges Punktmuster (Aliasing) erzeugen.
        """
        if size is not None:
            cached = self._alpha_cache.get(size)
            if cached is not None:
                self._alpha_cache.move_to_end(size)
                return cached
        alpha = self._alpha_calc(ll)
        if size is not None:
            self._alpha_cache[size] = alpha
            while len(self._alpha_cache) > 3:
                self._alpha_cache.popitem(last=False)
        return alpha

    def _alpha_calc(self, ll: np.ndarray) -> np.ndarray:
        from scipy.ndimage import map_coordinates

        bbox = self.bbox
        iy = (ll[..., 0] - bbox["lat_min"]) / self.mask_step
        ix = (ll[..., 1] - bbox["lon_min"]) / self.mask_step
        soft = map_coordinates(self.mask_soft, [iy, ix], order=1, mode="constant", cval=0.0)
        # Kontrastkurve: >0,8 wird voll deckend, <0,25 ganz transparent
        soft = np.clip((soft - 0.25) / 0.55, 0.0, 1.0)
        # Weicher Rand am Ende des Datengebiets statt einer geraden Kante quer durchs Land.
        edge = np.minimum.reduce([ll[..., 0] - bbox["lat_min"], bbox["lat_max"] - ll[..., 0],
                                  ll[..., 1] - bbox["lon_min"], bbox["lon_max"] - ll[..., 1]])
        fade = np.clip(edge / EDGE_FEATHER_DEG, 0.0, 1.0)
        soft = soft * fade * fade * (3.0 - 2.0 * fade)
        return np.round(soft * 255.0).astype(np.uint8)

    # ------------------------------------------------------------ Punktabfragen
    def point_index(self, lat: float, lon: float) -> int:
        _, idx = self.tree.query(self._to_xyz(np.array([lat]), np.array([lon]))[0], k=1)
        return int(idx)

    def nearest_city(self, lat: float, lon: float, k: int = 10) -> dict | None:
        """Nächstgelegener *relevanter* Ort.

        Reine Distanz würde in Großstädten Stadtteile statt der Stadt liefern
        (GeoNames führt z. B. „City Center“ in Madrid). Deshalb werden die k
        nächsten Kandidaten nach Einwohnerzahl und Entfernung gewichtet.
        """
        if self.city_tree is None:
            return None
        k = int(min(k, len(self.cities)))
        dist, idxs = self.city_tree.query(self._to_xyz(np.array([lat]), np.array([lon]))[0], k=k)
        dist = np.atleast_1d(dist) * 6371.0
        idxs = np.atleast_1d(idxs)
        best, best_score, best_km = None, -1.0, 0.0
        for d, i in zip(dist, idxs):
            c = self.cities[int(i)]
            km = float(d)
            score = float(max(int(c.get("pop", 0)), 1)) / (1.0 + km / 6.0) ** 3
            if score > best_score:
                best, best_score, best_km = c, score, km
        if best is None:
            return None
        return {"name": best["name"], "country": best.get("country"),
                "name_de": best.get("name_de", ""), "pop": int(best.get("pop", 0)),
                "lat": best["lat"], "lon": best["lon"], "dist_km": round(best_km, 1)}

    def _monthly(self, values: np.ndarray, total: bool = False,
                 period: str = "all") -> list[float]:
        """12 Monatswerte des Zeitraums: Mittel je Tag oder Summe je Jahr."""
        months = self.day_month[self._period_slice(period)]
        acc = np.bincount(months, weights=values.astype(np.float64), minlength=12)
        div = (self.years_in_period(period) if total
               else np.bincount(months, minlength=12).astype(np.float64))
        return [round(float(v), 2) for v in (acc / np.maximum(div, 1e-9))]

    def _annual_summary(self, period: str = "all") -> dict[str, np.ndarray]:
        """Vektorisierte Jahresmittel und Tageszahlen für alle Rasterpunkte.

        Werte bleiben in der quantisierten Rohdarstellung, bis eine Kennzahl
        berechnet wird. Dadurch genügt ein kleiner gecachter Ergebnisvektor je
        Messgröße statt zusätzlicher Float-Kopien des ganzen Rasters.
        """
        period = str(period)
        if period in self._annual_summary_cache:
            return self._annual_summary_cache[period]

        part = self._period_slice(period)
        years = self.years_in_period(period)
        summary: dict[str, np.ndarray] = {}
        # Blockwise means avoid retaining four full future float fields.
        for start in range(0, self.n_points, 128):
            stop = min(start + 128, self.n_points)
            points = slice(start, stop)
            tmax = self._raw_for("tmax", part, period, points)
            tmin = self._raw_for("tmin", part, period, points)
            tdew = self._raw_for("tdew", part, period, points)
            sun = self._raw_for("sun", part, period, points)
            prec = self._raw_for("prec", part, period, points)

            def days(mask: np.ndarray) -> np.ndarray:
                return np.count_nonzero(mask, axis=1).astype(np.float64) / years

            block = {
                # Temperatur-/Taupunkt-Rohwerte haben 0,5-°C-Schritte.
                "tmax": tmax.mean(axis=1) / 2.0,
                "tmin": tmin.mean(axis=1) / 2.0,
                "tdew": tdew.mean(axis=1) / 2.0,
                # Ein Rohwert Sonne entspricht 15 Minuten; Regen ist in 0,5-mm-Schritten.
                "sun": sun.mean(axis=1) / 4.0,
                "prec": prec.sum(axis=1, dtype=np.float64) / (2.0 * years),
                # 0,5 °C und 0,5 mm Quantisierung; Sonne ist in Viertelstunden codiert.
                "frost_days": days(tmin <= 0),
                "winter_days": days(tmin < 0),
                "ice_days": days(tmax < 0),
                "summer_days": days(tmax >= 50),
                "hot_days": days(tmax > 60),
                "very_hot_days": days(tmax >= 70),
                "tropical_nights": days(tmin >= 40),
                "sultry_days": days(tdew > 30),
                "wet_days": days(prec >= 2),
                "heavy_rain_days": days(prec >= 20),
                "sunny_days": days(sun > 16),
            }
            for key, values in block.items():
                if key not in summary:
                    summary[key] = np.empty(self.n_points, dtype=np.float64)
                summary[key][start:stop] = values

        self._annual_summary_cache[period] = summary
        return summary

    def annual_climatology(self, idx: int, period: str = "all") -> dict:
        """Jahresmittel und mittlere jährliche Ereignistage eines Rasterpunkts."""
        summary = self._annual_summary(period)
        return {
            "year": {
                "tmax": round(float(summary["tmax"][idx]), 1),
                "tmin": round(float(summary["tmin"][idx]), 1),
                "tdew": round(float(summary["tdew"][idx]), 1),
                "sun": round(float(summary["sun"][idx]), 2),
                "prec": round(float(summary["prec"][idx]), 0),
            },
            **{key: round(float(summary[key][idx]), 1)
               for key, _label, _threshold in CITY_DAY_INDICATORS},
            # Rückwärtskompatibel für die bisherige Ortsdetailkarte.
            "frost_days": round(float(summary["frost_days"][idx]), 1),
        }

    def climatology(self, idx: int, period: str = "all") -> dict:
        """Monats- und Jahresklima eines Rasterpunkts (°C, h/Tag, mm)."""
        part = self._period_slice(period)
        vals = {name: self._raw_for(name, part, period, idx)[0].astype(np.float64) * UNIT_FACTORS[name]
                for name in VARS}
        return {
            "months": {name: self._monthly(vals[name], total=(name == "prec"), period=period)
                       for name in VARS},
            **self.annual_climatology(idx, period),
            **({"projection": self.projections.point_info(period, idx)} if self.is_projection(period) else {}),
        }

    def city_climate_rows(self, period: str = "all") -> list[dict]:
        """Jahresklima aller Städte, am jeweils nächsten ERA5-Gitterpunkt."""
        if period in self._city_climate_cache:
            return self._city_climate_cache[period]
        rows: list[dict] = []
        if not self.cities:
            self._city_climate_cache[period] = rows
            return rows

        summary = self._annual_summary(period)
        for i, city in enumerate(self.cities):
            point = int(self.city_point_idx[i])
            climate = {
                "year": {
                    "tmax": round(float(summary["tmax"][point]), 1),
                    "tmin": round(float(summary["tmin"][point]), 1),
                    "tdew": round(float(summary["tdew"][point]), 1),
                    "sun": round(float(summary["sun"][point]), 2),
                    "prec": round(float(summary["prec"][point]), 0),
                },
                **{key: round(float(summary[key][point]), 1)
                   for key, _label, _threshold in CITY_DAY_INDICATORS},
            }
            rows.append({
                "name": city["name"],
                "country": city.get("name_de", city.get("country", "")),
                "country_en": city.get("country", ""),
                "aliases": [*(city.get("aliases") or []),
                            *([city["name_local"]] if city.get("name_local") else [])],
                "lat": float(city["lat"]),
                "lon": float(city["lon"]),
                "pop": int(city.get("pop", 0)),
                "grid_distance_km": round(float(self.city_point_km[i]), 1),
                "climate": climate,
            })
        self._city_climate_cache[period] = rows
        return rows

    def point_report(self, lat: float, lon: float, p: Params, month=None,
                     period: str = "all") -> dict:
        """Alles, was die Detailkarte zu einem Ort zeigt.

        ``month`` ist der Vergleichszeitraum für ``days_here``/``percentile`` –
        ``None`` (Jahr), eine Monatsnummer oder ein Monatsfenster.
        """
        idx = self.point_index(lat, lon)
        part = self._period_slice(period)
        days = self.days_mask(p, point=idx, period=period)[0]
        selected_months = self.day_month[part]
        per_month = [round(float(days[selected_months == m].sum() / self.years_in_period(period)), 1)
                     for m in range(12)]
        yearly = round(sum(per_month), 1)

        counts = self.counts(p, month, period)
        own = float(counts[idx])
        percentile = round(float((counts <= own).mean()) * 100.0, 1)
        best_idx = int(np.argmax(counts))
        best = {
            "days": round(float(counts[best_idx]), 1),
            "lat": round(float(self.lat[best_idx]), 2),
            "lon": round(float(self.lon[best_idx]), 2),
        }
        best_city = self.nearest_city(float(self.lat[best_idx]), float(self.lon[best_idx]))
        if best_city:
            best["place"] = best_city["name"]
            best["country"] = best_city["name_de"]

        rep = {
            "lat": float(self.lat[idx]), "lon": float(self.lon[idx]),
            "query_lat": lat, "query_lon": lon,
            "days_per_year": yearly,
            "days_per_month": per_month,
            "grid_distance_deg": round(float(np.hypot(self.lat[idx] - lat, self.lon[idx] - lon)), 2),
            "grid_distance_km": round(float(111.0 * np.hypot(self.lat[idx] - lat,
                                                             (self.lon[idx] - lon) * math.cos(math.radians(lat))))),
            "city": self.nearest_city(lat, lon),
            "climate": self.climatology(idx, period),
            "days_here": round(own, 1),
            "percentile": percentile,
            "europe_best": best,
            "params": p.query(),
            "month": month if isinstance(month, int) else None,
            "months": list(norm_months(month)) if norm_months(month) else None,
            "month_limit": round(self.month_limit(month, period), 1),
            "limit": round(self.month_limit(month, period), 1),
            "period": self.period_info(period),
        }
        if isinstance(month, (int, np.integer)):
            rep["selected_month"] = int(month)
            rep["days_selected_month"] = per_month[int(month)]
        return rep

    def winterquarters_point(self, lat: float, lon: float, f: Filter) -> dict:
        """Sommer-, Winter- und Kombinations-Tage an einem Ort (Winterquartier-Modus)."""
        idx = self.point_index(lat, lon)
        sm = self.point_monthly(f.p, idx, f.period)
        wm = self.point_monthly(f.winter, idx, f.period)
        sm_months = list(f.summer_months)
        wm_months = list(f.winter_months)
        summer = float(sm[sm_months].sum())
        winter = float(wm[wm_months].sum())

        # Einordnung an der Kombinations-Kennzahl (gleiche Zahlen wie die Karte)
        counts = f.counts(self)
        own = summer + winter
        best_idx = int(np.argmax(counts))
        best = {"days": round(float(counts[best_idx]), 1),
                "lat": round(float(self.lat[best_idx]), 2),
                "lon": round(float(self.lon[best_idx]), 2)}
        best_city = self.nearest_city(float(self.lat[best_idx]), float(self.lon[best_idx]))
        if best_city:
            best["place"] = best_city["name"]
            best["country"] = best_city["name_de"] or ""
        return {
            "percentile": round(float((counts <= own).mean()) * 100.0, 1),
            "europe_best": best,
            "summer_days": round(summer, 1),
            "winter_days": round(winter, 1),
            "total_days": round(summer + winter, 1),
            "summer_months": sm_months,
            "winter_months": wm_months,
            "summer_label": months_label(f.summer_months),
            "winter_label": months_label(f.winter_months),
            # Was übers Jahr möglich wäre, wenn das jeweilige Wetter überall gälte
            "summer_year_days": round(float(sm.sum()), 1),
            "winter_year_days": round(float(wm.sum()), 1),
            "summer_monthly": [round(float(v), 1) for v in sm],
            "winter_monthly": [round(float(v), 1) for v in wm],
            "params": f.p.query(),
            "winter_params": f.winter.query(),
        }

    # ------------------------------------------------- Winterquartier-Auswertung
    @staticmethod
    def month_windows(min_len: int = 1, max_len: int = 11) -> list[tuple[int, int]]:
        """Alle zusammenhängenden Monatsfenster als (Startmonat, Länge).

        Ein Fenster darf über den Jahreswechsel laufen (November – März), deshalb
        wird zyklisch gedacht: Start 0–11, Länge ``min_len``–``max_len`` (höchstens
        11, damit beide Quartiere mindestens einen Monat behalten).
        """
        lo = max(1, int(min_len))
        hi = min(11, max(lo, int(max_len)))
        return [(start, length) for length in range(lo, hi + 1) for start in range(12)]

    @staticmethod
    def _window_sums(delta: np.ndarray, min_len: int, max_len: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """Fenstersummen über ``delta`` (12, P) für alle zyklischen Monatsfenster.

        Rückgabe: ``(S, P)``-Matrix der Summen und die zugehörige Fensterliste.
        Über die doppelte Jahreskette (24 Monate) lässt sich jedes Fenster als
        Differenz zweier Präfixsummen ziehen – das macht die Optimierung über
        alle Aufteilungen billig (eine Vektoroperation statt 24 Zählungen).
        """
        windows = ClimateData.month_windows(min_len, max_len)
        doubled = np.concatenate([delta, delta], axis=0)
        prefix = np.vstack([np.zeros((1, delta.shape[1])), np.cumsum(doubled, axis=0)])
        sums = np.stack([prefix[s + length] - prefix[s] for s, length in windows])
        return sums, windows

    def _window_report(self, values: np.ndarray, windows: list[tuple[int, int]],
                       idx_windows: list[int], limit: int) -> list[dict]:
        """Beschreibt ausgewählte Fenster: Mittelwert, Bestwert (Ort) und beste Stadt."""
        city_counts = None
        city_points = None
        if self.cities and self.city_point_idx.size:
            city_points = self.city_point_idx
            city_counts = values[:, city_points]
        rows: list[dict] = []
        for w in idx_windows[:limit]:
            start, length = windows[w]
            months = cyclic_months(start, length)
            row_values = values[w]
            best_idx = int(np.argmax(row_values))
            best = {
                "days": round(float(row_values[best_idx]), 1),
                "lat": round(float(self.lat[best_idx]), 2),
                "lon": round(float(self.lon[best_idx]), 2),
            }
            city = self.nearest_city(float(self.lat[best_idx]), float(self.lon[best_idx]))
            if city:
                best["place"] = city["name"]
                best["country"] = city["name_de"] or ""
                best["city_km"] = city["dist_km"]
            row = {
                "start": start, "length": length, "months": list(months),
                "summer_label": months_label(months),
                "winter_label": months_label(tuple(m for m in range(12) if m not in months)),
                "mean_days": round(float(row_values.mean()), 1),
                "best": best,
            }
            if city_counts is not None:
                ci = int(np.argmax(city_counts[w]))
                row["best_city"] = {
                    "name": self.cities[ci]["name"],
                    "country": self.cities[ci].get("name_de", ""),
                    "lat": float(self.cities[ci]["lat"]), "lon": float(self.cities[ci]["lon"]),
                    "days": round(float(city_counts[w][ci]), 1),
                }
            rows.append(row)
        return rows

    def season_split_report(self, summer: Params, winter: Params, min_len: int = 3,
                            max_len: int = 9, limit: int = 8,
                            period: str = "all") -> dict:
        """Beste Aufteilung des Jahres in Sommer- und Winterquartier-Monate.

        Für jeden Ort und jede zusammenhängende Aufteilung (Sommerfenster der
        Länge ``min_len``–``max_len`` Monate, Winterquartier ist der Rest) wird
        gezählt, wie viele Wohlfühl-Tage dabei im Jahr zusammenkommen – Sommer
        nach ``summer``, Winter nach ``winter``. Gesucht ist die Kombination aus
        Aufteilung **und** Ort mit der höchsten Summe.
        """
        sm = self.monthly_counts(summer, period)      # (12, P)
        wm = self.monthly_counts(winter, period)
        sums, windows = self._window_sums(sm - wm, min_len, max_len)
        values = wm.sum(axis=0)[None, :] + sums       # (S, P) Tage/Jahr
        mean = values.mean(axis=1)
        best_points = values.max(axis=1)
        order = np.argsort(-np.round(best_points, 3))[:max(1, int(limit))]
        rows = self._window_report(values, windows, [int(i) for i in order], limit)

        # Punktweises Optimum (unabhängig davon, ob eine Aufteilung überall passt)
        per_point = values.argmax(axis=0)
        per_point_days = values[per_point, np.arange(values.shape[1])]
        idx_best = int(np.argmax(per_point_days))
        return {
            "period": self.period_info(period),
            "window_range": {"min_months": int(min_len), "max_months": int(max_len)},
            "summer_params": summer.query(),
            "winter_params": winter.query(),
            "candidates": rows,
            "best": rows[0] if rows else None,
            "point_optimum": {
                "days": round(float(per_point_days[idx_best]), 1),
                "lat": round(float(self.lat[idx_best]), 2),
                "lon": round(float(self.lon[idx_best]), 2),
                "summer_months": list(cyclic_months(*windows[int(per_point[idx_best])])),
                "mean_days": round(float(per_point_days.mean()), 1),
            },
            "city_optimum": self._best_city(per_point_days, windows, per_point),
        }

    def _best_city(self, per_point_days: np.ndarray, windows: list[tuple[int, int]],
                   per_point: np.ndarray) -> dict | None:
        """Beste Stadt im punktweisen Optimum (dort darf die Aufteilung variieren)."""
        if not self.cities or not self.city_point_idx.size:
            return None
        city_days = per_point_days[self.city_point_idx]
        ci = int(np.argmax(city_days))
        start, length = windows[int(per_point[self.city_point_idx[ci]])]
        months = cyclic_months(start, length)
        return {
            "name": self.cities[ci]["name"], "country": self.cities[ci].get("name_de", ""),
            "lat": float(self.cities[ci]["lat"]), "lon": float(self.cities[ci]["lon"]),
            "days": round(float(city_days[ci]), 1),
            "summer_months": list(months),
            "summer_label": months_label(months),
        }

    def pair_report(self, lat: float, lon: float, role: str, summer: Params, winter: Params,
                    summer_months=None, limit: int = 8, max_km: float | None = None,
                    min_len: int = 3, max_len: int = 9,
                    period: str = "all") -> dict:
        """Bestes Gegenstück zu einem festen Quartier (Zwei-Orte-Kombination).

        ``role`` sagt, was der gewählte Ort *ist*: ``'summer'`` (dann wird das
        Winterquartier gesucht) oder ``'winter'`` (dann das Sommerquartier).
        Für jeden Kandidaten werden die Wohlfühl-Tage im gesuchten Zeitraum
        gezählt und die Summe mit dem eigenen Quartier gebildet – inklusive der
        für das Paar besten Aufteilung der Monate.
        """
        role = "winter" if str(role).lower().startswith("w") else "summer"
        ms = norm_months(summer_months) or DEFAULT_SUMMER_MONTHS
        rest = tuple(m for m in range(12) if m not in ms)
        cd_want = winter if role == "summer" else summer      # Profil des Gegenstücks
        months_want = rest if role == "summer" else ms        # dessen Zeitraum
        want_name = "winter" if role == "summer" else "summer"
        want_label = "Winterquartier" if role == "summer" else "Sommerquartier"

        anchor = self.point_index(lat, lon)
        sm_a = self.point_monthly(summer, anchor, period)
        wm_a = self.point_monthly(winter, anchor, period)
        # Eigene Wohlfühl-Tage des Ankers in *seinem* Quartier-Fenster
        own_days = (float(sm_a[list(ms)].sum()) if role == "summer"
                    else float(wm_a[list(rest)].sum()))

        counts = self.counts(cd_want, months_want, period)
        city_days = counts[self.city_point_idx] if self.city_point_idx.size else np.zeros(0)
        order = np.argsort(-city_days)[: max(limit * 6, 40)]
        picked: list[tuple[float, float]] = []
        out: list[dict] = []
        for i in order:
            i = int(i)
            c = self.cities[i]
            klat, klon = float(c["lat"]), float(c["lon"])
            km = float(self.city_point_km[i])
            if max_km is not None and km > float(max_km):
                continue
            if any(abs(klat - a) < 0.35
                   and abs((klon - b) * math.cos(math.radians(klat))) < 0.35 for a, b in picked):
                continue
            picked.append((klat, klon))
            # Monatswerte des Kandidaten für beide Profile (jeweils 12 kleine Zählungen)
            sm_b = self.point_monthly(summer, int(self.city_point_idx[i]), period)
            wm_b = self.point_monthly(winter, int(self.city_point_idx[i]), period)
            if role == "summer":      # Anker = Sommer, Kandidat = Winter
                base = float(wm_b.sum())
                delta = sm_a - wm_b
            else:                     # Anker = Winter, Kandidat = Sommer
                base = float(sm_b.sum())
                delta = wm_a - sm_b
            sums, windows = self._window_sums(delta[:, None], min_len, max_len)
            w = int(np.argmax(sums[:, 0]))
            start, length = windows[w]
            best_months = cyclic_months(start, length)
            total = base + float(sums[w, 0])
            own_in_window = (float(sm_a[list(best_months)].sum()) if role == "summer"
                             else float(wm_a[list(best_months)].sum()))
            other_outside = total - own_in_window
            out.append({
                "name": c["name"], "country": c.get("name_de", ""),
                "lat": klat, "lon": klon, "pop": int(c.get("pop", 0)),
                "days": round(float(city_days[i]), 1),         # Tage im gesuchten Quartier
                "own_days": round(own_in_window, 1),
                "counterpart_days": round(other_outside, 1),
                "total_days": round(total, 1),
                "km": round(km),
                "summer_months": list(best_months),
                "summer_label": months_label(best_months),
                "winter_label": months_label(tuple(m for m in range(12) if m not in best_months)),
            })
            if len(out) >= limit:
                break
        out.sort(key=lambda r: (-r["total_days"], r["km"]))
        return {
            "period": self.period_info(period),
            "role": role,
            "wanted": want_name,
            "wanted_label": want_label,
            "anchor": {
                "lat": round(float(self.lat[anchor]), 2),
                "lon": round(float(self.lon[anchor]), 2),
                "query_lat": lat, "query_lon": lon,
                "city": self.nearest_city(lat, lon),
                "own_days": round(own_days, 1),
                "summer_days": round(float(sm_a[list(ms)].sum()), 1),
                "winter_days": round(float(wm_a[list(rest)].sum()), 1),
                "total_days": round(float(sm_a[list(ms)].sum()) + float(wm_a[list(rest)].sum()), 1),
                "summer_months": list(ms), "winter_months": list(rest),
                "summer_label": months_label(ms), "winter_label": months_label(rest),
            },
            "window_range": {"min_months": int(min_len), "max_months": int(max_len)},
            "max_km": max_km,
            "candidates": out,
        }

    def escape_report(self, lat: float, lon: float, season: str, p: Params,
                      summer_months=DEFAULT_SUMMER_MONTHS, limit: int = 8,
                      min_distance_km: float = 50, period: str = "all",
                      spread_km: float = 0.0) -> dict:
        """Fester Wohnort, feste Reisemonate, ein gemeinsames Wohlfühlprofil.

        Rangfolge: Jahressumme (äquivalent zum Zugewinn), bei Gleichstand die
        größere Stadt, danach Distanz. Alle Städte werden geprüft, nicht nur eine
        Vorauswahl guter Paare. Mit ``spread_km`` > 0 liegen die vorgeschlagenen
        Ziele mindestens so weit auseinander – sonst teilen sich oft acht Orte
        rund um denselben Rasterpunkt (Murcia, Molina de Segura, …) die Liste.
        """
        if season not in ("winter", "summer"):
            raise ValueError("season muss winter oder summer sein")
        summer = norm_months(summer_months) or DEFAULT_SUMMER_MONTHS
        winter = tuple(m for m in range(12) if m not in summer)
        if not winter:
            raise ValueError("Es muss mindestens ein Monat für das Winterquartier bleiben")
        away = winter if season == "winter" else summer
        home = tuple(m for m in range(12) if m not in away)
        anchor_idx = self.point_index(lat, lon)
        anchor_monthly = self.point_monthly(p, anchor_idx, period)
        home_days = float(anchor_monthly[list(home)].sum())
        baseline = float(anchor_monthly.sum())
        monthly = self.monthly_counts(p, period)[:, self.city_point_idx]
        away_days = monthly[list(away)].sum(axis=0)
        anchor_city = self.nearest_city(lat, lon)
        home_city = {"name": anchor_city["name"] if anchor_city else "Wohnort",
                     "country": anchor_city.get("name_de", "") if anchor_city else "",
                     "lat": lat, "lon": lon, "days": round(home_days, 1)}
        candidates = []
        seen = set()
        for i, city in enumerate(self.cities):
            location = (float(city["lat"]), float(city["lon"]))
            dlat = math.radians(location[0] - lat)
            dlon = math.radians(location[1] - lon)
            a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat))
                 * math.cos(math.radians(location[0])) * math.sin(dlon / 2) ** 2)
            distance = 2 * 6371 * math.asin(math.sqrt(min(1, max(0, a))))
            if distance < min_distance_km or location in seen:
                continue
            seen.add(location)
            value = float(away_days[i])
            destination = {"name": city["name"], "country": city.get("name_de", ""),
                           "lat": location[0], "lon": location[1], "days": round(value, 1),
                           "pop": int(city.get("pop", 0) or 0)}
            candidates.append({
                "summer_city": home_city if season == "winter" else destination,
                "winter_city": destination if season == "winter" else home_city,
                "summer_days": round(home_days if season == "winter" else value, 1),
                "winter_days": round(value if season == "winter" else home_days, 1),
                "total_days": round(home_days + value, 1),
                "gain_days": round(home_days + value - baseline, 1),
                "distance_km": round(distance, 1),
            })
        candidates.sort(key=lambda c: (-c["total_days"], -c[season + "_city"]["pop"],
                                       c["distance_km"], c[season + "_city"]["name"]))
        if spread_km > 0:
            candidates = self._spread(candidates, season + "_city", spread_km, limit)
        return {"period": self.period_info(period), "season": season,
                "anchor": home_city, "baseline_days": round(baseline, 1),
                "away_months": list(away), "home_months": list(home),
                "summer_months": list(summer), "winter_months": list(winter),
                "summer_label": months_label(summer), "winter_label": months_label(winter),
                "params": p.query(), "min_distance_km": min_distance_km,
                "candidates": candidates[:limit], "best": candidates[0] if candidates else None}

    @staticmethod
    def _spread(candidates: list[dict], key: str, spread_km: float, limit: int) -> list[dict]:
        """Bestplatzierte Ziele behalten, die mindestens ``spread_km`` auseinanderliegen."""
        picked: list[dict] = []
        for candidate in candidates:
            city = candidate[key]
            lat1, lon1 = math.radians(city["lat"]), math.radians(city["lon"])
            too_close = False
            for other in picked:
                lat2, lon2 = math.radians(other[key]["lat"]), math.radians(other[key]["lon"])
                a = (math.sin((lat2 - lat1) / 2) ** 2
                     + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
                if 2 * 6371 * math.asin(math.sqrt(min(1.0, a))) < spread_km:
                    too_close = True
                    break
            if not too_close:
                picked.append(candidate)
                if len(picked) >= limit:
                    break
        return picked

    def two_city_combination(self, p: Params, summer_months=None, limit: int = 8,
                             min_distance_km: float = 50.0,
                             period: str = "all") -> dict:
        """Beste feste Sommer-/Winteraufteilung auf zwei verschiedene Städte.

        Anders als :meth:`season_split_report` gilt in beiden Zeitfenstern
        *dasselbe* Wohlfühlprofil. Die Sommermonate werden am Sommerort gezählt,
        alle übrigen Monate am Winterort. Jede Stadt erhält ihre Monatswerte vom
        nächstgelegenen ERA5-Punkt; alle Tageszahlen sind Mittelwerte pro Jahr.
        """
        ms = norm_months(summer_months) or DEFAULT_SUMMER_MONTHS
        rest = tuple(m for m in range(12) if m not in ms)
        limit = max(1, min(40, int(limit)))
        min_distance_km = max(0.0, float(min_distance_km))
        if not self.cities or not self.city_point_idx.size:
            return {"candidates": [], "best": None,
                    "period": self.period_info(period),
                    "summer_months": list(ms), "winter_months": list(rest),
                    "summer_label": months_label(ms), "winter_label": months_label(rest),
                    "params": p.query(), "min_distance_km": min_distance_km}

        # (Monat, Stadt); ein und dasselbe Traumwetterprofil wird für beide Orte
        # verwendet. Die komplementären Monatsfenster teilen das Jahr vollständig.
        monthly = self.monthly_counts(p, period)[:, self.city_point_idx]
        summer_days = monthly[list(ms)].sum(axis=0)
        winter_days = monthly[list(rest)].sum(axis=0)
        city_lats = np.asarray([c["lat"] for c in self.cities], dtype=np.float64)
        city_lons = np.asarray([c["lon"] for c in self.cities], dtype=np.float64)
        city_pops = np.asarray([int(c.get("pop", 0)) for c in self.cities], dtype=np.int64)

        def distinct_order(scores: np.ndarray) -> np.ndarray:
            """Exakte/nahe Koordinatenduplikate entfernen, Städte sonst behalten."""
            selected: list[int] = []
            seen_locations: set[tuple[float, float]] = set()
            for raw_idx in np.argsort(-scores):
                idx = int(raw_idx)
                location = (round(float(city_lats[idx]), 4), round(float(city_lons[idx]), 4))
                if location in seen_locations:
                    continue
                selected.append(idx)
                seen_locations.add(location)
            return np.asarray(selected, dtype=np.int64)

        summer_order = distinct_order(summer_days)
        winter_order = distinct_order(winter_days)
        winter_lat_rad = np.radians(city_lats[winter_order])
        winter_lon_rad = np.radians(city_lons[winter_order])
        winter_cos_lat = np.cos(winter_lat_rad)
        # Für die globalen Top-k genügen je Sommerort seine k besten gültigen
        # Winterorte: ein noch schwächerer Winterort kann nicht in die Top-k rutschen.
        alternatives_per_summer = limit
        candidates: list[dict] = []

        for summer_idx in summer_order:
            summer_idx = int(summer_idx)
            lat1 = math.radians(float(city_lats[summer_idx]))
            lon1 = math.radians(float(city_lons[summer_idx]))
            dlat = winter_lat_rad - lat1
            dlon = (winter_lon_rad - lon1 + math.pi) % (2.0 * math.pi) - math.pi
            a = (np.sin(dlat / 2.0) ** 2
                 + math.cos(lat1) * winter_cos_lat * np.sin(dlon / 2.0) ** 2)
            distances = 2.0 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
            # winter_order ist bereits nach den Wintertagen sortiert: die ersten
            # gültigen Einträge liefern die besten Winterorte für diesen Sommerort.
            eligible = np.flatnonzero(distances >= min_distance_km)
            for position in eligible[:alternatives_per_summer]:
                winter_idx = int(winter_order[int(position)])
                summer_city = self.cities[summer_idx]
                winter_city = self.cities[winter_idx]
                summer_value = float(summer_days[summer_idx])
                winter_value = float(winter_days[winter_idx])
                candidates.append({
                    "summer_city": {
                        "name": summer_city["name"],
                        "country": summer_city.get("name_de", ""),
                        "lat": float(city_lats[summer_idx]),
                        "lon": float(city_lons[summer_idx]),
                        "pop": int(city_pops[summer_idx]),
                        "days": round(summer_value, 1),
                    },
                    "winter_city": {
                        "name": winter_city["name"],
                        "country": winter_city.get("name_de", ""),
                        "lat": float(city_lats[winter_idx]),
                        "lon": float(city_lons[winter_idx]),
                        "pop": int(city_pops[winter_idx]),
                        "days": round(winter_value, 1),
                    },
                    "summer_days": round(summer_value, 1),
                    "winter_days": round(winter_value, 1),
                    "total_days": round(summer_value + winter_value, 1),
                    "distance_km": round(float(distances[int(position)]), 1),
                })

        candidates.sort(key=lambda row: (-row["total_days"], row["distance_km"],
                                         row["summer_city"]["name"], row["winter_city"]["name"]))
        return {
            "period": self.period_info(period),
            "summer_months": list(ms),
            "winter_months": list(rest),
            "summer_label": months_label(ms),
            "winter_label": months_label(rest),
            "params": p.query(),
            "min_distance_km": min_distance_km,
            "candidates": candidates[:limit],
            "best": candidates[0] if candidates else None,
        }

    def ranking(self, p: Params, month=None, limit: int = 12) -> list[dict]:
        """Beste Städte für ein Wetterprofil (Rückwärtskompatibel, s. a. counts)."""
        return self.ranking_counts(self.counts(p, month), limit)

    def ranking_counts(self, counts: np.ndarray, limit: int = 12) -> list[dict]:
        """Beste Städte zu einer bereits berechneten Tageszahl-Folge."""
        if not self.cities:
            return []
        city_counts = counts[self.city_point_idx]
        order = np.argsort(-city_counts)[: max(limit * 8, 60)]
        seen, picked, out = set(), [], []
        for i in order:
            i = int(i)
            c = self.cities[i]
            key = (c["name"].lower(), c.get("country"))
            if key in seen:
                continue
            # Doppelte Metropolregionen (z. B. „City of London“ neben „London“) überspringen
            lat, lon = float(c["lat"]), float(c["lon"])
            if any(abs(lat - a) < 0.35 and abs((lon - b) * math.cos(math.radians(lat))) < 0.35
                   for a, b in picked):
                continue
            seen.add(key)
            picked.append((lat, lon))
            out.append({
                "name": c["name"], "country": c["name_de"], "lat": lat, "lon": lon,
                "pop": c["pop"], "days": round(float(city_counts[i]), 1),
                "dist_km": round(float(self.city_point_km[i])),
            })
            if len(out) >= limit:
                break
        return out

    def cities_for_map(self, p: Params, month=None, limit: int = 400) -> list[dict]:
        """Die größten Orte (nach Einwohnern) mit ihrer Tageszahl – für Kartenmarker."""
        return self.cities_for_map_counts(self.counts(p, month), limit)

    def cities_for_map_counts(self, counts: np.ndarray, limit: int = 400) -> list[dict]:
        """Wie :meth:`cities_for_map`, aber für eine fertige Tageszahl-Folge."""
        if not self.cities:
            return []
        city_counts = counts[self.city_point_idx]
        pops = np.array([int(c.get("pop", 0)) for c in self.cities], dtype=np.int64)
        order = np.argsort(-pops)[: max(1, min(limit, len(self.cities)))]
        return [{
            "name": self.cities[i]["name"],
            "country": self.cities[i].get("name_de", ""),
            "lat": self.cities[i]["lat"], "lon": self.cities[i]["lon"],
            "pop": int(pops[i]),
            "days": round(float(city_counts[i]), 1),
        } for i in order]

    def legend_ticks(self, vmax: float, n: int = 5) -> list[float]:
        step = vmax / n
        return [round(step * i, 1) for i in range(n + 1)]
