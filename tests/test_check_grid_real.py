"""Das Qualitätstor über dem Datensatz, der tatsächlich im Repo liegt.

Der Datenlauf committet `app/data/climate_grid.npz` nach `main`; diese Prüfung
lässt das Tor (`scripts/check_grid.py`) genau darüber laufen – in einer Kopie,
damit das Repo unberührt bleibt. Sie ist die Sicherung dagegen, dass erneut ein
synthetisches oder halbfertiges Raster deployed wird: Schlägt das Tor fehl, ist
auch die Website gesperrt (HTTP 503).

Fehlt das Raster in der Arbeitskopie (frische Klone ohne Datenlauf), wird
übersprungen – dann prüft `tests/test_data_gate.py` das Verhalten der App.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATEN = ROOT / "app" / "data"

pytestmark = pytest.mark.skipif(not (DATEN / "climate_grid.npz").exists(),
                                reason="Kein Klimaraster im Arbeitsverzeichnis")


def test_qualitaetstor_bestanden(tmp_path):
    kopie = tmp_path / "daten"
    kopie.mkdir()
    for quelle in DATEN.iterdir():
        if quelle.name == "quality_report.json":
            continue                      # Bericht soll frisch entstehen
        if quelle.is_file():
            shutil.copy2(quelle, kopie / quelle.name)

    meta = json.loads((kopie / "climate_meta.json").read_text())
    if meta.get("model") != "era5" and os.environ.get("CI") != "true":
        pytest.skip("Lokales Entwicklungsraster (synthetisch) – streng geprüft wird in CI")

    lauf = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_grid.py"), str(kopie)],
                          capture_output=True, text=True, timeout=900)
    assert lauf.returncode == 0, (lauf.stdout[-4000:] + "\n" + lauf.stderr[-1500:])

    bericht = json.loads((kopie / "quality_report.json").read_text())
    assert bericht["errors"] == [], bericht["errors"]
    assert bericht["ok"] is True
    assert bericht["model"] == "era5", "nur echte ERA5-Daten dürfen im Repo liegen"
    assert bericht["coverage"] >= 0.995, bericht["coverage"]
    assert bericht["mean_temp_deviation_k"] < 4.5, bericht["mean_temp_deviation_k"]
    # Der Bericht muss zum Datensatz gehören, sonst ignoriert die App ihn.
    fingerprint = bericht["dataset"]
    assert fingerprint["generated_at"] == meta["generated_at"]
    assert fingerprint["n_points"] == meta["n_points"]
