#!/usr/bin/env python3
"""Rendert Beispiele für die Dokumentation (docs/) aus dem aktuellen Raster.

Aufruf:  python scripts/make_docs_image.py
Ergebnis: docs/beispiel-jahr.png, docs/beispiel-juli.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.climate import ClimateData, Params  # noqa: E402

BEISPIELE = [
    ("beispiel-jahr.png", "Ganzes Jahr, ausgewogen mild",
     Params(tmax_min=18, tmax_max=28, tmin_min=8, tmin_max=20, tdew_max=17, sun_min=3, prec_max=99),
     None),
    ("beispiel-juli.png", "Juli, warm & schweißfrei",
     Params(tmax_min=24, tmax_max=33, tmin_min=14, tmin_max=24, tdew_max=15, sun_min=5, prec_max=99),
     6),
]


def main() -> None:
    data_dir = ROOT / "app" / "data"
    if not (data_dir / "climate_grid.npz").exists():
        raise SystemExit("Kein Raster vorhanden – erst Daten erzeugen.")
    cd = ClimateData(data_dir)
    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)

    for name, label, params, month in BEISPIELE:
        png, info = cd.render(params, month, width=1400)
        (out_dir / name).write_bytes(png)
        rows = cd.ranking(params, month, limit=5)
        print(f"{name}: {label} – Skala 0..{info['vmax']:.0f}, "
              f"Mittel {info['mean']:.0f} Tage")
        for r in rows:
            print(f"    {r['days']:5.1f}  {r['name']} ({r['country']})")
    (out_dir / "beispiele.json").write_text(json.dumps(
        {"hinweis": "Erzeugt von scripts/make_docs_image.py",
         "beispiele": [{"datei": n, "beschriftung": l} for n, l, _, _ in BEISPIELE]},
        indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
