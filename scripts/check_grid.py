#!/usr/bin/env python3
"""Plausibilitätsprüfung für ``app/data/climate_grid.npz``.

Prüft, ob der Datensatz vollständig und physikalisch plausibel ist – z. B. ob
Südspanien mehr Wohlfühl-Tage hat als Nordskandinavien und ob die Temperaturen
in sinnvollen Bereichen liegen. Läuft ohne Netz.

Aufruf:  python scripts/check_grid.py
Rückgabewert 0 = in Ordnung, 1 = Auffälligkeiten (Details siehe Ausgabe).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.climate import SCALES, ClimateData, Params  # noqa: E402

warnungen: list[str] = []
fehler: list[str] = []


def pruefe(name: str, bedingung: bool, detail: str = "", schwer: bool = False) -> None:
    marker = "✓" if bedingung else ("✗" if schwer else "!") 
    print(f"  {marker} {name}" + (f" – {detail}" if detail else ""))
    if not bedingung:
        (fehler if schwer else warnungen).append(f"{name}: {detail}")


def main() -> int:
    datei = ROOT / "app" / "data" / "climate_grid.npz"
    if not datei.exists():
        print("Kein Raster vorhanden:", datei)
        return 1
    cd = ClimateData(ROOT / "app" / "data")
    print(f"Datensatz: {cd.n_points} Punkte × {cd.n_days} Tage "
          f"({cd.meta['start_date']} .. {cd.meta['end_date']}), "
          f"{cd.meta.get('model')}, {cd.meta['grid_step_deg']}°")

    print("\nStruktur:")
    pruefe("Zeitreihe deckt den angegebenen Zeitraum ab",
           cd.n_days == int((np.datetime64(cd.meta["end_date"]) - np.datetime64(cd.meta["start_date"])).astype(int)) + 1,
           f"{cd.n_days} Tage", schwer=True)
    pruefe("Alle Monate kommen vor", len(set(cd.day_month.tolist())) == 12, "", schwer=True)
    pruefe("Keine Sentinel-Lücken (-128) in tmax", int(np.min(cd.raw["tmax"])) > -128, "", schwer=True)
    pruefe("Rasterpunkte sind unterschiedlich (keine Dubletten)",
           len({(round(float(a), 3), round(float(b), 3)) for a, b in zip(cd.lat, cd.lon)}) == cd.n_points,
           "", schwer=True)

    print("\nWertebereiche (physikalisch plausibel):")
    tmax = cd.raw["tmax"].astype(np.float32) / SCALES["tmax"]
    tdew = cd.raw["tdew"].astype(np.float32) / SCALES["tdew"]
    # sun ist in Viertelstunden quantisiert: int8-Wert / 4 = Stunden
    sun = cd.raw["sun"].astype(np.float32) / SCALES["sun"] / 3600.0
    pruefe("Tageshöchstwerte zwischen -35 und +48 °C",
           -35 <= tmax.min() and tmax.max() <= 48, f"{tmax.min():.1f} .. {tmax.max():.1f} °C")
    pruefe("Taupunkt überschreitet die Höchsttemperatur nicht",
           float(np.mean(tdew > tmax + 0.6)) < 0.02,
           f"{float(np.mean(tdew > tmax + 0.6))*100:.1f} % der Tage")
    pruefe("Sonnenschein zwischen 0 und 24 h", 0 <= sun.min() and sun.max() <= 24,
           f"{sun.min():.2f} .. {sun.max():.2f} h")
    nordsommer = cd.raw["sun"][cd.lat > 65][:, cd.day_month == 5].astype(np.float32) / 4.0
    pruefe("Polartag im Juni: im Norden über 15 h Sonne möglich", float(nordsommer.max()) > 15,
           f"max {float(nordsommer.max()):.1f} h")

    print("\nPlausibilität der Auswertung (Preset „Ausgewogen mild“):")
    mild = Params(tmax_min=18, tmax_max=28, tmin_min=8, tmin_max=20, tdew_max=17, sun_min=3, prec_max=99)
    counts = cd.counts(mild)

    def punkt(lat: float, lon: float) -> float:
        idx = cd.point_index(lat, lon)
        return float(counts[idx])

    sued = punkt(37.4, -5.9)      # Sevilla
    nord = punkt(68.0, 20.0)      # Nordschweden
    mitte = punkt(50.0, 8.7)      # Rhein-Main
    print(f"    Sevilla {sued:.0f} Tage/Jahr · Rhein-Main {mitte:.0f} · Nordschweden {nord:.0f}")
    pruefe("Südspanien hat mehr Wohlfühl-Tage als Nordskandinavien", sued > nord + 30,
           f"{sued:.0f} vs. {nord:.0f}", schwer=True)
    pruefe("Südspanien liegt über 100 Tagen/Jahr", sued > 100, f"{sued:.0f}")
    pruefe("Norden liegt unter 120 Tagen/Jahr", nord < 120, f"{nord:.0f}")
    pruefe("Mittelwerte sind endlich", bool(np.isfinite(counts).all()), "", schwer=True)

    print("\nMonatsmodus:")
    juli = cd.counts(mild, month=6)
    print(f"    Sevilla im Juli: {juli[cd.point_index(37.4, -5.9)]:.1f} Tage · "
          f"Nordschweden: {juli[cd.point_index(68.0, 20.0)]:.1f} Tage")
    pruefe("Juli-Werte überschreiten die Monatslänge nicht",
           float(juli.max()) <= 31.0 + 1e-6, f"max {juli.max():.1f}")

    print()
    if fehler:
        print(f"{len(fehler)} Fehler, {len(warnungen)} Warnungen")
        return 1
    print(f"Alles in Ordnung ({len(warnungen)} Warnungen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
