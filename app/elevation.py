"""Höhenkorrektur für Stadtwerte auf einem groben Klimaraster.

Das Klimaraster hat 0,75° Schrittweite. Seine Zellen sind Flächenmittel über
grob 50–85 km; in Gebirgen liegt ihre mittlere Höhe oft mehr als 1.000 m über
einem Talort. Meran (310 m) bekam deshalb die Tageswerte einer Zelle, deren
Landfläche im Mittel bei etwa 1.996 m liegt – im Juli zeigte die Ortskarte
16,0 °C statt stationsnaher ~30 °C Tageshöchsttemperatur.

Für **Stadtwerte** (nicht für die Kartenfläche) werden die Temperaturen der
zugeordneten Rasterzelle deshalb auf die Höhe des Ortes umgerechnet:

* Tageshöchsttemperatur: 6,5 K je 1.000 m (übliche mittlere Temperaturabnahme
  mit der Höhe; gegen Stationsnormalwerte der Alpen-Talorte geprüft),
* Tagestiefsttemperatur: 5,0 K je 1.000 m (nächtliche Talinversionen dämpfen
  den Effekt),
* Taupunkt: Magnus-Formel bei unveränderter relativer Feuchte, ausgehend vom
  Tagesmittel aus Höchst- und Tiefstwert – dieselbe Annahme wie bei den
  Klimaprojektionen in :mod:`app.projections`.

Sonne und Niederschlag werden nicht korrigiert: Dafür gibt es ohne feineres
Raster keine belastbare Höhenbeziehung. Die Korrektur ist eine physikalische
Näherung, kein Stationsmesswert; sie wird in den API-Antworten offengelegt.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# Temperaturabnahme mit der Höhe, K je Kilometer.
LAPSE_TMAX_C_PER_KM = 6.5
LAPSE_TMIN_C_PER_KM = 5.0
# Größere Höhendifferenzen als das kommen im Datensatz praktisch nicht vor;
# die Kappung verhindert Ausreißer durch falsch zugeordnete Orte.
ELEV_DIFF_MIN_M = -1000.0
ELEV_DIFF_MAX_M = 2000.0

METHOD = "lapse_rate_tmax_6.5_tmin_5.0_dewpoint_constant_rh"
METHOD_LABEL = ("Höhenkorrektur auf Ortshöhe (Tmax 6,5 K/km, Tmin 5,0 K/km, "
                "Taupunkt bei gleicher relativer Feuchte)")


def load_grid_elevation(data_dir: Path, lat: np.ndarray,
                        lon: np.ndarray) -> tuple[np.ndarray | None, dict]:
    """Mittlere Zellenhöhe zu jedem Rasterpunkt – oder ``None``, wenn unbrauchbar.

    Die Datei muss exakt dasselbe Raster beschreiben wie ``climate_grid.npz``.
    Passt sie nicht (alte Kopie, anderes Raster), wird lieber gar nicht
    korrigiert, als Temperaturen mit fremden Höhen zu verschieben.
    """
    path = Path(data_dir) / "grid_elevation.npz"
    if not path.exists():
        return None, {}
    try:
        with np.load(path) as z:
            elev = np.asarray(z["elev"], dtype=np.float64)
            file_lat = np.asarray(z["lat"], dtype=np.float64)
            file_lon = np.asarray(z["lon"], dtype=np.float64)
    except Exception:  # noqa: BLE001 – kaputte Höhendatei darf die App nicht stoppen
        return None, {}
    if elev.shape != lat.shape or file_lat.shape != lat.shape:
        return None, {}
    if not (np.allclose(file_lat, lat, atol=1e-4) and np.allclose(file_lon, lon, atol=1e-4)):
        return None, {}
    if not np.isfinite(elev).all():
        return None, {}
    meta_path = Path(data_dir) / "grid_elevation_meta.json"
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:  # noqa: BLE001
            meta = {}
    return elev, meta


def elevation_diff_m(grid_elev_m, target_elev_m):
    """Gekappte Höhendifferenz Rasterzelle − Ort (positiv: Ort liegt tiefer)."""
    diff = np.asarray(grid_elev_m, dtype=np.float64) - np.asarray(target_elev_m, dtype=np.float64)
    return np.clip(diff, ELEV_DIFF_MIN_M, ELEV_DIFF_MAX_M)


def temperature_shift_c(diff_m, lapse_c_per_km: float):
    """Temperaturverschiebung in °C für eine Höhendifferenz in Metern."""
    return np.asarray(diff_m, dtype=np.float64) * float(lapse_c_per_km) / 1000.0


def dewpoint_c(tmean_c, dewpoint_c, delta_c):
    """Taupunkt nach einer Temperaturverschiebung bei gleicher relativer Feuchte.

    Magnus-Formel über Wasser, Sättigung bei 100 % gekappt – identische Annahme
    wie :func:`app.projections.estimate_dewpoint`.
    """
    a, b = 17.625, 243.04
    tmean = np.asarray(tmean_c, dtype=np.float64)
    dew = np.asarray(dewpoint_c, dtype=np.float64)
    rh_log = np.minimum(a * dew / (b + dew) - a * tmean / (b + tmean), 0.0)
    warmed = tmean + np.asarray(delta_c, dtype=np.float64)
    gamma = rh_log + a * warmed / (b + warmed)
    return b * gamma / (a - gamma)


def adjustment_info(city_elev_m: float, grid_elev_m: float, diff_m: float) -> dict:
    """Offenlegung einer Höhenkorrektur für API-Antworten (eine Stadt)."""
    return {
        "method": METHOD,
        "method_label": METHOD_LABEL,
        "city_elev_m": round(float(city_elev_m)),
        "grid_elev_m": round(float(grid_elev_m)),
        "elev_diff_m": round(float(diff_m)),
        "tmax_shift_c": round(float(temperature_shift_c(diff_m, LAPSE_TMAX_C_PER_KM)), 1),
        "tmin_shift_c": round(float(temperature_shift_c(diff_m, LAPSE_TMIN_C_PER_KM)), 1),
        "note": ("Temperaturen der Rasterzelle auf die Höhe des Ortes umgerechnet; "
                 "Sonne und Niederschlag bleiben Rasterwerte. Näherung, kein Stationsmesswert."),
    }
