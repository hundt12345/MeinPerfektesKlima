#!/usr/bin/env python3
"""Build small, reproducible CMIP6 regional monthly delta tables from the AR6 Atlas.

Only the derived JSON is shipped. Raw CSVs are cached outside the source tree.
Uses authenticated `gh api` when available; no credentials enter the output.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

import numpy as np

REPO = "SantanderMetGroup/ATLAS"
REVISION = "a1687c6db5590eb9b618fd4e1ebba4b842084031"
REGIONS = ("GIC", "NEU", "WCE", "EEU", "MED", "RAR", "WCA")
SCENARIOS = ("ssp126", "ssp245", "ssp585")
WINDOWS = {"2030": (2021, 2040), "2050": (2041, 2060)}


def api(path: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPO}/{path}"]))


def monthly_means(text: str, start: int, end: int) -> np.ndarray:
    reader = csv.DictReader(line for line in text.splitlines() if not line.startswith("#"))
    rows = [[] for _ in range(12)]
    dates = set()
    for row in reader:
        year, month = map(int, row["date"].split("-"))
        if start <= year <= end:
            if row["date"] in dates:
                raise ValueError("Duplicate month")
            dates.add(row["date"])
            rows[month - 1].append([float(row[region]) for region in REGIONS])
    if len(dates) != (end - start + 1) * 12:
        raise ValueError(f"Incomplete period {start}–{end}")
    out = np.array([np.mean(row, axis=0) for row in rows]).T  # region, month
    if not np.isfinite(out).all():
        raise ValueError("Missing regional values")
    return out


def build(out: Path, cache: Path, baseline: tuple[int, int]) -> dict:
    tree = api(f"git/trees/{REVISION}?recursive=1")
    if tree.get("truncated"):
        raise ValueError("Truncated upstream tree")
    files = {entry["path"]: entry for entry in tree["tree"]}
    inputs = {}
    for scenario in SCENARIOS:
        by_variable = {}
        for variable in ("tas", "pr"):
            prefix = f"datasets-aggregated-regionally/data/CMIP6/CMIP6_{variable}_land/"
            by_variable[variable] = {
                path.split("/")[-1].replace(f"_{scenario}_", "_"): entry
                for path, entry in files.items()
                if path.startswith(prefix) and f"_{scenario}_" in path and path.endswith(".csv")
            }
        inputs[scenario] = by_variable
    # Consistent model/run selection across all three scenarios and both variables.
    common = set.intersection(*(set(values) for group in inputs.values() for values in group.values()))
    entries = {entry["sha"]: entry for group in inputs.values() for values in group.values()
               for member, entry in values.items() if member in common}
    region_entry = files["reference-regions/IPCC-WGI-reference-regions-v4.geojson"]
    entries[region_entry["sha"]] = region_entry
    cache.mkdir(parents=True, exist_ok=True)

    def download(entry):
        file = cache / entry["sha"]
        if not file.exists():
            blob = api(f"git/blobs/{entry['sha']}")
            data = base64.b64decode(blob["content"])
            # Git object identity guards against stale/wrong cache content.
            identity = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if identity != entry["sha"]:
                raise ValueError("Blob checksum mismatch")
            file.write_bytes(data)
        data = file.read_bytes()
        identity = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if identity != entry["sha"]:
            raise ValueError("Cached blob checksum mismatch")
        return entry["sha"], data.decode()

    with ThreadPoolExecutor(max_workers=6) as pool:
        texts = dict(pool.map(download, entries.values()))
    monthly = {}
    rejected = []
    valid = []
    for member in sorted(common):
        try:
            for scenario in SCENARIOS:
                for variable in ("tas", "pr"):
                    entry = inputs[scenario][variable][member]
                    text = texts[entry["sha"]]
                    unit = "#Units: degC" if variable == "tas" else "#Units: mm/day"
                    if unit not in text:
                        raise ValueError(f"Unexpected units for {variable}")
                    for window, (start, end) in {"baseline": baseline, **WINDOWS}.items():
                        monthly[member, scenario, variable, window] = monthly_means(text, start, end)
            valid.append(member)
        except ValueError as error:
            rejected.append({"member": member, "reason": str(error)})
    if len(valid) < 10:
        raise ValueError(f"Only {len(valid)} complete models; need at least 10. {rejected}")

    regions_geo = json.loads(texts[region_entry["sha"]])
    result = {
        "schema_version": 1,
        "method": "regional_monthly_delta_change",
        "source": "CMIP6 / IPCC AR6 Atlas, regional monthly land means",
        "source_url": f"https://github.com/{REPO}/tree/{REVISION}/datasets-aggregated-regionally",
        "revision": REVISION, "license": "CC-BY-4.0",
        "attribution": "Iturbide et al. (2021), doi:10.5281/zenodo.3691645; WCRP and CMIP6 modelling groups. Modified: regional monthly deltas applied to ERA5 analog days.",
        "baseline": {"start_year": baseline[0], "end_year": baseline[1]},
        "models": [m.removeprefix("CMIP6_").removesuffix(".csv") for m in valid],
        "rejected": rejected,
        "regions": {f["properties"]["Acronym"]: {"name": f["properties"]["Name"], "geometry": f["geometry"]}
                    for f in regions_geo["features"] if f["properties"]["Acronym"] in REGIONS},
        "scenarios": {},
        "inputs": [{"path": e["path"], "git_blob_sha": sha,
                    "sha256": hashlib.sha256(texts[sha].encode()).hexdigest()} for sha, e in sorted(entries.items())],
    }
    for scenario in SCENARIOS:
        result["scenarios"][scenario] = {}
        for window, (start, end) in WINDOWS.items():
            temperatures, precipitation = [], []
            for member in valid:
                temperatures.append(monthly[member, scenario, "tas", window] - monthly[member, scenario, "tas", "baseline"])
                base = monthly[member, scenario, "pr", "baseline"]
                if np.any(base <= 0):
                    raise ValueError("Non-positive baseline precipitation")
                precipitation.append(monthly[member, scenario, "pr", window] / base)
            temperatures = np.asarray(temperatures)
            precipitation = np.asarray(precipitation)
            result["scenarios"][scenario][window] = {
                "start_year": start, "end_year": end,
                "regions": {region: {
                    "temperature_delta_c": np.median(temperatures[:, i], axis=0).round(4).tolist(),
                    "temperature_delta_p10_c": np.percentile(temperatures[:, i], 10, axis=0).round(4).tolist(),
                    "temperature_delta_p90_c": np.percentile(temperatures[:, i], 90, axis=0).round(4).tolist(),
                    "precipitation_ratio": np.median(precipitation[:, i], axis=0).round(4).tolist(),
                    "precipitation_ratio_p10": np.percentile(precipitation[:, i], 10, axis=0).round(4).tolist(),
                    "precipitation_ratio_p90": np.percentile(precipitation[:, i], 90, axis=0).round(4).tolist(),
                } for i, region in enumerate(REGIONS)},
            }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("app/data/projections.json"))
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "klima-atlas")
    parser.add_argument("--baseline-start", type=int, default=2016)
    parser.add_argument("--baseline-end", type=int, default=2025)
    args = parser.parse_args()
    result = build(args.out, args.cache, (args.baseline_start, args.baseline_end))
    print(f"Built {args.out}: {len(result['models'])} models, {len(result['regions'])} regions, 3 scenarios, 2 windows")
