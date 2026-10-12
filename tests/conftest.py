"""Stellt für die Tests ein deterministisches, *plausibles* Testraster bereit.

Die im Repo committete ``app/data/climate_grid.npz`` ist je nach Stand entweder
noch nicht gebaut oder ein älterer Stand – die Tests sollen aber unabhängig davon
laufen und physikalisch sinnvolle Erwartungen prüfen können (Juli wärmer als
Januar, Süden sonniger als Norden). Deshalb erzeugt conftest einmalig das
synthetische Referenzraster aus ``scripts/make_dev_grid.py`` (Klimatologie +
Wettergenerator, Abweichung zu Normalwerten ~1–2 K) und weist die App über
``KLIMA_DATA_DIR`` darauf.

Um ein anderes Raster zu testen (z. B. die echten ERA5-Daten):
    KLIMA_DATA_DIR=app/data pytest tests
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = Path(os.environ.get("KLIMA_DEV_GRID", "/tmp/klima-dev-grid"))

if os.environ.get("KLIMA_DATA_DIR"):
    DEV = Path(os.environ["KLIMA_DATA_DIR"])

if not (DEV / "climate_grid.npz").exists():
    DEV.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_dev_grid.py"), "--out", str(DEV)],
        check=True, capture_output=True, text=True,
    )

os.environ["KLIMA_DATA_DIR"] = str(DEV)
DATA_DIR = DEV
