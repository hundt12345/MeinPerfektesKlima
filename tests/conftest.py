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

# Die Tests prüfen die *Mechanik* der App mit dem synthetischen Referenzraster;
# das Datentor der Website (nur echte ERA5-Daten) wird in tests/test_data_gate.py
# separat geprüft. Deshalb ist es hier geöffnet – und der Fortschrittsabruf bleibt
# offline (keine GitHub-Anfragen aus dem Testlauf).
os.environ.setdefault("KLIMA_ALLOW_UNVERIFIED", "1")
os.environ.setdefault("KLIMA_PROGRESS_URL", "")
