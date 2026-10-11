"""Datenzugriff, Tageszählung und Karten-Rendering für „Mein perfektes Klima“.

Der Datensatz (``app/data/climate_grid.npz``) enthält tägliche ERA5-Werte
(2016–2025) für ~2.250 Landpunkte über Europa in 0,75°-Auflösung:

    tmax, tmin  – Tageshöchst-/Tiefsttemperatur (int8, 0,5 °C-Schritte)
    tdew        – mittlerer Tages-Taupunkt (int8, 0,5 °C-Schritte)
    sun         – Sonnenscheindauer pro Tag (int8, 0,25 h-Schritte)
    prec        – Niederschlagssumme pro Tag (int8, 0,5 mm-Schritte)

Gezählt werden die Tage, die **alle** Kriterien gleichzeitig erfüllen – exakt
über die tägliche Zeitreihe, nicht über Monatsmittel.
"""
from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree

VARS = ("tmax", "tmin", "tdew", "sun", "prec")
# Skalen: int8-Schritte je Einheit der Rohdaten. Temperatur/Taupunkt kommen in °C
# (0,5-°C-Schritte), Sonnenschein kommt in *Sekunden* (0,25-h-Schritte = 900 s),
# Niederschlag in mm (0,5-mm-Schritte).
SCALES = {"tmax": 2.0, "tmin": 2.0, "tdew": 2.0, "sun": 4.0 / 3600.0, "prec": 2.0}
SUN_SECONDS_PER_HOUR = 3600.0
DATA_START_LAT = None  # wird aus der Meta-Datei gelesen

# Farbskala: wenige Tage (blau) -> viele Tage (rot)
COLOR_STOPS = np.array([
    [0.00, 16, 38, 92],
    [0.15, 28, 92, 168],
    [0.32, 32, 150, 160],
    [0.50, 62, 166, 92],
    [0.68, 198, 205, 63],
    [0.84, 240, 140, 30],
    [1.00, 200, 40, 30],
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
        self.day_month = days.astype("datetime64[M]").astype(int) % 12  # 0=Januar
        self.years = self.n_days / 365.25

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
        self._counts_cache: dict = {}
        self._alpha_cache: dict = {}

        cities_file = data_dir / "cities.json"
        self.cities = json.loads(cities_file.read_text()) if cities_file.exists() else []
        if self.cities:
            c_xyz = self._to_xyz(
                np.array([c["lat"] for c in self.cities]),
                np.array([c["lon"] for c in self.cities]),
            )
            dist, self.city_point_idx = self.tree.query(c_xyz, k=1)
            # Chord-Distanz auf der Einheitskugel -> km (für kurze Strecken genau genug)
            self.city_point_km = dist * 6371.0

    # ---------------------------------------------------------------- Geometrie
    @staticmethod
    def _to_xyz(lat, lon):
        la = np.radians(np.asarray(lat, dtype=np.float64))
        lo = np.radians(np.asarray(lon, dtype=np.float64))
        return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)

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

    def days_mask(self, p: Params, month: int | None = None, point: int | None = None) -> np.ndarray:
        """Boolesche Matrix (Punkte, Tage) bzw. (Tage,) für einen Einzelpunkt."""
        if month is None:
            sel = slice(None)
        else:
            sel = np.flatnonzero(self.day_month == int(month))

        def get(name):
            arr = self.raw[name]
            if point is not None:
                arr = arr[point: point + 1]
            return arr[:, sel]

        ok = (get("tmax") >= self._q(p.tmax_min, SCALES["tmax"], "min"))
        ok &= (get("tmax") <= self._q(p.tmax_max, SCALES["tmax"], "max"))
        ok &= (get("tmin") >= self._q(p.tmin_min, SCALES["tmin"], "min"))
        ok &= (get("tmin") <= self._q(p.tmin_max, SCALES["tmin"], "max"))
        ok &= (get("tdew") <= self._q(p.tdew_max, SCALES["tdew"], "max"))
        # sun_min wird in Stunden angegeben, die Rohdaten in Sekunden
        ok &= (get("sun") >= self._q(p.sun_min * SUN_SECONDS_PER_HOUR, SCALES["sun"], "min"))
        if p.prec_max < 90:
            ok &= (get("prec") <= self._q(p.prec_max, SCALES["prec"], "max"))
        return ok

    def counts(self, p: Params, month: int | None = None) -> np.ndarray:
        """Tage pro Jahr bzw. Tage im gewählten Monat, je Rasterpunkt."""
        key = (p, month, self.n_days)
        hit = self._counts_cache.get(key)
        if hit is None:
            # Immer durch die Zahl der Jahre teilen: im Jahresmodus ergeben sich
            # Tage/Jahr, im Monatsmodus die mittleren Tage dieses Monats.
            hit = self.days_mask(p, month).sum(axis=1).astype(np.float64) / self.years
            if len(self._counts_cache) > 400:
                self._counts_cache.clear()
            self._counts_cache[key] = hit
        return hit

    def monthly_counts(self, p: Params) -> np.ndarray:
        """(12, Punkte) – Tage je Kalendermonat (gemittelt über die Jahre)."""
        out = np.zeros((12, self.n_points), dtype=np.float64)
        for m in range(12):
            out[m] = self.counts(p, month=m)
        return out

    def month_limit(self, month: int | None) -> float:
        """Maximal erreichbare Tageszahl (365,25 im Jahr bzw. Tage im Monat)."""
        if month is None:
            return 365.25
        return float((self.day_month == int(month)).sum() / self.years)

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

    def _idw(self, ll: np.ndarray, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Inverse-Distanz-Interpolation (k=8) auf ein Pixelgitter (Mercator)."""
        lat_px = ll[..., 0].reshape(-1)
        lon_px = ll[..., 1].reshape(-1)
        k = 8
        counts_f = counts.astype(np.float32)
        vals = np.empty(lat_px.size, dtype=np.float32)
        far = np.empty(lat_px.size, dtype=bool)
        block = 200_000
        for start in range(0, lat_px.size, block):
            stop = min(start + block, lat_px.size)
            q = self._to_xyz(lat_px[start:stop], lon_px[start:stop]).astype(np.float64)
            dist, idx = self.tree.query(q, k=k, workers=-1)
            dist = np.maximum(dist, 1e-9)
            wgt = (1.0 / dist ** 3.0).astype(np.float32)
            vals[start:stop] = (wgt * counts_f[idx]).sum(axis=1) / wgt.sum(axis=1)
            far[start:stop] = dist[:, 0] > 0.06      # weiter als ~380 km von jedem Datenpunkt
        return vals, far

    def render(self, p: Params, month: int | None = None, width: int = 1100,
               fixed_scale: bool = False) -> tuple[bytes, dict]:
        """Erzeugt das Kartenbild (PNG, Web-Mercator) und Statistikdaten."""
        width = int(max(320, min(2000, width)))
        counts = self.counts(p, month)

        # Fein aufgelöstes Gitter nur für die Landmaske (Deckkraft), das Feld
        # wird auf einem gröberen Gitter interpoliert und hochskaliert.
        ll, height, width = self._pixel_grid(width)
        cwidth = int(max(360, min(700, width)))
        cll, cheight, cwidth = self._pixel_grid(cwidth)

        vals, far = self._idw(cll, counts)
        field_c = vals.reshape(cheight, cwidth)
        mask_c = far.reshape(cheight, cwidth)
        field_c = gaussian_filter(np.where(mask_c, np.float32(np.nanmin(counts)), field_c), 1.0)

        if (cheight, cwidth) != (height, width):
            from scipy.ndimage import zoom
            zy, zx = height / cheight, width / cwidth
            field = zoom(field_c, (zy, zx), order=1, mode="nearest")[:height, :width]
            cover = zoom((~mask_c).astype(np.float32), (zy, zx), order=1, mode="nearest")[:height, :width]
            field = np.where(cover > 0.35, field, np.nan)
        else:
            field = np.where(mask_c, np.nan, field_c)

        vmax_data = float(np.nanmax(counts)) if np.isfinite(counts).any() else 1.0
        limit = self.month_limit(month)
        if fixed_scale:
            vmax = limit
        else:
            p99 = float(np.nanpercentile(counts, 99))
            vmax = min(max(p99, vmax_data * 0.5, 1.0), limit)
        vmin = 0.0

        norm = np.clip((field - vmin) / max(vmax - vmin, 1e-9), 0.0, 1.0)
        norm = np.nan_to_num(norm, nan=0.0)
        rgb = np.empty(norm.shape + (3,), dtype=np.uint8)
        for ch in range(3):
            rgb[..., ch] = np.interp(norm, COLOR_STOPS[:, 0], COLOR_STOPS[:, 1 + ch]).astype(np.uint8)

        alpha = self._alpha_for(ll, (height, width))
        alpha = np.where(np.isnan(field), 0, alpha).astype(np.uint8)
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
                return cached
        alpha = self._alpha_calc(ll)
        if size is not None:
            self._alpha_cache[size] = alpha
        return alpha

    def _alpha_calc(self, ll: np.ndarray) -> np.ndarray:
        from scipy.ndimage import map_coordinates

        bbox = self.bbox
        iy = (ll[..., 0] - bbox["lat_min"]) / self.mask_step
        ix = (ll[..., 1] - bbox["lon_min"]) / self.mask_step
        soft = map_coordinates(self.mask_soft, [iy, ix], order=1, mode="constant", cval=0.0)
        # Kontrastkurve: >0,8 wird voll deckend, <0,25 ganz transparent
        soft = np.clip((soft - 0.25) / 0.55, 0.0, 1.0)
        return np.round(soft * 255.0).astype(np.uint8)

    # ------------------------------------------------------------ Punktabfragen
    def point_index(self, lat: float, lon: float) -> int:
        _, idx = self.tree.query(self._to_xyz(np.array([lat]), np.array([lon]))[0], k=1)
        return int(idx)

    def point_report(self, lat: float, lon: float, p: Params) -> dict:
        idx = self.point_index(lat, lon)
        days = self.days_mask(p, point=idx)[0]
        per_month = [round(float(days[self.day_month == m].sum() / self.years), 1) for m in range(12)]
        yearly = round(sum(per_month), 1)
        return {
            "lat": float(self.lat[idx]), "lon": float(self.lon[idx]),
            "query_lat": lat, "query_lon": lon,
            "days_per_year": yearly,
            "days_per_month": per_month,
            "grid_distance_deg": round(float(np.hypot(self.lat[idx] - lat, self.lon[idx] - lon)), 2),
            "grid_distance_km": round(float(111.0 * np.hypot(self.lat[idx] - lat,
                                                             (self.lon[idx] - lon) * math.cos(math.radians(lat))))),
        }

    def ranking(self, p: Params, month: int | None = None, limit: int = 12) -> list[dict]:
        if not self.cities:
            return []
        counts = self.counts(p, month)
        city_counts = counts[self.city_point_idx]
        order = np.argsort(-city_counts)[: max(limit * 6, 40)]
        seen, out = set(), []
        for i in order:
            c = self.cities[int(i)]
            key = c["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": c["name"], "country": c["name_de"], "lat": c["lat"], "lon": c["lon"],
                "pop": c["pop"], "days": round(float(city_counts[int(i)]), 1),
                "dist_km": round(float(self.city_point_km[int(i)])),
            })
            if len(out) >= limit:
                break
        return out

    def legend_ticks(self, vmax: float, n: int = 5) -> list[float]:
        step = vmax / n
        return [round(step * i, 1) for i in range(n + 1)]
