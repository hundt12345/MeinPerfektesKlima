#!/usr/bin/env python3
"""Erzeugt ein *synthetisches* Raster im gleichen Format wie fetch_climate_grid.py.

Nur für lokale Entwicklung/Demo gedacht – wenn kein Internetzugang zu Open-Meteo
besteht, aber Karte und Bedienung getestet werden sollen. **Die echten Daten
kommen aus dem GitHub-Action-Workflow** (`Klimadaten bauen`); die App kennzeichnet
dieses Raster deutlich als Testdaten (`model: synthetic`).

Das Modell ist ein einfacher Wettergenerator auf Klimatologie-Basis:

* Jahresmittel aus einer Breitenreferenz (europäische Stationsmittel) plus
  Kontinentalitäts-, Atlantik- und Grob-Orographie-Korrektur,
* Jahresgang als Cosinus mit ortsabhängiger Amplitude und Phase,
* Tagesmittel = Klimatologie + AR(1)-Wetteranomalie,
* Tageshöchst/-tiefstwert aus Tagesmittel und Tagesschwankung (DTR),
* Taupunkt über die Magnus-Formel aus Temperatur und relativer Feuchte
  (Feuchte je Klimazone und Jahreszeit),
* Sonnenschein = astronomische Tageslänge × (1 − Bewölkung),
* Niederschlag = Jahreszielsumme, über Monatsgewichte verteilt
  (Mittelmeer: trockener Sommer, Atlantik: nasser Winter, Kontinent: Sommermax.).

Damit ist das Ergebnis physikalisch stimmig (Juli wärmer als Januar, Sevilla
sonniger und trockener im Sommer als Stockholm) – aber es ist **kein** Ersatz
für ERA5. Abweichungen zu Stationswerten: typisch 1–2 K, beim Niederschlag ±30 %.

Aufruf:
  python scripts/make_dev_grid.py                  # nach app/data
  python scripts/make_dev_grid.py --out /tmp/grid  # eigenes Verzeichnis
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "app" / "data"

CFG = dict(lat_min=34.0, lat_max=72.0, lon_min=-25.0, lon_max=45.0, step=0.75,
           start_date="2016-01-01", end_date="2025-12-31", mask_step=0.05)

# Jahresmitteltemperatur (°C) über der Breite – grober europäischer Durchschnitt
LAT_REF = [34.0, 36.0, 38.0, 40.0, 42.0, 44.0, 46.0, 48.0, 50.0, 52.0,
           54.0, 56.0, 58.0, 60.0, 62.0, 64.0, 66.0, 68.0, 70.0, 72.0]
T_REF = [19.9, 19.8, 19.0, 18.0, 16.9, 15.5, 13.8, 12.0, 11.3, 10.8,
         9.9, 8.8, 7.8, 7.0, 6.0, 5.2, 3.6, 2.5, 0.8, -1.5]

# Grobe Orographie als Summe von Gauß-Beulen: (lat, lon, sigma_lat, sigma_lon, °C)
OROGRAPHY = [
    (46.4, 10.5, 0.85, 3.2, -6.5),   # Alpen
    (45.9, 7.0, 0.80, 1.2, -5.0),    # Westalpen
    (42.6, 0.8, 0.80, 2.4, -5.0),    # Pyrenäen
    (43.2, 13.2, 1.10, 1.2, -3.0),   # Apennin
    (40.3, -3.7, 2.00, 3.0, -2.2),   # Meseta (hoch gelegen)
    (37.1, -3.2, 0.80, 1.4, -2.6),   # Sierra Nevada
    (41.2, 22.5, 1.30, 2.8, -3.2),   # Balkan/Rhodopen
    (39.7, 21.2, 0.90, 1.1, -2.6),   # Pindos
    (45.6, 25.0, 1.50, 3.2, -3.2),   # Karpaten
    (61.2, 8.6, 2.00, 1.6, -4.5),    # Skandinavisches Gebirge
    (64.5, 25.0, 2.50, 4.0, -3.5),   # Nordfinnland/Fjell
    (57.4, -5.2, 1.10, 1.3, -1.8),   # Schottisches Hochland
    (64.8, -18.6, 1.20, 1.8, -3.0),  # Isländisches Hochland
    (45.0, 3.0, 1.10, 1.3, -2.4),    # Massif Central
    (50.6, 15.5, 0.90, 1.8, -1.8),   # Sudeten/Erzgebirge
    (47.4, 19.2, 0.70, 1.2, -1.2),   # Ungarische Mittelgebirge
    (43.0, 18.5, 0.80, 2.2, -2.0),   # Dinarisches Gebirge
    (41.0, 29.0, 1.00, 3.0, -2.0),   # Nordwestanatolien
    (39.0, 35.0, 1.60, 3.0, -2.6),   # Zentralanatolien
    (37.8, 14.3, 0.80, 1.5, -3.5),   # Sizilien (Madonie/Etna-Umland)
    (40.6, 15.6, 1.00, 1.4, -3.5),   # Südapennin
    (35.3, 24.9, 0.50, 1.2, -2.8),   # Kreta (Ida/Gebirge)
    (42.4, 43.5, 1.20, 2.0, -3.0),   # Kaukasus
    (43.2, 42.0, 1.00, 1.6, -2.4),   # Georgien/Armenien
]


def bump(lat: np.ndarray, lon: np.ndarray, la: float, lo: float,
         sla: float, slo: float) -> np.ndarray:
    """Gauß-Beule (0..1) um einen Punkt, in Länge breitenkorrigiert."""
    dlon = (lon - lo) * np.cos(np.radians(np.clip(lat, -85.0, 85.0)))
    return np.exp(-((lat - la) / sla) ** 2 - (dlon / slo) ** 2)


def orog_anomaly(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    out = np.zeros_like(lat, dtype=np.float64)
    for la, lo, sla, slo, amp in OROGRAPHY:
        out += amp * bump(lat, lon, la, lo, sla, slo)
    return out


def orog_wet(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """0..1 – wie stark die Grob-Orographie den Niederschlag hebt (Stau).

    Nur echte Gebirge wirken als Regenfänger; Hochebenen wie die Meseta
    (kleine Temperatur-Anomalie) tragen kaum bei.
    """
    out = np.zeros_like(lat, dtype=np.float64)
    for la, lo, sla, slo, amp in OROGRAPHY:
        b = bump(lat, lon, la, lo, sla * 1.4, slo * 1.4) ** 0.6
        out += b * float(np.clip((abs(amp) - 2.0) / 4.5, 0.0, 1.0)) ** 1.5
    return np.clip(out, 0.0, 1.6)


def daylength(lat: np.ndarray, doy: np.ndarray) -> np.ndarray:
    """Astronomische Tageslänge in Stunden (Polartag/-nacht abgeschnitten)."""
    decl = np.radians(-23.44 * np.cos(2 * np.pi * (doy + 10) / 365.25))
    tan_lat = np.tan(np.radians(lat))[:, None]
    cos_h = np.clip(-tan_lat * np.tan(decl)[None, :], -1.0, 1.0)
    return (24.0 / np.pi) * np.arccos(cos_h)


def dew_point(t: np.ndarray, rh: np.ndarray) -> np.ndarray:
    """Magnus-Formel (umgekehrt): Taupunkt aus Temperatur [°C] und r. F. [0..1]."""
    a, b = 17.62, 243.12
    rh = np.clip(rh, 0.05, 1.0)
    alpha = np.log(rh) + a * t / (b + t)
    return b * alpha / (a - alpha)


def ar1(rng: np.random.Generator, shape: tuple[int, int], rho: float, sigma: float) -> np.ndarray:
    """AR(1)-Prozess entlang der Zeitachse (stationär, Standardabweichung sigma)."""
    n_pts, n_days = shape
    out = np.empty(shape, dtype=np.float32)
    innov = sigma * float(np.sqrt(1.0 - rho * rho))
    prev = rng.normal(0.0, sigma, size=n_pts).astype(np.float32)
    out[:, 0] = prev
    for d in range(1, n_days):
        prev = (rho * prev + rng.normal(0.0, innov, size=n_pts)).astype(np.float32)
        out[:, d] = prev
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Zielverzeichnis für climate_grid.npz")
    ap.add_argument("--start", default=CFG["start_date"])
    ap.add_argument("--end", default=CFG["end_date"])
    ap.add_argument("--step", type=float, default=CFG["step"])
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    out_dir = Path(args.out)
    cfg = dict(CFG, start_date=args.start, end_date=args.end, step=args.step)
    from global_land_mask import globe

    rng = np.random.default_rng(args.seed)
    lats = np.arange(cfg["lat_min"], cfg["lat_max"] + 1e-9, cfg["step"])
    lons = np.arange(cfg["lon_min"], cfg["lon_max"] + 1e-9, cfg["step"])
    lon2, lat2 = np.meshgrid(lons, lats)
    lat = lat2.ravel().astype(np.float64)
    lon = lon2.ravel().astype(np.float64)
    land = globe.is_land(lat, lon)
    if land.dtype != bool:
        land = land > 0.5
    lat, lon = lat[land], lon[land]
    n_pts = lat.size
    print(f"{n_pts} Landpunkte ({cfg['step']}°)")

    # ------------------------------------------------------------------ Zonen
    orog_t = orog_anomaly(lat, lon)                       # °C (negativ = kühler)
    orog_p = orog_wet(lat, lon)                           # 0..1,6
    cont = np.clip(1.0 - np.exp(-(lon + 15.0) / 18.0), 0.0, 1.0)   # Kontinentalität
    mar = np.clip(np.exp(-((lon + 2.0) / 10.0) ** 2)
                  * np.clip((lat - 36.0) / 6.0, 0.0, 1.0), 0.0, 1.0)  # West-/Nordwesteuropa
    med = (np.exp(-((lat - 40.0) / 6.0) ** 2)
           * (1.0 / (1.0 + np.exp(-(lon + 12.0) / 4.0)))
           * (1.0 / (1.0 + np.exp((lon - 34.0) / 6.0))))               # Mittelmeerklima
    dry_iberia = bump(lat, lon, 40.0, -3.5, 3.4, 4.6)     # Meseta/Ebro: trocken, groß
    iceland = bump(lat, lon, 63.5, -19.0, 4.0, 8.0)       # Nordatlantik: trüb, nass, mild
    north = np.clip((lat - 55.0) / 8.0, 0.0, 1.0)         # dunkler Winter im Norden

    # ------------------------------------------------------- Temperaturklima
    base = (np.interp(lat, LAT_REF, T_REF)
            + orog_t
            - 0.10 * np.clip(lon - 12.0, 0.0, None) * (1.0 - 0.6 * med)   # Osten kühler
            + 0.5 * iceland)
    amp = (5.0 + 6.5 * cont - 1.8 * mar - 1.8 * med
           + 2.5 * dry_iberia + 0.5 * iceland + 0.8 * north * cont)
    phase = 197.0 + 10.0 * (1.0 - cont) - 5.0 * cont      # Wärmemaximum ~Mitte Juli

    days = np.arange(np.datetime64(cfg["start_date"]), np.datetime64(cfg["end_date"]) + 1)
    n_days = days.size
    doy = (days - days.astype("datetime64[Y]")).astype(int) + 1
    season = np.cos(2 * np.pi * (doy[None, :] - phase[:, None]) / 365.25)
    t_clim = base[:, None] + amp[:, None] * season        # Tagesmittel °C

    dl = daylength(lat, doy)                              # (Punkte, Tage)
    seas_pos = np.clip(season, 0.0, None)
    seas_neg = np.clip(-season, 0.0, None)

    # Bewölkung: Grundwert je Zone, im Sommer klarer (Süden stärker),
    # im Winter trüber (außer Atlantik – der ist ganzjährig grau).
    c_base = (0.60 + 0.12 * mar - 0.22 * med + 0.15 * iceland + 0.03 * north)[:, None]
    cloud = (c_base
             - 0.22 * seas_pos * med[:, None]
             - (0.10 + 0.06 * cont[:, None]) * seas_pos * (1.0 - med)[:, None]
             + 0.20 * seas_neg * (1.0 - med)[:, None] * (1.0 - mar - 0.6 * iceland)[:, None]
             + 0.05 * north[:, None] * seas_neg)

    # Relative Feuchte je Zone und Jahreszeit -> Taupunkt über Magnus
    rh_winter = 0.86 - 0.10 * med + 0.02 * mar
    rh_summer = 0.80 - 0.34 * med - 0.14 * dry_iberia + 0.02 * mar
    rh_clim = rh_winter[:, None] + (rh_summer - rh_winter)[:, None] * (season + 1.0) / 2.0

    # --------------------------------------------------------- Niederschlag
    # Westwind-Niederschlag (eigene Kenngröße: Madrid liegt „westlich“, ist aber trocken,
    # deshalb wird die Trockenheit des Mittelmeer-/Meseta-Raums separat abgezogen).
    wet_west = np.exp(-((lon + 8.0) / 12.0) ** 2)
    annual_mm = np.clip(330.0 + 300.0 * wet_west + 160.0 * cont + 1000.0 * orog_p
                        + 260.0 * iceland - 90.0 * med - 200.0 * med * dry_iberia,
                        200.0, 3200.0)
    month_of_day = days.astype("datetime64[M]").astype(int) % 12
    m_idx = np.arange(12, dtype=np.float64)
    w = np.clip(1.0
                + 0.80 * med[:, None] * np.cos(2 * np.pi * (m_idx - 10.0) / 12.0)[None, :]
                + 0.35 * mar[:, None] * np.cos(2 * np.pi * (m_idx - 0.5) / 12.0)[None, :]
                + 0.35 * cont[:, None] * np.cos(2 * np.pi * (m_idx - 6.0) / 12.0)[None, :],
                0.25, 2.2)
    years = n_days / 365.25
    days_per_month = np.bincount(month_of_day, minlength=12).astype(np.float64) / years
    monthly_rate = annual_mm[:, None] * w / (w * days_per_month[None, :]).sum(axis=1, keepdims=True)
    daily_rate = monthly_rate[:, month_of_day]            # mm pro Tag im Monatsmittel

    # ------------------------------------------------------------- Wetterlauf
    print(f"erzeuge {n_days} Tage für {n_pts} Punkte …")
    t_anom = ar1(rng, (n_pts, n_days), 0.74, 2.7)
    c_anom = ar1(rng, (n_pts, n_days), 0.62, 0.19)
    t_mean = t_clim + t_anom

    dtr = np.clip(4.2 + 2.6 * cont[:, None] + 2.4 * seas_pos
                  + 2.0 * med[:, None] * seas_pos + 2.2 * dry_iberia[:, None] * seas_pos
                  + 1.5 * med[:, None] * seas_neg + 2.5 * dry_iberia[:, None] * seas_neg
                  - 1.6 * mar[:, None] - 2.5 * iceland[:, None] + 0.5 * t_anom, 2.0, 18.0)
    tmax = t_mean + dtr / 2 + rng.normal(0, 0.7, size=(n_pts, n_days)).astype(np.float32)
    tmin = t_mean - dtr / 2 + rng.normal(0, 0.7, size=(n_pts, n_days)).astype(np.float32)
    tmax = np.maximum(tmax, tmin + 0.5)

    cloud_day = np.clip(cloud + c_anom, 0.02, 0.995)
    sun_h = np.clip(dl * (1.0 - cloud_day) * 0.93, 0.0, 24.0)

    rh_day = np.clip(rh_clim + 0.10 * c_anom - 0.017 * t_anom, 0.10, 1.0)
    tdew = np.minimum(dew_point(np.clip(t_mean, -45.0, 45.0), rh_day), tmin + 1.5)

    event_mm = 3.2 + 3.4 * mar[:, None] + 3.0 * cont[:, None] * seas_pos
    p_wet = np.clip(daily_rate / np.maximum(event_mm, 0.5), 0.02, 0.72)
    p_wet = np.clip(p_wet * (1.0 + 0.9 * c_anom), 0.005, 0.85)
    wet = rng.random((n_pts, n_days)) < p_wet
    prec = np.where(wet, rng.exponential(1.0, size=(n_pts, n_days)) * event_mm
                    * (1.0 + 0.6 * np.clip(c_anom, 0, None)), 0.0)
    prec = np.clip(prec, 0.0, 180.0).astype(np.float32)

    # ------------------------------------------------------------ Quantisieren
    def q(a, scale, lo):
        return np.clip(np.rint(np.asarray(a, dtype=np.float64) * scale), lo, 127).astype(np.int8)

    store = {
        "tmax": q(tmax, 2.0, -127), "tmin": q(tmin, 2.0, -127), "tdew": q(tdew, 2.0, -127),
        "sun": q(sun_h * 3600.0, 4.0 / 3600.0, 0),   # die API liefert Sekunden
        "prec": q(prec, 2.0, 0),
    }

    mlat = np.arange(cfg["lat_min"], cfg["lat_max"] + 1e-9, cfg["mask_step"])
    mlon = np.arange(cfg["lon_min"], cfg["lon_max"] + 1e-9, cfg["mask_step"])
    mlon2, mlat2 = np.meshgrid(mlon, mlat)
    mask = globe.is_land(mlat2, mlon2)
    if mask.dtype != bool:
        mask = mask > 0.5

    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_dir / "climate_grid.npz",
        lat=lat.astype(np.float32), lon=lon.astype(np.float32), **store,
        land_mask_packed=np.packbits(mask.reshape(-1)),
        land_mask_shape=np.array(mask.shape, dtype=np.int32),
    )
    meta = {
        "source": "SYNTHETISCHE TESTDATEN (scripts/make_dev_grid.py) – keine Messwerte!",
        "attribution": "keine (synthetisch erzeugt)",
        "model": "synthetic",
        "start_date": str(days[0]), "end_date": str(days[-1]),
        "n_days": int(n_days), "n_years": round(float(years), 2), "n_points": int(n_pts),
        "grid_step_deg": cfg["step"],
        "bbox": {"lat_min": cfg["lat_min"], "lat_max": cfg["lat_max"],
                 "lon_min": cfg["lon_min"], "lon_max": cfg["lon_max"]},
        "variables": {k: {"scale": s, "unit": u, "api": "synthetic"} for k, s, u in [
            ("tmax", 2.0, "°C"), ("tmin", 2.0, "°C"), ("tdew", 2.0, "°C"),
            ("sun", 0.25, "h"), ("prec", 0.5, "mm")]},
        "land_mask": {"step": cfg["mask_step"], "shape": list(mask.shape)},
        "coverage": {"fraction": 1.0, "missing_point_days": 0},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/make_dev_grid.py (Klimatologie + AR(1)-Wettergenerator)",
    }
    (out_dir / "climate_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    # Geo-Dateien mitnehmen, damit die App aus dem Verzeichnis laufen kann
    for name in ("cities.json", "countries.geojson"):
        src, dst = DEFAULT_OUT / name, out_dir / name
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)

    size = (out_dir / "climate_grid.npz").stat().st_size / 1e6
    print(f"geschrieben: {out_dir/'climate_grid.npz'} ({size:.1f} MB)")
    print("Achtung: Testdaten. Echte Werte liefert der Workflow „Klimadaten bauen“.")


if __name__ == "__main__":
    main()
