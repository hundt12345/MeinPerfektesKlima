#!/usr/bin/env python3
"""Zweite, gezielte Datenquellen-Probe: **Tageswerte** statt Stundenwerte.

Ergebnis der ersten Probe (data/quellen-probe.txt):
  * ``gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3``
    enthält alles Nötige (2m_temperature, 2m_dewpoint_temperature,
    total_precipitation, total_sky_direct_solar_radiation_at_surface), ist aber
    stündlich und in **globalen** Chunks (~3 MB je Zeitstunde und Variable)
    abgelegt: ein Europa-Ausschnitt kostet deshalb pro Monat ~60 s und
    ~12 GB – zehn Jahre wären >1 TB Lesevolumen.
  * WeatherBench2 hat Ordner ``datasets/era5_daily/`` und
    ``datasets/era5-daily-climatology/`` – wenn dort Tages-Maxima/-Minima,
    Taupunkt, Niederschlag und Strahlung liegen, schrumpft die Datenmenge um
    den Faktor 24.

Diese Probe listet diese Speicher tief auf (Variablen, Chunk-Geometrie,
Objektgrößen) und misst einen echten Lesetest über ein Jahr Europa.
"""
from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "meinperfektesklima-quellenprobe"}
GCS = "https://storage.googleapis.com"
BBOX = {"lat_min": 34.0, "lat_max": 72.0, "lon_min": -25.0, "lon_max": 45.0}
JAHR = "2024"

INTERESSE = ("2m_temperature", "maximum_2m_temperature", "minimum_2m_temperature",
             "mean_2m_temperature", "2m_dewpoint_temperature", "dew_point", "total_precipitation",
             "precipitation", "sunshine", "solar_radiation", "surface_solar", "direct_solar",
             "shortwave", "cloud", "t2m", "tp", "ssrd", "fdir", "mx2t", "mn2t", "d2m", "sund")


def line(*a):
    print(*a, flush=True)


def get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def listing(bucket: str, prefix: str, delimiter: str = "/", max_keys: int = 300):
    q = urllib.parse.urlencode({"prefix": prefix, "delimiter": delimiter, "maxResults": max_keys,
                                "fields": "prefixes,items(name,size)"})
    data, err = get_json(f"{GCS}/storage/v1/b/{bucket}/o?{q}")
    if data is None:
        return [], [], err
    return data.get("prefixes", []) or [], data.get("items", []) or [], ""


def sizes(bucket: str, prefix: str, max_keys: int = 200) -> list[int]:
    q = urllib.parse.urlencode({"prefix": prefix, "maxResults": max_keys, "fields": "items(name,size)"})
    data, err = get_json(f"{GCS}/storage/v1/b/{bucket}/o?{q}")
    out = []
    for it in (data or {}).get("items", []) or []:
        try:
            out.append(int(it.get("size") or 0))
        except (TypeError, ValueError):
            pass
    return out


def store_report(bucket: str, store: str, deep_vars: int = 40) -> dict:
    """Variablen + Chunk-Geometrie eines Zarr-Speichers (v2/v3)."""
    base = f"{GCS}/{bucket}/{store.rstrip('/')}"
    info: dict = {"store": store, "format": None, "vars": {}, "attrs": {}, "members": [],
                  "error": ""}
    v3, _ = get_json(f"{base}/zarr.json")
    if v3:
        info["format"] = "v3"
        info["attrs"] = v3.get("attributes", {}) or {}
    else:
        zg, _ = get_json(f"{base}/.zgroup")
        za, _ = get_json(f"{base}/.zattrs")
        if zg is None and za is None:
            info["error"] = "kein Zarr-Wurzelknoten"
            return info
        info["format"] = "v2"
        info["attrs"] = za or {}
        cons, _ = get_json(f"{base}/.zmetadata")
        if cons:
            for key, val in (cons.get("metadata") or {}).items():
                parts = key.split("/")
                if len(parts) == 2 and parts[1] == ".zarray":
                    info["vars"][parts[0]] = {"shape": val.get("shape"), "chunks": val.get("chunks"),
                                              "dtype": val.get("dtype")}
            info["members"] = sorted(info["vars"])
            return info

    prefixes, items, err = listing(bucket, store.rstrip("/") + "/")
    names = [p.rstrip("/").split("/")[-1] for p in prefixes]
    info["members"] = names
    pick = [n for n in names if any(k in n.lower() for k in INTERESSE) or
            n in ("time", "valid_time", "latitude", "longitude", "lat", "lon")]
    for name in (pick or names)[:deep_vars]:
        if info["format"] == "v3":
            meta, _ = get_json(f"{base}/{name}/zarr.json")
            if not meta:
                continue
            info["vars"][name] = {
                "shape": meta.get("shape"),
                "chunks": (meta.get("chunk_grid") or {}).get("configuration", {}).get("chunk_shape"),
                "shards": (meta.get("chunk_grid") or {}).get("configuration", {}),
                "dtype": meta.get("data_type"),
                "attrs": {k: v for k, v in (meta.get("attributes") or {}).items()
                          if k in ("units", "long_name")}}
        else:
            meta, _ = get_json(f"{base}/{name}/.zarray")
            if not meta:
                continue
            attrs, _ = get_json(f"{base}/{name}/.zattrs")
            info["vars"][name] = {"shape": meta.get("shape"), "chunks": meta.get("chunks"),
                                  "dtype": meta.get("dtype"),
                                  "attrs": {k: v for k, v in (attrs or {}).items()
                                            if k in ("units", "long_name")}}
    if err:
        info["error"] = err
    return info


def show(info: dict) -> None:
    line(f"\n   == {info['store']}  Format {info['format']}, {len(info['members'])} Mitglieder")
    if info["error"]:
        line(f"      Hinweis: {info['error']}")
    attrs = {k: v for k, v in list(info["attrs"].items())[:14]
             if isinstance(v, (str, int, float))}
    if attrs:
        line(f"      Attribute: {json.dumps(attrs, ensure_ascii=False)[:600]}")
    line(f"      Mitglieder: {info['members'][:70]}")
    for name, v in info["vars"].items():
        line(f"      {name:44s} shape={v.get('shape')} chunks={v.get('chunks')} "
             f"dtype={v.get('dtype')} {v.get('attrs') or ''}")


def read_test(bucket: str, store: str, year: str = JAHR, tage_max: int = 400) -> None:
    line("\n" + "=" * 78)
    line(f"Lesetest gs://{bucket}/{store}  Jahr {year}")
    line("=" * 78)
    try:
        import gcsfs
        import xarray as xr
        import zarr
    except Exception as exc:  # noqa: BLE001
        line(f"  !! Bibliotheken fehlen: {exc}")
        return
    fs = gcsfs.GCSFileSystem(token="anon")
    path = f"{bucket}/{store.rstrip('/')}"
    ds = None
    for attempt in ("fsspec-store", "gs-url"):
        try:
            t0 = time.time()
            if attempt == "fsspec-store":
                try:
                    obj = zarr.storage.FsspecStore(fs=fs, path=path, read_only=True)
                except TypeError:
                    obj = zarr.storage.FsspecStore(fs, path)
                ds = xr.open_zarr(obj, chunks=None, consolidated=None)
            else:
                ds = xr.open_zarr(f"gs://{path}", storage_options={"token": "anon"},
                                  chunks=None, consolidated=None)
            line(f"  geöffnet ({attempt}) in {time.time()-t0:.1f}s")
            break
        except Exception as exc:  # noqa: BLE001
            line(f"  !! {attempt}: {type(exc).__name__}: {str(exc)[:250]}")
            ds = None
    if ds is None:
        return

    names = sorted(ds.data_vars)
    line(f"  {len(names)} Variablen: {names[:70]}")
    line(f"  Koordinaten: {sorted(ds.coords)} · Größen: {dict(ds.sizes)}")
    tcoord = next((c for c in ("valid_time", "time") if c in ds.coords), None)
    lats = next((c for c in ("latitude", "lat") if c in ds.coords), None)
    lons = next((c for c in ("longitude", "lon") if c in ds.coords), None)
    if not (tcoord and lats and lons):
        line("  !! keine Zeit-/Geokoordinaten")
        return
    tv = ds[tcoord].values
    line(f"  Zeit: {str(tv[0])[:19]} .. {str(tv[-1])[:19]}")

    pick = [v for v in names if any(k in v.lower() for k in
            ("2m_temperature", "dewpoint", "precipitation", "solar_radiation", "sunshine"))]
    pick = pick[:6] or names[:3]
    line(f"  gelesen: {pick}")

    latvals = ds[lats].values
    lo, hi = (BBOX["lat_min"], BBOX["lat_max"]) if latvals[0] < latvals[-1] else \
             (BBOX["lat_max"], BBOX["lat_min"])
    lonvals = ds[lons].values
    # Gitter kann bei 0..360 liegen: dann zwei Ausschnitte
    if lonvals.min() >= -0.001:
        sel_lon = None
    else:
        sel_lon = slice(BBOX["lon_min"], BBOX["lon_max"])
    try:
        s = {lats: slice(lo, hi), tcoord: slice(f"{year}-01-01", f"{year}-12-31T23")}
        s[lons] = sel_lon if sel_lon is not None else slice(0, BBOX["lon_max"])
        sub = ds[pick].sel(**s)
        line(f"  Ausschnitt: {dict(sub.sizes)}")
        t0 = time.time()
        arr = sub.isel({tcoord: slice(0, tage_max)}).load()
        dt = time.time() - t0
        n = arr.sizes[tcoord]
        line(f"  {n} Schritte in {dt:.1f}s gelesen ({dt/max(n,1)*1000:.0f} ms/Schritt), "
             f"RAM {arr.nbytes/1e6:.1f} MB")
        if lonvals.min() >= -0.001:
            line("  (Längengrad-Achse beginnt bei 0 – der Ausschnitt westlich von Null "
                 "muss separat gelesen werden)")
        pro_jahr = dt / max(n, 1) * 365
        line(f"  -> Hochrechnung: ein Jahr Europa ≈ {pro_jahr:.0f}s, "
             f"zehn Jahre ≈ {pro_jahr*10/60:.0f} min (ein Thread)")
        for v in pick:
            vals = arr[v].values.astype("float64")
            fin = vals[vals == vals]
            if fin.size == 0:
                line(f"     {v}: nur NaN")
                continue
            line(f"     {v}: min {fin.min():.4g} · max {fin.max():.4g} · mittel {fin.mean():.4g} "
                 f"· NaN {(vals != vals).mean()*100:.2f}%")
    except Exception as exc:  # noqa: BLE001
        line(f"  !! Lesetest: {type(exc).__name__}: {str(exc)[:300]}")


def main() -> None:
    line(f"Probe Tageswerte · {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

    for bucket, prefixes in (
        ("weatherbench2", ["datasets/era5_daily/", "datasets/era5-daily-climatology/",
                           "datasets/era5_biweekly/", "datasets/era5_weekly/"]),
        ("gcp-public-data-arco-era5", ["ar/"]),
    ):
        line("\n" + "=" * 78)
        line(f"Auflistung gs://{bucket}")
        line("=" * 78)
        for prefix in prefixes:
            subs, items, err = listing(bucket, prefix)
            line(f"\n-- {bucket}/{prefix}  ({len(subs)} Ordner, {len(items)} Objekte) {err}")
            for s in subs[:40]:
                line(f"   [dir] {s}")
            for it in items[:8]:
                line(f"   [obj] {it['name']}")
            if "daily" not in prefix:
                continue
            for store in subs[:8]:
                info = store_report(bucket, store)
                show(info)
                sz = sizes(bucket, store.rstrip("/") + "/", 200)
                sz = [x for x in sz if x > 0]
                if sz:
                    line(f"      Chunk-Objekte ({len(sz)}): min {min(sz)/1e6:.3f} MB · "
                         f"median {statistics.median(sz)/1e6:.3f} MB · max {max(sz)/1e6:.3f} MB")

    # Objektgröße im großen Stundenspeicher (Preis je Zeitstunde)
    for var in ("2m_temperature", "total_precipitation"):
        sz = sizes("gcp-public-data-arco-era5",
                   f"ar/full_37-1h-0p25deg-chunk-1.zarr-v3/{var}/", 60)
        sz = [x for x in sz if x > 0]
        if sz:
            line(f"\nStundenspeicher-Chunks {var}: n={len(sz)} · min {min(sz)/1e6:.3f} MB · "
                 f"median {statistics.median(sz)/1e6:.3f} MB · max {max(sz)/1e6:.3f} MB")

    # Lesetests: Tages-Speicher zuerst
    candidates = []
    for prefix in ("datasets/era5_daily/", "datasets/era5-daily-climatology/"):
        subs, _, _ = listing("weatherbench2", prefix)
        candidates.extend(subs)
    for store in candidates[:4]:
        try:
            read_test("weatherbench2", store)
        except Exception as exc:  # noqa: BLE001
            line(f"!! {store}: {type(exc).__name__}: {exc}")
    line("\nENDE")


if __name__ == "__main__":
    main()
