#!/usr/bin/env python3
"""Bestandsaufnahme der freien ERA5-Datenquellen (läuft auf einem GitHub-Runner).

Hintergrund: Der Abruf über die Open-Meteo-Archive-API scheitert am Kontingent
(10.000 „calls"/Tag je IP – GitHub-Runner-IPs sind geteilt). Gesucht ist eine
Quelle, die echte ERA5-Tageswerte für ~2.250 europäische Landpunkte über zehn
Jahre liefert, ohne Schlüssel und ohne Tageskontingent.

Geprüft werden:
  1. Open-Meteo        – Status + Antworttext (warum 429?)
  2. Google ARCO ERA5  – gs://gcp-public-data-arco-era5 (anonym, Zarr)
  3. WeatherBench2     – gs://weatherbench2/datasets/era5*
  4. AWS Open Data     – era5-pds (anonyme S3-Auflistung)

Für die Zarr-Speicher werden Variablen, Chunk-Geometrie und Objektgrößen
ausgelesen und ein Lese-Test (Europa-Ausschnitt, ein Monat) gemessen.
Alles ohne Abhängigkeiten außer numpy/xarray/zarr/gcsfs; jeder Abschnitt ist
in try/except gekapselt, damit ein Fehler die übrigen Ergebnisse nicht frisst.
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

# Gesuchte ERA5-Variablen (Kurzname -> was wir damit bauen)
WANTED = {
    "2m_temperature": "tmean",
    "maximum_2m_temperature": "tmax",
    "minimum_2m_temperature": "tmin",
    "2m_dewpoint_temperature": "tdew",
    "total_precipitation": "prec",
    "sunshine_duration": "sun (direkt!)",
    "total_sky_direct_solar_radiation_at_surface": "sun (ableitbar)",
    "surface_solar_radiation_downwards": "sun (grob ableitbar)",
    "10m_u_component_of_wind": "-",
}

BBOX = {"lat_min": 34.0, "lat_max": 72.0, "lon_min": -25.0, "lon_max": 45.0}


def line(*a):
    print(*a, flush=True)


def get(url: str, timeout: int = 60) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), ""
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            pass
        return exc.code, body, str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, b"", f"{type(exc).__name__}: {exc}"


def get_json(url: str, timeout: int = 60):
    status, body, err = get(url, timeout)
    if status != 200:
        return None, f"HTTP {status} {err}"
    try:
        return json.loads(body.decode("utf-8")), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"JSON-Fehler: {exc}"


# --------------------------------------------------------------- 1. Open-Meteo
def probe_open_meteo() -> None:
    line("\n" + "=" * 78)
    line("1) Open-Meteo Archive-API (bisherige Quelle)")
    line("=" * 78)
    cases = [
        ("klein", {"latitude": "52.5", "longitude": "13.4", "start_date": "2024-01-01",
                   "end_date": "2024-01-02", "daily": "temperature_2m_max", "timezone": "UTC"}),
        ("12 Punkte x 10 Jahre (wie im Abruf)", {
            "latitude": ",".join(f"{34 + i * 0.75:.2f}" for i in range(12)),
            "longitude": ",".join("10.00" for _ in range(12)),
            "start_date": "2016-01-01", "end_date": "2025-12-31",
            "daily": "temperature_2m_max,temperature_2m_min,dew_point_2m_mean,"
                     "sunshine_duration,precipitation_sum",
            "timezone": "UTC", "models": "era5"}),
    ]
    for name, params in cases:
        url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
        t0 = time.time()
        status, body, err = get(url, timeout=180)
        dt = time.time() - t0
        text = body[:400].decode("utf-8", "replace").replace("\n", " ")
        line(f"  {name}: HTTP {status} in {dt:.1f}s, {len(body)/1e6:.2f} MB")
        line(f"    Antwort: {text}")
        if status != 429:
            time.sleep(2)


# ------------------------------------------------------------------ 2./3. GCS
def gcs_list(bucket: str, prefix: str, max_keys: int = 400):
    """Ordnerartige Auflistung eines öffentlichen Buckets (anonym)."""
    q = urllib.parse.urlencode({"prefix": prefix, "delimiter": "/", "maxResults": max_keys,
                                "fields": "prefixes,items(name,size)"})
    data, err = get_json(f"{GCS}/storage/v1/b/{bucket}/o?{q}")
    if data is None:
        return [], [], err
    return data.get("prefixes", []) or [], data.get("items", []) or [], ""


def gcs_list_objects(bucket: str, prefix: str, max_keys: int = 500):
    q = urllib.parse.urlencode({"prefix": prefix, "maxResults": max_keys,
                                "fields": "items(name,size)"})
    data, err = get_json(f"{GCS}/storage/v1/b/{bucket}/o?{q}")
    if data is None:
        return [], err
    return data.get("items", []) or [], ""


def zarr_meta(bucket: str, store: str) -> dict:
    """Variablen + Chunk-Geometrie eines Zarr-Speichers (v2 und v3)."""
    out: dict = {"store": store, "format": None, "vars": {}, "attrs": {}, "error": ""}
    base = f"{GCS}/{bucket}/{store.rstrip('/')}"

    data, _ = get_json(f"{base}/zarr.json")
    if data:
        out["format"] = "v3"
        out["attrs"] = data.get("attributes", {}) or {}
    else:
        data2, _ = get_json(f"{base}/.zgroup")
        data3, _ = get_json(f"{base}/.zattrs")
        if data2 is not None or data3 is not None:
            out["format"] = "v2"
            out["attrs"] = data3 or {}
            cons, _ = get_json(f"{base}/.zmetadata")
            if cons:
                meta = cons.get("metadata", {})
                for key, val in meta.items():
                    parts = key.split("/")
                    if len(parts) == 2 and parts[1] == ".zarray":
                        out["vars"][parts[0]] = {
                            "shape": val.get("shape"), "chunks": val.get("chunks"),
                            "dtype": val.get("dtype"),
                            "compressor": (val.get("compressor") or {}).get("id"),
                        }
                out["consolidated"] = True
                return out
        else:
            out["error"] = "kein zarr.json und kein .zgroup gefunden"
            return out

    # Variablen aus der Auflistung bestimmen
    prefixes, items, err = gcs_list(bucket, store.rstrip("/") + "/")
    names = [p.rstrip("/").split("/")[-1] for p in prefixes]
    if err and not names:
        out["error"] = err
    interesting = [n for n in names if n in WANTED or n in ("time", "latitude", "longitude",
                                                            "lat", "lon", "valid_time")]
    for name in (interesting or names[:25]):
        if out["format"] == "v3":
            meta, e = get_json(f"{base}/{name}/zarr.json")
            if not meta:
                continue
            arr = meta.get("attributes", {})
            shape = meta.get("shape")
            chunks = meta.get("chunk_grid", {}).get("configuration", {}).get("chunk_shape")
            comp = meta.get("codecs") or []
            dtype = meta.get("data_type")
            out["vars"][name] = {"shape": shape, "chunks": chunks, "dtype": dtype,
                                 "compressor": [c.get("name") for c in comp if isinstance(c, dict)],
                                 "attrs": {k: arr.get(k) for k in ("units", "long_name")
                                           if k in arr}}
        else:
            meta, e = get_json(f"{base}/{name}/.zarray")
            if not meta:
                continue
            attrs, _ = get_json(f"{base}/{name}/.zattrs")
            out["vars"][name] = {"shape": meta.get("shape"), "chunks": meta.get("chunks"),
                                 "dtype": meta.get("dtype"),
                                 "compressor": (meta.get("compressor") or {}).get("id"),
                                 "attrs": {k: (attrs or {}).get(k)
                                           for k in ("units", "long_name") if attrs and k in attrs}}
    out["n_members"] = len(names)
    return out


def probe_gcs(bucket: str, prefixes: list[str], title: str, deep: int = 6) -> None:
    line("\n" + "=" * 78)
    line(title)
    line("=" * 78)
    for prefix in prefixes:
        subs, items, err = gcs_list(bucket, prefix)
        line(f"\n-- {bucket}/{prefix}  ({len(subs)} Ordner, {len(items)} Objekte) {err}")
        for s in subs[:40]:
            line(f"   [dir] {s}")
        for it in items[:10]:
            line(f"   [obj] {it['name']}  {it.get('size', 0)/1e6:.2f} MB")
        # Detailblick auf die interessantesten Speicher
        stores = [s for s in subs if "zarr" in s.lower()][:deep]
        for store in stores:
            try:
                meta = zarr_meta(bucket, store)
            except Exception as exc:  # noqa: BLE001
                line(f"   !! {store}: {type(exc).__name__}: {exc}")
                continue
            line(f"\n   == {store}  (Format {meta['format']}, {meta.get('n_members', '?')} Mitglieder)")
            if meta["error"]:
                line(f"      Fehler: {meta['error']}")
            attrs = meta["attrs"]
            if attrs:
                keep = {k: v for k, v in list(attrs.items())[:14] if isinstance(v, (str, int, float))}
                line(f"      Attribute: {json.dumps(keep, ensure_ascii=False)[:600]}")
            for name, v in meta["vars"].items():
                flag = "  <-- gebraucht" if name in WANTED else ""
                line(f"      {name:52s} shape={v.get('shape')} chunks={v.get('chunks')} "
                     f"dtype={v.get('dtype')} comp={v.get('compressor')} {v.get('attrs') or ''}{flag}")
            # Objektgrößen: wie teuer ist ein Chunk?
            objs, err2 = gcs_list_objects(bucket, store.rstrip("/") + "/", 300)
            sizes = [o.get("size", 0) for o in objs]
            if sizes:
                line(f"      Chunk-Objekte (Stichprobe {len(sizes)}): "
                     f"min {min(sizes)/1e6:.3f} MB, median {statistics.median(sizes)/1e6:.3f} MB, "
                     f"max {max(sizes)/1e6:.3f} MB {err2}")


# ------------------------------------------------------------------ 4. AWS S3
def probe_aws() -> None:
    line("\n" + "=" * 78)
    line("4) AWS Open Data – Bucket 'era5-pds'")
    line("=" * 78)
    for bucket in ("era5-pds",):
        q = urllib.parse.urlencode({"list-type": "2", "delimiter": "/", "max-keys": "50"})
        status, body, err = get(f"https://{bucket}.s3.amazonaws.com/?{q}")
        line(f"  {bucket}: HTTP {status} {err} – {body[:600].decode('utf-8', 'replace')}")


# ------------------------------------------------------------- 5. Lesetest Zarr
def read_test(bucket: str, store: str, variables: list[str], month: str = "2024-07") -> None:
    line("\n" + "=" * 78)
    line(f"5) Lesetest: gs://{bucket}/{store}  ({month}, Europa-Ausschnitt)")
    line("=" * 78)
    try:
        import gcsfs
        import xarray as xr
        import zarr
    except Exception as exc:  # noqa: BLE001
        line(f"  !! Bibliotheken fehlen: {exc}")
        return

    stats = {"calls": 0, "bytes": 0}
    fs = gcsfs.GCSFileSystem(token="anon")

    def wrap(name):
        orig = getattr(fs, name, None)
        if orig is None:
            return

        def patched(*a, **kw):
            res = orig(*a, **kw)
            try:
                if name == "cat_ranges":
                    stats["calls"] += len(a[0]) if a else 0
                    stats["bytes"] += sum(len(r or b"") for r in res)
                elif isinstance(res, bytes):
                    stats["calls"] += 1
                    stats["bytes"] += len(res)
                elif isinstance(res, dict):
                    stats["calls"] += len(res)
                    stats["bytes"] += sum(len(v or b"") for v in res.values())
            except Exception:  # noqa: BLE001
                pass
            return res

        setattr(fs, name, patched)

    for fname in ("cat_ranges", "cat_file", "cat"):
        wrap(fname)

    path = f"{bucket}/{store.rstrip('/')}"
    ds = None
    for attempt in ("fsspec-store", "gs-url"):
        try:
            t0 = time.time()
            if attempt == "fsspec-store":
                try:
                    storeobj = zarr.storage.FsspecStore(fs=fs, path=path, read_only=True)
                except TypeError:
                    storeobj = zarr.storage.FsspecStore(fs, path)
                ds = xr.open_zarr(storeobj, chunks=None, consolidated=None)
            else:
                ds = xr.open_zarr(f"gs://{path}", storage_options={"token": "anon"},
                                  chunks=None, consolidated=None)
            line(f"  geöffnet über {attempt} in {time.time()-t0:.1f}s")
            break
        except Exception as exc:  # noqa: BLE001
            line(f"  !! {attempt}: {type(exc).__name__}: {str(exc)[:300]}")
            ds = None
    if ds is None:
        return

    line(f"  Variablen im Speicher: {sorted(ds.data_vars)[:40]}")
    line(f"  Koordinaten: {sorted(ds.coords)}")
    tcoord = next((c for c in ("valid_time", "time") if c in ds.coords), None)
    lats = next((c for c in ("latitude", "lat") if c in ds.coords), None)
    lons = next((c for c in ("longitude", "lon") if c in ds.coords), None)
    if not (tcoord and lats and lons):
        line("  !! Zeit-/Geokoordinaten nicht gefunden – kein Ausschnitt möglich.")
        return
    line(f"  Zeitachse: {str(ds[tcoord].values[0])[:19]} .. {str(ds[tcoord].values[-1])[:19]} "
         f"({ds.sizes[tcoord]} Schritte)")

    have = [v for v in variables if v in ds.data_vars]
    missing = [v for v in variables if v not in ds.data_vars]
    line(f"  gebraucht und vorhanden: {have}")
    line(f"  fehlt: {missing}")
    if not have:
        return

    sub = ds[have].sel({lats: slice(BBOX["lat_min"], BBOX["lat_max"]),
                        lons: slice(BBOX["lon_min"], BBOX["lon_max"])})
    if sub.sizes.get(lats, 0) == 0:  # falls die Breitenachse absteigend ist
        sub = ds[have].sel({lats: slice(BBOX["lat_max"], BBOX["lat_min"]),
                            lons: slice(BBOX["lon_min"], BBOX["lon_max"])})
    line(f"  Ausschnitt: {dict(sub.sizes)}")
    mon = sub.sel({tcoord: slice(f"{month}-01", f"{month}-31")})
    line(f"  Monat {month}: {dict(mon.sizes)}")
    before = dict(stats)
    t0 = time.time()
    try:
        arr = mon.load()
    except Exception as exc:  # noqa: BLE001
        line(f"  !! Lesen fehlgeschlagen: {type(exc).__name__}: {str(exc)[:400]}")
        return
    dt = time.time() - t0
    line(f"  geladen in {dt:.1f}s | HTTP-Aufrufe: {stats['calls']-before['calls']} | "
         f"Daten: {(stats['bytes']-before['bytes'])/1e6:.1f} MB | "
         f"RAM {arr.nbytes/1e6:.1f} MB")
    for v in have:
        try:
            vals = arr[v].values
            finite = vals[vals == vals]
            line(f"    {v}: min {finite.min():.4g} max {finite.max():.4g} mittel {finite.mean():.4g} "
                 f"NaN-Anteil {(vals != vals).mean()*100:.2f}%")
        except Exception as exc:  # noqa: BLE001
            line(f"    {v}: Statistik fehlgeschlagen ({exc})")
    line(f"  Gesamtstatistik des Laufs: {stats['calls']} HTTP-Aufrufe, {stats['bytes']/1e6:.1f} MB")


def main() -> None:
    line("Datenquellen-Probe für 'Mein perfektes Klima'")
    line(f"BBOX: {BBOX}")
    line(f"Zeit: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    probe_open_meteo()
    probe_gcs("gcp-public-data-arco-era5", ["", "ar/", "co/", "analysis/", "model/"],
              "2) Google Cloud ARCO ERA5 (gs://gcp-public-data-arco-era5)")
    probe_gcs("weatherbench2", ["datasets/", "datasets/era5/", "datasets/era5_derived/"],
              "3) WeatherBench2 (gs://weatherbench2)")
    probe_aws()

    # Lesetests: erst der ARCO-Hauptspeicher, dann WeatherBench2-Alternative.
    wanted_vars = list(WANTED)
    read_test("gcp-public-data-arco-era5", "ar/full_37-1h-0p25deg-chunk-1.zarr-v3", wanted_vars)
    read_test("weatherbench2", "datasets/era5/1959-2022_01_10-6h-1440x721_with_statics.zarr",
              wanted_vars)
    line("\nENDE")


if __name__ == "__main__":
    main()
