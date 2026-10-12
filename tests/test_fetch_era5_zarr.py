"""Offline-Test der ERA5-Ernte aus einem Zarr-Speicher (scripts/fetch_era5_zarr.py).

Der echte Datenspeicher liegt auf Google Cloud – hier wird stattdessen ein
kleiner, künstlicher Zarr-Speicher im selben Layout gebaut:

  * Längengrade 0…355° (wie ARCO: der Bereich westlich von Null liegt am Ende
    der Achse, die Ernte muss also „um den Nullmeridian herum" lesen),
  * stündliche Zeitachse,
  * Temperaturen in Kelvin, Niederschlag in Metern, Strahlung in J/m² je Stunde.

Alle Werte sind geschlossene Funktionen von Ort und Stunde, deshalb lassen sich
die Tageswerte exakt vorhersagen: Höchst-/Tiefsttemperatur, mittlerer Taupunkt,
Niederschlagssumme und die Zahl der Stunden mit Direkteinstrahlung ≥ 120 W/m²
(= Sonnenscheindauer nach ECMWF-Definition).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

fetch_era5_zarr = pytest.importorskip("fetch_era5_zarr")
xr = pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")
pytest.importorskip("zarr")        # ohne zarr kann der künstliche Speicher nicht entstehen

LAT = np.arange(25.0, 76.0, 5.0)
LON = np.arange(0.0, 360.0, 5.0)
TAGE = 4                                   # 3 Auswertetage + 1 Tag für die letzte Akkumulation
START = "2020-01-01"

ZIEL = {"lat_min": 30.0, "lat_max": 70.0, "lon_min": -10.0, "lon_max": 10.0, "step": 10.0,
        "start_date": "2020-01-01", "end_date": "2020-01-03"}


# ------------------------------------------------------------- künstliche Daten
def basis_temperatur(lat):
    return 300.0 - 0.4 * np.abs(lat - 45.0)


def stundenwerte() -> dict[str, np.ndarray]:
    """(Stunden, Breiten, Längen) – geschlossene Funktionen, exakt prüfbar."""
    form = (TAGE * 24, LAT.size, LON.size)
    tt = (np.arange(TAGE * 24) % 24)[:, None, None].astype(np.float64)
    la = LAT[None, :, None]
    lo = LON[None, None, :]
    t2m = basis_temperatur(la) + 8.0 * np.sin(2 * np.pi * (tt - 9) / 24) + 0.01 * lo
    d2m = basis_temperatur(la) - 5.0 + 0.01 * tt
    tp = np.where((tt == 6) | (tt == 7), 0.001, 0.0)          # 1 mm je Stunde, 06 und 07 UTC
    fdir = np.where((tt >= 8) & (tt <= 17), 500_000.0, 0.0)   # 138,9 W/m² in 10 Stunden
    return {
        "2m_temperature": np.broadcast_to(t2m, form).astype("float32").copy(),
        "2m_dewpoint_temperature": np.broadcast_to(d2m + 0.0 * lo, form).astype("float32").copy(),
        "total_precipitation": np.broadcast_to(tp + 0.0 * lo + 0.0 * la, form).astype("float32").copy(),
        "total_sky_direct_solar_radiation_at_surface":
            np.broadcast_to(fdir + 0.0 * lo + 0.0 * la, form).astype("float32").copy(),
    }


def erwartet(lat: float, lon_speicher: float) -> dict[str, float]:
    """Tageswerte in den Einheiten des Datensatzes (°C, °C, °C, s, mm)."""
    basis = float(basis_temperatur(lat))
    return {
        "tmax": basis + 8.0 + 0.01 * lon_speicher - 273.15,
        "tmin": basis - 8.0 + 0.01 * lon_speicher - 273.15,
        "tdew": basis - 5.0 + 0.01 * 11.5 - 273.15,
        "sun": 10 * 3600.0,
        "prec": 2.0,
    }


@pytest.fixture(scope="module")
def zarr_speicher(tmp_path_factory) -> Path:
    pfad = tmp_path_factory.mktemp("arco") / "era5-fake.zarr"
    werte = stundenwerte()
    ds = xr.Dataset(
        {k: (("time", "latitude", "longitude"), v) for k, v in werte.items()},
        coords={"time": pd.date_range(START, periods=TAGE * 24, freq="h"),
                "latitude": LAT, "longitude": LON},
        attrs={"source": "Test-Speicher im ARCO-Layout"},
    )
    ds.to_zarr(pfad, mode="w")
    return pfad


def konfiguration(speicher: Path, arbeitsverzeichnis: Path) -> dict:
    cfg = dict(fetch_era5_zarr.BASE_CFG)
    cfg.update(fetch_era5_zarr.ZARR_CFG)
    cfg.update(ZIEL)
    cfg.update({
        "zarr_store": str(speicher),
        "mask_step": 0.5,
        "days_per_read": 2,             # erzwingt mehrere Leseblöcke
        "max_minutes": 5,
        "checkpoint_dir": str(arbeitsverzeichnis / "checkpoints"),
        "test_mode": True,
    })
    return cfg


# ------------------------------------------------------------------------- Tests
def test_ernte_liefert_exakte_tageswerte(zarr_speicher, tmp_path):
    out = tmp_path / "data"
    cfg = konfiguration(zarr_speicher, tmp_path)
    fetch_era5_zarr.run(cfg, out, progress=None)

    grid = np.load(out / "climate_grid.npz")
    meta = json.loads((out / "climate_meta.json").read_text())
    assert meta["model"] == "era5"                       # darf nicht als „synthetisch" gelten
    assert meta["coverage"]["fraction"] == 1.0
    assert meta["n_days"] == 3
    assert set(grid.files) >= {"lat", "lon", "tmax", "tmin", "tdew", "sun", "prec",
                               "land_mask_packed"}
    lat, lon = grid["lat"].astype(float), grid["lon"].astype(float)
    assert lat.shape == lon.shape == (grid["tmax"].shape[0],)
    assert grid["tmax"].shape == (lat.size, 3)

    # Landmaske: dieselbe wie global_land_mask für das Zielgitter liefert
    lats, lons, land = fetch_era5_zarr.target_grid(cfg)
    assert lat.size == int(land.sum())

    for punkt, (pla, plo) in enumerate(zip(lat, lon)):
        soll = erwartet(float(pla), float(plo) % 360.0)
        for name, einheit in (("tmax", 0.5), ("tmin", 0.5), ("tdew", 0.5),
                              ("sun", 3600.0), ("prec", 0.5)):
            ist = grid[name][punkt] / fetch_era5_zarr.SCALES[name]
            if name == "sun":
                ist = ist / 3600.0 * 3600.0                # Sekunden bleiben Sekunden
            assert abs(float(ist.mean()) - soll[name]) <= einheit + 1e-6, \
                f"{name} an {pla:.1f}/{plo:.1f}: {ist.mean()} erwartet {soll[name]}"


def test_fortschritt_und_kontrollpunkte(zarr_speicher, tmp_path):
    out = tmp_path / "data2"
    cfg = konfiguration(zarr_speicher, tmp_path)
    cfg["max_minutes"] = 0.0                               # nichts tun, nur prüfen
    geholt = fetch_era5_zarr.run(cfg, out, progress=None)
    assert geholt == 0
    assert not (out / "climate_grid.npz").exists()         # ohne Daten kein Raster

    cfg["max_minutes"] = 5.0
    from build_progress import Progress
    progress = Progress(out_dir=out, token="", publish_every=1e9)
    fetch_era5_zarr.run(cfg, out, progress=progress)
    assert (out / "climate_grid.npz").exists()
    fortschritt = json.loads((out / "build_progress.json").read_text())
    assert fortschritt["status"] == "done"
    assert fortschritt["percent"] == 100.0
    # vollständiger Lauf räumt seine Kontrollpunkte auf
    assert not list(fetch_era5_zarr.checkpoint_dir(cfg).glob("y*.npz"))


def test_achsenzuordnung_ueber_den_nullmeridian():
    achse = np.arange(0.0, 360.0, 0.25)
    signed = np.where(achse > 180.0, achse - 360.0, achse)
    ziele = np.array([-25.0, -24.25, -0.25, 0.0, 0.75, 44.75])
    idx = fetch_era5_zarr.axis_index(signed, ziele)
    assert list(idx) == [1340, 1343, 1439, 0, 3, 179]
    abschnitte = fetch_era5_zarr.segments(idx)
    assert abschnitte == [[1340, 1343, 1439], [0, 3, 179]]
    assert list(fetch_era5_zarr.positions_in(abschnitte)) == [0, 3, 99, 100, 103, 279]


def test_abschnitte_bei_fallender_achse_und_umbruch():
    """Der Speicher darf die Achse fallend zählen (90° -> -90°) – dann zeigen die
    Indizes nach unten und müssen trotzdem EIN zusammenhängendes Fenster bleiben."""
    # Ziel aufsteigend (34 .. 71.5 N), Achse fallend gespeichert (90 -> -90):
    # Index 224 = 34.0°, Index 74 = 71.5°
    fallend = list(range(224, 71, -3))
    abschnitte = fetch_era5_zarr.segments(np.array(fallend))
    assert abschnitte == [fallend], "ein zusammenhängendes Fenster, nicht 51 einzelne"
    assert fetch_era5_zarr.positions_in(abschnitte).tolist() == list(range(150, -1, -3)), \
        "Zielpunkte im Block (fallend gelesen, aufsteigend zugeordnet)"
    achse = np.arange(90, -90.01, -0.25)
    assert fetch_era5_zarr.axis_slice(abschnitte[0], True, achse) == slice(71.5, 34.0), \
        "bei fallender Achse muss der Slice groß -> klein laufen"

    umbruch = np.array([1340, 1343, 1439, 0, 3, 179])   # Längengrade 335..359.75, 0..44.75
    assert fetch_era5_zarr.segments(umbruch) == [[1340, 1343, 1439], [0, 3, 179]]
    doppel = np.array([5, 5, 6])                        # Zielpunkt doppelt -> eigener Abschnitt
    assert fetch_era5_zarr.segments(doppel) == [[5], [5, 6]]
    assert fetch_era5_zarr.positions_in([[5], [5, 6]]).tolist() == [0, 1, 2]


def test_tagesreduktion_mit_luecken():
    cfg = {"sun_threshold_w_m2": 120.0}
    # ein Tag, aber nur 20 von 24 Stunden geliefert -> Rest gilt als Lücke
    block = np.full((20, 1, 1), np.nan, dtype=np.float32)
    block[:, 0, 0] = 290.0
    tmax = fetch_era5_zarr.to_daily(block, 1, "max", cfg)
    assert np.isnan(tmax).all()                            # zu kurz -> Lücke, kein Fantasiewert

    voll = np.full((24, 1, 1), np.nan, dtype=np.float32)
    voll[:12, 0, 0] = 280.0
    voll[12:, 0, 0] = 300.0
    assert fetch_era5_zarr.to_daily(voll, 1, "max", cfg)[0, 0, 0] == pytest.approx(300 - 273.15)
    assert fetch_era5_zarr.to_daily(voll, 1, "min", cfg)[0, 0, 0] == pytest.approx(280 - 273.15)
    assert fetch_era5_zarr.to_daily(voll, 1, "mean", cfg)[0, 0, 0] == pytest.approx(290 - 273.15)

    regen = np.zeros((24, 1, 1), dtype=np.float32)
    regen[[3, 9], 0, 0] = [0.002, -1e-9]                   # negative Artefakte gibt es in ERA5
    assert fetch_era5_zarr.to_daily(regen, 1, "sum_mm", cfg)[0, 0, 0] == pytest.approx(2.0)

    strahlung = np.zeros((24, 1, 1), dtype=np.float32)
    strahlung[8:18, 0, 0] = 120.0 * 3600.0                 # genau an der Schwelle
    strahlung[19, 0, 0] = 119.9 * 3600.0                   # knapp darunter
    sonnig = fetch_era5_zarr.to_daily(strahlung, 1, "sunshine_hours", cfg)
    assert sonnig[0, 0, 0] == pytest.approx(10.0)
