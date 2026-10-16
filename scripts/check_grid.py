#!/usr/bin/env python3
"""Plausibilitäts- und Vollständigkeitsprüfung für ``app/data/climate_grid.npz``.

Dieses Skript ist das **Qualitätstor** der Datenpipeline: Es vergleicht den
Datensatz gegen öffentliche Klimanormalwerte (~1991–2020) von 22 europäischen
Städten (Monatsmittel der Höchst-/Tiefsttemperatur im Januar und Juli,
Sonnenscheindauer, Jahresniederschlag) und gegen strukturelle Erwartungen
(Jahresgang, Wertebereiche, Vollständigkeit laut ``coverage``).

Ergebnis wird als ``app/data/quality_report.json`` geschrieben und von der App
angezeigt (Banner/Hinweis, wenn etwas nicht stimmt). Rückgabewert:
0 = Tor bestanden, 1 = harte Fehler (Daten nicht verwendbar).

Warum das nötig ist: Ein stillschweigend kaputter Datensatz (z. B. synthetische
Testdaten oder ein abgebrochener Abruf mit Lücken) erzeugt genau die
„unplausiblen“ Kartenbilder, die man sonst erst auf der Website bemerkt.

Aufruf:  python scripts/check_grid.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.climate import SCALES, UNIT_FACTORS, ClimateData, Params  # noqa: E402

# Referenz: Klimanormalwerte ~1991–2020 (Stationsmittel, gerundet).
# name: (lat, lon, tmaxJan, tmaxJul, tminJan, tminJul, SonneJan h/Tag, SonneJul h/Tag, mm/Jahr)
REFERENCE = {
    "Madrid":    (40.42, -3.70, 10.0, 33.5, 1.5, 17.5, 4.5, 11.5, 380),
    "Sevilla":   (37.39, -5.98, 16.5, 36.5, 6.0, 20.0, 6.0, 12.0, 530),
    "Lissabon":  (38.72, -9.14, 14.8, 28.0, 8.3, 18.3, 5.0, 11.0, 700),
    "Athen":     (37.98, 23.73, 13.0, 33.5, 7.0, 24.0, 4.5, 12.0, 400),
    "Rom":       (41.90, 12.50, 12.5, 31.0, 3.5, 19.0, 4.0, 10.5, 800),
    "Palermo":   (38.12, 13.36, 14.8, 30.0, 9.8, 22.0, 4.5, 11.0, 610),
    "Nizza":     (43.70, 7.27, 13.0, 27.8, 5.0, 19.0, 5.0, 11.0, 730),
    "Barcelona": (41.39, 2.17, 13.6, 28.6, 5.0, 19.5, 5.0, 9.5, 640),
    "Paris":     (48.86, 2.35, 7.5, 25.2, 2.8, 15.0, 2.0, 7.0, 640),
    "Berlin":    (52.52, 13.40, 3.4, 24.8, -1.9, 14.8, 1.5, 7.0, 570),
    "München":   (48.14, 11.58, 3.5, 24.0, -3.7, 13.8, 2.0, 7.5, 950),
    "Zürich":    (47.38, 8.54, 3.4, 24.6, -2.0, 14.6, 1.5, 7.5, 1100),
    "Wien":      (48.21, 16.37, 4.3, 26.3, -1.7, 16.0, 2.0, 8.0, 620),
    "Dublin":    (53.35, -6.26, 8.2, 19.5, 2.8, 12.0, 2.0, 5.0, 730),
    "London":    (51.51, -0.13, 8.3, 23.2, 2.6, 14.2, 1.8, 6.8, 640),
    "Kopenhagen": (55.68, 12.57, 3.0, 22.3, -1.7, 14.2, 1.5, 8.0, 610),
    "Stockholm": (59.33, 18.07, -0.6, 22.3, -4.8, 13.3, 1.0, 9.5, 540),
    "Oslo":      (59.91, 10.75, -1.8, 21.5, -6.8, 12.3, 1.0, 8.0, 760),
    "Helsinki":  (60.17, 24.94, -2.8, 21.5, -8.6, 13.0, 1.0, 9.0, 650),
    "Reykjavik": (64.14, -21.94, 2.6, 11.3, -2.0, 8.0, 0.7, 6.0, 820),
    "Warschau":  (52.23, 21.01, 1.2, 24.5, -3.8, 14.3, 1.3, 8.0, 530),
    "Moskau":    (55.75, 37.62, -4.0, 24.3, -9.0, 14.5, 1.0, 9.0, 700),
}

# Toleranzen: ERA5 auf 0,75° geglättet (Höhenlage, Küsten) – deshalb großzügig,
# aber eng genug, um verkehrte Jahreszeiten oder Kaputtdaten sicher zu erkennen.
TOL_T = 4.5          # K, Monatsmittel
TOL_SUN = 2.5        # h/Tag
TOL_PREC = 0.45      # relativer Fehler Jahresniederschlag

# Alpen-Talorte: genau die Fälle, die mit reinen Rasterwerten sichtbar falsch
# werden (Meran zeigte 31 Wohlfühl-Tage im Juli bei Höchstwert ≤ 25 °C, weil
# die zugeordnete Zelle im Mittel auf ~2.000 m liegt, der Ort aber auf 310 m).
# Geprüft werden die *höhenkorrigierten Stadtwerte* gegen Stationsnormalwerte.
# name: (lat, lon, Ortshöhe m, tminJan, tmaxJan, tminJul, tmaxJul, Quelle)
ALPINE_REFERENCE = {
    "Meran":     (46.6682, 11.1595, 310, -3.1, 6.8, 15.1, 30.0,
                  "Landeswetterdienst Südtirol, Station Gratsch (Meran), 2001–2020"),
    "Innsbruck": (47.2627, 11.3945, 587, -4.5, 3.9, 13.1, 24.7,
                  "Klimanormalwerte 1991–2020"),
}
ALPINE_TOL_T = 3.5   # K, Monatsmittel nach Höhenkorrektur (Stationsperiode weicht ab)

warnungen: list[str] = []
fehler: list[str] = []
checks: list[dict] = []
tabelle: list[dict] = []
alpen_tabelle: list[dict] = []


def pruefe(name: str, bedingung: bool, detail: str = "", schwer: bool = False) -> None:
    marker = "✓" if bedingung else ("✗" if schwer else "!")
    print(f"  {marker} {name}" + (f" – {detail}" if detail else ""))
    checks.append({"name": name, "ok": bool(bedingung), "detail": detail,
                   "schwer": bool(schwer)})
    if not bedingung:
        (fehler if schwer else warnungen).append(f"{name}: {detail}")


def monat(cd: ClimateData, idx: int, mon: int):
    """Monatsmittel (tmax, tmin, tdew, Sonne h/Tag) und Monatssumme Regen (mm)."""
    sel = cd.day_month == mon
    out = {}
    for name in ("tmax", "tmin", "tdew"):
        v = cd.raw[name][idx][sel].astype(np.float64) / SCALES[name]
        out[name] = float(v.mean())
    sun = cd.raw["sun"][idx][sel].astype(np.float64) / SCALES["sun"] / 3600.0
    out["sun"] = float(sun.mean())
    prec = cd.raw["prec"][idx][sel].astype(np.float64) / SCALES["prec"]
    out["prec"] = float(prec.sum() / cd.years)
    return out


def stadt_monat(cd: ClimateData, city_idx: int, mon: int):
    """Monatsmittel einer Stadt nach Höhenkorrektur (wie in der Detailkarte)."""
    sel = cd.day_month == mon
    out = {}
    for name in ("tmax", "tmin", "tdew", "sun"):
        v = cd._city_raw_adjusted(name, slice(None), "all", [city_idx])[0].astype(np.float64)
        out[name] = float((v * UNIT_FACTORS[name])[sel].mean())
    return out


def main() -> int:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "app" / "data"
    datei = data_dir / "climate_grid.npz"
    if not datei.exists():
        print("Kein Raster vorhanden:", datei)
        return 1
    cd = ClimateData(data_dir)
    print(f"Datensatz: {cd.n_points} Punkte × {cd.n_days} Tage "
          f"({cd.meta['start_date']} .. {cd.meta['end_date']}), "
          f"{cd.meta.get('model')}, {cd.meta['grid_step_deg']}°")

    print("\nStruktur und Vollständigkeit:")
    pruefe("Zeitreihe deckt den angegebenen Zeitraum ab",
           cd.n_days == int((np.datetime64(cd.meta["end_date"]) - np.datetime64(cd.meta["start_date"])).astype(int)) + 1,
           f"{cd.n_days} Tage", schwer=True)
    pruefe("Alle Monate kommen vor", len(set(cd.day_month.tolist())) == 12, "", schwer=True)
    pruefe("Keine Sentinel-Lücken (-128) übrig",
           all(int(np.min(cd.raw[k])) > -128 for k in ("tmax", "tmin", "tdew", "sun", "prec")),
           "", schwer=True)
    pruefe("Rasterpunkte sind unterschiedlich (keine Dubletten)",
           len({(round(float(a), 3), round(float(b), 3)) for a, b in zip(cd.lat, cd.lon)}) == cd.n_points,
           "", schwer=True)
    cov = cd.coverage
    pruefe("Vollständigkeit ≥ 99,5 % der Punkt-Tage", cov >= 0.995, f"{cov*100:.2f} %", schwer=True)
    pruefe("Datenbasis ist echte ERA5-Reanalyse", not cd.is_synthetic,
           str(cd.meta.get("source"))[:80], schwer=False)

    print("\nWertebereiche (physikalisch plausibel):")
    tmax = cd.raw["tmax"].astype(np.float32) / SCALES["tmax"]
    tdew = cd.raw["tdew"].astype(np.float32) / SCALES["tdew"]
    sun = cd.raw["sun"].astype(np.float32) / SCALES["sun"] / 3600.0
    # Physikalische Grenzen hängen am Ausschnitt: Dieses Raster reicht von Island
    # (72° N) bis in den Ostirak (34° N / 45° O) und bis Archangelsk (65° N).
    # Gemessen wurden dort −43 °C (Kältewelle Januar 2017) und +52 °C
    # (Hitzewelle Juli 2020) – beides reale Extreme, kein Datenfehler. Die alten
    # Grenzen (−35/+48) stammten aus einem engeren Europa-Verständnis und haben
    # echte ERA5-Daten zu Unrecht gesperrt. Ein Kelvin-Leck (~+300 °C) oder eine
    # vertauschte Skala (×2 ⇒ +104 °C) fällt weiterhin sofort auf – zusätzlich
    # wird geprüft, ob die Extreme am erwartbaren Ort und in der erwartbaren
    # Jahreszeit liegen.
    tmax_unten, tmax_oben = -50.0, 55.0
    tmin_unten, tmin_oben = -60.0, 40.0
    pruefe(f"Tageshöchstwerte zwischen {tmax_unten:.0f} und {tmax_oben:+.0f} °C",
           tmax_unten <= tmax.min() and tmax.max() <= tmax_oben,
           f"{tmax.min():.1f} .. {tmax.max():.1f} °C", schwer=True)
    tmin = cd.raw["tmin"].astype(np.float32) / SCALES["tmin"]
    pruefe(f"Tagestiefstwerte zwischen {tmin_unten:.0f} und {tmin_oben:+.0f} °C",
           tmin_unten <= tmin.min() and tmin.max() <= tmin_oben,
           f"{tmin.min():.1f} .. {tmin.max():.1f} °C", schwer=True)

    n_days = tmax.shape[1]
    hei_p, hei_d = divmod(int(np.argmax(tmax)), n_days)
    kalt_p, kalt_d = divmod(int(np.argmin(tmax)), n_days)
    hei_monat = int(cd.day_month[hei_d]) + 1        # day_month zählt ab 0
    kalt_monat = int(cd.day_month[kalt_d]) + 1
    pruefe("Hitzerekord liegt im Süden und im Sommer",
           cd.lat[hei_p] < 50 and hei_monat in (5, 6, 7, 8, 9),
           f"{tmax.max():.1f} °C bei {cd.lat[hei_p]:.1f}° N im Monat {hei_monat}",
           schwer=False)
    pruefe("Kälterekord liegt im Norden und im Winter",
           cd.lat[kalt_p] > 55 and kalt_monat in (1, 2, 3, 11, 12),
           f"{tmax.min():.1f} °C bei {cd.lat[kalt_p]:.1f}° N im Monat {kalt_monat}",
           schwer=False)
    pruefe("Taupunkt überschreitet die Höchsttemperatur nicht",
           float(np.mean(tdew > tmax + 0.6)) < 0.02,
           f"{float(np.mean(tdew > tmax + 0.6))*100:.1f} % der Tage", schwer=True)
    pruefe("Sonnenschein zwischen 0 und 24 h", 0 <= sun.min() and sun.max() <= 24,
           f"{sun.min():.2f} .. {sun.max():.2f} h", schwer=True)
    nordsommer = cd.raw["sun"][cd.lat > 65][:, cd.day_month == 5].astype(np.float32) / 4.0
    pruefe("Polartag im Juni: im Norden über 15 h Sonne möglich", float(nordsommer.max()) > 15,
           f"max {float(nordsommer.max()):.1f} h")

    print("\nVergleich mit Klimanormalwerten (22 Städte, Jan/Jul):")
    print(f"  {'Stadt':11s} {'tmax Jan':>16s} {'tmax Jul':>16s} {'Sonne Jul':>13s} {'mm/Jahr':>14s}")
    dev_t: list[float] = []
    fail_t = 0
    n_t = 0
    sun_fail_n = 0
    prec_fail_n = 0
    for name, (la, lo, tj, tJ, nj, nJ, sj, sJ, py) in REFERENCE.items():
        idx = cd.point_index(la, lo)
        jan, jul = monat(cd, idx, 0), monat(cd, idx, 6)
        prec_year = float(cd.raw["prec"][idx].astype(np.float64).sum() / SCALES["prec"] / cd.years)
        rows = [("tmaxJan", jan["tmax"], tj), ("tmaxJul", jul["tmax"], tJ),
                ("tminJan", jan["tmin"], nj), ("tminJul", jul["tmin"], nJ)]
        dev_t += [abs(g - r) for _, g, r in rows]
        fail_t += sum(abs(g - r) > TOL_T for _, g, r in rows)
        n_t += len(rows)
        sun_fail = abs(jul["sun"] - sJ) > TOL_SUN or abs(jan["sun"] - sj) > TOL_SUN
        prec_fail = abs(prec_year - py) / max(py, 1) > TOL_PREC
        sun_fail_n += int(sun_fail)
        prec_fail_n += int(prec_fail)
        tabelle.append({"ort": name, "tmax_jan": round(jan["tmax"], 1), "ref_tmax_jan": tj,
                        "tmax_jul": round(jul["tmax"], 1), "ref_tmax_jul": tJ,
                        "tmin_jan": round(jan["tmin"], 1), "ref_tmin_jan": nj,
                        "tmin_jul": round(jul["tmin"], 1), "ref_tmin_jul": nJ,
                        "sonne_jan": round(jan["sun"], 1), "ref_sonne_jan": sj,
                        "sonne_jul": round(jul["sun"], 1), "ref_sonne_jul": sJ,
                        "niederschlag_jahr": round(prec_year), "ref_niederschlag_jahr": py})
        print(f"  {name:11s} {jan['tmax']:7.1f}/{tj:<7} {jul['tmax']:7.1f}/{tJ:<7} "
              f"{jul['sun']:6.1f}/{sJ:<5} {prec_year:8.0f}/{py:<5}")
    mean_dev = float(np.mean(dev_t))
    pruefe(f"Ø Abweichung der Monatsmittel ≤ {TOL_T} K", mean_dev <= TOL_T,
           f"{mean_dev:.2f} K über {n_t} Werte", schwer=True)
    pruefe("Höchstens ein Drittel der Temperatur-Vergleiche außerhalb der Toleranz",
           fail_t / n_t <= 1 / 3, f"{fail_t}/{n_t}", schwer=True)
    pruefe("Sonnenschein im Rahmen der Normalwerte", sun_fail_n <= len(REFERENCE) // 3,
           f"{sun_fail_n}/{len(REFERENCE)} Städte außerhalb ±{TOL_SUN} h", schwer=True)
    pruefe("Jahresniederschlag im Rahmen der Normalwerte", prec_fail_n <= len(REFERENCE) // 2,
           f"{prec_fail_n}/{len(REFERENCE)} Städte außerhalb ±{int(TOL_PREC*100)} %")

    print("\nAlpen-Talorte (Stationswerte gegen höhenkorrigierte Stadtwerte):")
    pruefe("Höhendaten für die Stadt-Korrektur vorhanden", cd.city_elevation_adjusted,
           "grid_elevation.npz + elev_m in cities.json", schwer=True)
    alpen_dev: list[float] = []
    alpen_fail = 0
    alpen_n = 0
    for name, (la, lo, elev, nj_min, nj_max, nJ_min, nJ_max, quelle) in ALPINE_REFERENCE.items():
        city_idx = cd._city_index_at(la, lo, max_km=5.0)
        if city_idx is None:
            pruefe(f"Alpen-Referenzort {name} in der Städteliste", False,
                   "nicht gefunden", schwer=True)
            continue
        jan, jul = stadt_monat(cd, city_idx, 0), stadt_monat(cd, city_idx, 6)
        rows = [("tmaxJan", jan["tmax"], nj_max), ("tminJan", jan["tmin"], nj_min),
                ("tmaxJul", jul["tmax"], nJ_max), ("tminJul", jul["tmin"], nJ_min)]
        alpen_dev += [abs(g - r) for _, g, r in rows]
        alpen_fail += sum(abs(g - r) > ALPINE_TOL_T for _, g, r in rows)
        alpen_n += len(rows)
        adjustment = cd.city_adjustment(city_idx) or {}
        alpen_tabelle.append({
            "ort": name, "elev_m": elev,
            "grid_elev_m": adjustment.get("grid_elev_m"),
            "tmax_jan": round(jan["tmax"], 1), "ref_tmax_jan": nj_max,
            "tmin_jan": round(jan["tmin"], 1), "ref_tmin_jan": nj_min,
            "tmax_jul": round(jul["tmax"], 1), "ref_tmax_jul": nJ_max,
            "tmin_jul": round(jul["tmin"], 1), "ref_tmin_jul": nJ_min,
            "quelle": quelle,
        })
        print(f"  {name:11s} Ort {elev} m, Zelle {adjustment.get('grid_elev_m')} m · "
              f"Jan {jan['tmax']:5.1f}/{nj_max:<5} Jul {jul['tmax']:5.1f}/{nJ_max:<5} "
              f"(korrigiert, Quelle: {quelle})")
    pruefe(f"Alpen-Talorte nach Höhenkorrektur ≤ {ALPINE_TOL_T} K daneben",
           alpen_n > 0 and alpen_fail == 0,
           f"{alpen_fail}/{alpen_n} Werte außerhalb ±{ALPINE_TOL_T} K", schwer=True)

    print("\nJahresgang (verkehrte Jahreszeiten?):")
    sued = [cd.point_index(REFERENCE[k][0], REFERENCE[k][1]) for k in
            ("Madrid", "Sevilla", "Athen", "Rom", "Palermo", "Lissabon")]
    nord = [cd.point_index(REFERENCE[k][0], REFERENCE[k][1]) for k in
            ("Stockholm", "Oslo", "Helsinki", "Reykjavik")]
    for label, idxs, min_delta in (("Südeuropa", sued, 10.0), ("Nordeuropa", nord, 12.0)):
        deltas = []
        for i in idxs:
            jul = float(cd.raw["tmax"][i][cd.day_month == 6].astype(np.float64).mean()) / SCALES["tmax"]
            jan = float(cd.raw["tmax"][i][cd.day_month == 0].astype(np.float64).mean()) / SCALES["tmax"]
            deltas.append(jul - jan)
        pruefe(f"{label}: Juli deutlich wärmer als Januar",
               float(np.mean(deltas)) > min_delta,
               f"Ø +{np.mean(deltas):.1f} K (Jul − Jan)", schwer=True)

    print("\nAuswertung (Preset „Ausgewogen mild“):")
    mild = Params(tmax_min=18, tmax_max=28, tmin_min=8, tmin_max=20, tdew_max=17, sun_min=3, prec_max=99)
    counts = cd.counts(mild)

    def punkt(lat: float, lon: float) -> float:
        return float(counts[cd.point_index(lat, lon)])

    s_sued, s_nord, s_mitte = punkt(37.4, -5.9), punkt(68.0, 20.0), punkt(50.0, 8.7)
    print(f"    Sevilla {s_sued:.0f} Tage/Jahr · Rhein-Main {s_mitte:.0f} · Nordschweden {s_nord:.0f}")
    pruefe("Südspanien hat mehr Wohlfühl-Tage als Nordskandinavien", s_sued > s_nord + 30,
           f"{s_sued:.0f} vs. {s_nord:.0f}", schwer=True)
    pruefe("Südspanien liegt über 100 Tagen/Jahr", s_sued > 100, f"{s_sued:.0f}", schwer=True)
    pruefe("Norden liegt unter 120 Tagen/Jahr", s_nord < 120, f"{s_nord:.0f}")
    pruefe("Mittelwerte sind endlich", bool(np.isfinite(counts).all()), "", schwer=True)

    print("\nMonatsmodus:")
    juli = cd.counts(mild, month=6)
    print(f"    Sevilla im Juli: {juli[cd.point_index(37.4, -5.9)]:.1f} Tage · "
          f"Nordschweden: {juli[cd.point_index(68.0, 20.0)]:.1f} Tage")
    pruefe("Juli-Werte überschreiten die Monatslänge nicht",
           float(juli.max()) <= 31.0 + 1e-6, f"max {juli.max():.1f}", schwer=True)

    ok = not fehler
    report = {
        "ok": ok,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": cd.meta.get("model"),
        # Fingerabdruck: Die App prüft damit, ob dieser Bericht überhaupt zum
        # geladenen Raster gehört – ein liegengebliebener Bericht aus einem
        # älteren Datenlauf soll nicht echte Daten sperren.
        "dataset": {"generated_at": cd.meta.get("generated_at"),
                    "n_points": int(cd.n_points), "n_days": int(cd.n_days),
                    "start_date": cd.meta.get("start_date"),
                    "end_date": cd.meta.get("end_date"),
                    "grid_step_deg": cd.meta.get("grid_step_deg")},
        "coverage": round(float(cov), 6),
        "mean_temp_deviation_k": round(mean_dev, 2),
        "alpine_mean_temp_deviation_k": (round(float(np.mean(alpen_dev)), 2)
                                         if alpen_dev else None),
        "checks": checks,
        "warnings": warnungen,
        "errors": fehler,
        "referenz": tabelle,
        "alpen_referenz": alpen_tabelle,
    }
    out = data_dir / "quality_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print()
    print(f"{'TOR BESTANDEN' if ok else 'TOR NICHT BESTANDEN'}: "
          f"{len(fehler)} Fehler, {len(warnungen)} Warnungen")
    print(f"Bericht: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
