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
from collections import OrderedDict
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
# Faktor int8-Rohwert -> Einheit der Anzeige: °C, Stunden bzw. mm
UNIT_FACTORS = {
    "tmax": 1.0 / SCALES["tmax"],
    "tmin": 1.0 / SCALES["tmin"],
    "tdew": 1.0 / SCALES["tdew"],
    "sun": 1.0 / SCALES["sun"] / SUN_SECONDS_PER_HOUR,
    "prec": 1.0 / SCALES["prec"],
}
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


# Breite des Gitters, auf dem das Feld interpoliert wird. Das Ergebnis wird
# danach bilinear auf die angeforderte PNG-Breite skaliert – das Feld ist glatt,
# deshalb sieht man davon nichts, spart aber auf kleinen Render-Instanzen
# (0,1 CPU / 512 MB) mehrere hundert Millisekunden pro Anfrage.
FIELD_WIDTH = 640
MAX_WIDTH = 1400
# Pixel, die weiter als dieser Betrag (Einheitskugel, ~380 km) von jedem
# Datenpunkt entfernt liegen, gelten als „keine Daten“ (offenes Meer).
FAR_DISTANCE = 0.06


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
        self.repaired = self._repair_sentinels()
        self.season_delta = self._season_sign()
        self._counts_cache: dict = {}
        self._alpha_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._idw_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._month_day_count = np.bincount(self.day_month, minlength=12).astype(np.float64)
        self._month_days = self._month_day_count / self.years

        # Qualität des Datensatzes (von scripts/check_grid.py geschrieben)
        report = data_dir / "quality_report.json"
        self.quality = json.loads(report.read_text()) if report.exists() else None

        cities_file = data_dir / "cities.json"
        self.cities = json.loads(cities_file.read_text()) if cities_file.exists() else []
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
        width = int(max(320, min(MAX_WIDTH, width)))
        counts = self.counts(p, month)

        # Das Feld wird auf einem festen, gröberen Gitter interpoliert und erst
        # danach (bilinear, in C) auf die Zielbreite skaliert; die Landmaske wird
        # weiter in voller Auflösung ausgewertet, die Küsten bleiben also scharf.
        fwidth = min(FIELD_WIDTH, width)
        field, fheight, fwidth = self._field(counts, fwidth)
        ll, height, width = self._pixel_grid(width)

        vmax_data = float(np.nanmax(counts)) if np.isfinite(counts).any() else 1.0
        limit = self.month_limit(month)
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

    def _monthly(self, values: np.ndarray, total: bool = False) -> list[float]:
        """12 Monatswerte: Mittel je Tag (``total=False``) oder Summe je Jahr."""
        acc = np.bincount(self.day_month, weights=values.astype(np.float64), minlength=12)
        div = self.years if total else self._month_day_count
        return [round(float(v), 2) for v in (acc / np.maximum(div, 1e-9))]

    def climatology(self, idx: int) -> dict:
        """Monats- und Jahresklima eines Rasterpunkts (°C, h/Tag, mm)."""
        vals = {name: self.raw[name][idx].astype(np.float64) * UNIT_FACTORS[name] for name in VARS}
        out: dict = {
            "months": {name: self._monthly(vals[name], total=(name == "prec")) for name in VARS},
            "year": {
                "tmax": round(float(vals["tmax"].mean()), 1),
                "tmin": round(float(vals["tmin"].mean()), 1),
                "tdew": round(float(vals["tdew"].mean()), 1),
                "sun": round(float(vals["sun"].mean()), 2),
                "prec": round(float(vals["prec"].sum() / self.years), 0),
            },
        }
        for label, mask in (
            ("frost_days", vals["tmin"] <= 0.0),
            ("summer_days", vals["tmax"] >= 25.0),
            ("hot_days", vals["tmax"] >= 30.0),
            ("wet_days", vals["prec"] >= 1.0),
        ):
            out[label] = round(float(mask.sum() / self.years), 1)
        return out

    def point_report(self, lat: float, lon: float, p: Params, month: int | None = None) -> dict:
        """Alles, was die Detailkarte zu einem Ort zeigt."""
        idx = self.point_index(lat, lon)
        days = self.days_mask(p, point=idx)[0]
        per_month = [round(float(days[self.day_month == m].sum() / self.years), 1) for m in range(12)]
        yearly = round(sum(per_month), 1)

        counts = self.counts(p, month)
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
            "climate": self.climatology(idx),
            "days_here": round(own, 1),
            "percentile": percentile,
            "europe_best": best,
            "params": p.query(),
            "month": month,
            "month_limit": round(self.month_limit(month), 1),
            "limit": round(self.month_limit(month), 1),
        }
        if month is not None:
            rep["selected_month"] = int(month)
            rep["days_selected_month"] = per_month[int(month)]
        return rep

    def ranking(self, p: Params, month: int | None = None, limit: int = 12) -> list[dict]:
        if not self.cities:
            return []
        counts = self.counts(p, month)
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

    def cities_for_map(self, p: Params, month: int | None = None, limit: int = 400) -> list[dict]:
        """Die größten Orte (nach Einwohnern) mit ihrer Tageszahl – für Kartenmarker."""
        if not self.cities:
            return []
        counts = self.counts(p, month)
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
