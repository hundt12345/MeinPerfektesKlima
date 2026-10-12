#!/usr/bin/env python3
"""Bestandsaufnahme freier ERA5-Datenquellen (läuft auf einem GitHub-Runner).

Warum dieses Skript existiert
-----------------------------
Der Abruf über die Open-Meteo-Archive-API scheitert am Tageskontingent:
frei sind 10.000 „calls"/Tag **je IP**, und GitHub-Runner-IPs sind geteilt.
Ein Aufruf mit mehreren Orten über einen langen Zeitraum zählt mehrfach
(Open-Meteo: >2 Wochen oder >10 Variablen je Ort = mehrere calls). Gemessen
am letzten Lauf (5 von 188 Blöcken, danach dauerhaft HTTP 429) reicht das
freie Kontingent für ~5 Blöcke/Tag – der komplette Rasterabruf bräuchte also
rund einen Monat. Deshalb wird hier nach einer Quelle gesucht, die echte
ERA5-Tageswerte ohne Schlüssel und ohne Tageskontingent liefert.

Geprüft werden (anonym, nur lesend):
  1. Open-Meteo-Archive-API      – Status/Antworttext, Kosten eines Blocks
  2. Google ARCO ERA5            – gs://gcp-public-data-arco-era5 (Zarr)
  3. WeatherBench2               – gs://weatherbench2/datasets/era5*
  4. AWS Open Data               – open-meteo-data, era5-pds

Berichtet werden Variablen, Chunk-Geometrie, Objektgrößen (= Preis eines
Lesevorgangs) und ein echter Lesetest mit Zählung der HTTP-Aufrufe und Bytes.
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

# ERA5-Variablen, die der Datensatz braucht (Kurzname -> Rolle)
WANTED = [
    "2m_temperature", "maximum_2m_temperature", "minimum_2m_temperature",
    "2m_dewpoint_temperature", "total_precipitation", "sunshine_duration",
    "total_sky_direct_solar_radiation_at_surface", "surface_solar_radiation_downwards",
    "surface_solar_radiation_downward", "total_cloud_cover",
]
# Kürzel, unter denen dieselben Variablen in manchen Speichern liegen
ALIASES = ["t2m", "mx2t", "mn2t", "d2m", "tp", "sund", "fdir", "ssrd", "tcc"]


def line(*a):
    print(*a, flush=True)


def get(url: str, timeout: int = 60) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), ""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
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


def size_of(item: dict) -> int:
    try:
        return int(item.get("size") or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------- 1. Open-Meteo
def probe_open_meteo() -> None:
    line("\n" + "=" * 78)
    line("1) Open-Meteo Archive-API (bisherige Quelle) – Kontingent-Test")
    line("=" * 78)
    cases = [
        ("1 Punkt, 2 Tage", {"latitude": "52.5", "longitude": "13.4",
                             "start_date": "2024-01-01", "end_date": "2024-01-02",
                             "daily": "temperature_2m_max", "timezone": "UTC"}),
        ("12 Punkte x 10 Jahre (1 Block des Abrufs)", {
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
        status, body, err = get(url, timeout=240)
        line(f"  {name}: HTTP {status} in {time.time()-t0:.1f}s, {len(body)/1e6:.2f} MB")
        line(f"    Antwort (Anfang): {body[:260].decode('utf-8', 'replace')}")
        if status == 429:
            line("    -> Kontingent erschöpft: der freie Zugang ist für diesen Umfang nicht nutzbar.")
        time.sleep(3)
    line("  Einordnung: frei = 600 calls/min, 5.000/h, 10.000/Tag je IP; ein Block mit")
    line("  12 Orten x 10 Jahren zählt laut Open-Meteo-Preislogik mehrfach (>2 Wochen/Ort).")


# ------------------------------------------------------------------ GCS-Listen
def gcs_list(bucket: str, prefix: str, max_keys: int = 400):
    q = urllib.parse.urlencode({"prefix": prefix, "delimiter": "/", "maxResults": max_keys,
                                "fields": "prefixes,items(name,size)"})
    data, err = get_json(f"{GCS}/storage/v1/b/{bucket}/o?{q}")
    if data is None:
        return [], [], err
    return data.get("prefixes", []) or [], data.get("items", []) or [], ""


def gcs_objects(bucket: str, prefix: str, max_keys: int = 400):
    q = urllib.parse.urlencode({"prefix": prefix, "maxResults": max_keys, "fields": "items(name,size)"})
    data, err = get_json(f"{GCS}/storage/v1/b/{bucket}/o?{q}")
    if data is None:
        return [], err
    return data.get("items", []) or [], ""


def chunk_costs(bucket: str, store: str, label: str = "") -> None:
    """Objektgrößen im Speicher: wie viele Bytes kostet ein Chunk-Lesen?"""
    objs, err = gcs_objects(bucket, store.rstrip("/") + "/", 500)
    sizes = [size_of(o) for o in objs if size_of(o) > 0]
    if not sizes:
        line(f"      Chunk-Größen: keine Objekte sichtbar ({err})")
        return
    line(f"      Chunk-Objekte (Stichprobe {len(sizes)}{label}): "
         f"min {min(sizes)/1e6:.3f} MB · median {statistics.median(sizes)/1e6:.3f} MB · "
         f"max {max(sizes)/1e6:.3f} MB · Summe der Stichprobe {sum(sizes)/1e6:.0f} MB")


def zarr_meta(bucket: str, store: str, only_wanted: bool = True) -> dict:
    out: dict = {"store": store, "format": None, "vars": {}, "attrs": {}, "error": "",
                 "members": []}
    base = f"{GCS}/{bucket}/{store.rstrip('/')}"
    data, _ = get_json(f"{base}/zarr.json")
    if data:
        out["format"] = "v3"
        out["attrs"] = data.get("attributes", {}) or {}
    else:
        zg, _ = get_json(f"{base}/.zgroup")
        za, _ = get_json(f"{base}/.zattrs")
        if zg is None and za is None:
            out["error"] = "weder zarr.json noch .zgroup gefunden"
            return out
        out["format"] = "v2"
        out["attrs"] = za or {}
        cons, _ = get_json(f"{base}/.zmetadata")
        if cons:
            for key, val in (cons.get("metadata") or {}).items():
                parts = key.split("/")
                if len(parts) == 2 and parts[1] == ".zarray":
                    out["vars"][parts[0]] = {
                        "shape": val.get("shape"), "chunks": val.get("chunks"),
                        "dtype": val.get("dtype"),
                        "compressor": (val.get("compressor") or {}).get("id")}
            out["members"] = list(out["vars"])
            return out

    prefixes, items, err = gcs_list(bucket, store.rstrip("/") + "/")
    names = [p.rstrip("/").split("/")[-1] for p in prefixes]
    out["members"] = names
    if err and not names:
        out["error"] = err
    wanted = [n for n in names if n in WANTED or n in ALIASES or
              n in ("time", "valid_time", "latitude", "longitude", "lat", "lon")]
    for name in (wanted if only_wanted else names[:30]):
        if out["format"] == "v3":
            meta, _ = get_json(f"{base}/{name}/zarr.json")
            if not meta:
                continue
            out["vars"][name] = {
                "shape": meta.get("shape"),
                "chunks": (meta.get("chunk_grid") or {}).get("configuration", {}).get("chunk_shape"),
                "dtype": meta.get("data_type"),
                "compressor": [c.get("name") for c in (meta.get("codecs") or [])
                               if isinstance(c, dict)],
                "attrs": {k: v for k, v in (meta.get("attributes") or {}).items()
                          if k in ("units", "long_name")}}
        else:
            meta, _ = get_json(f"{base}/{name}/.zarray")
            if not meta:
                continue
            attrs, _ = get_json(f"{base}/{name}/.zattrs")
            out["vars"][name] = {
                "shape": meta.get("shape"), "chunks": meta.get("chunks"),
                "dtype": meta.get("dtype"),
                "compressor": (meta.get("compressor") or {}).get("id"),
                "attrs": {k: v for k, v in (attrs or {}).items() if k in ("units", "long_name")}}
    return out


def show_store(bucket: str, store: str) -> None:
    try:
        meta = zarr_meta(bucket, store)
    except Exception as exc:  # noqa: BLE001
        line(f"   !! {store}: {type(exc).__name__}: {exc}")
        return
    line(f"\n   == {store}   Format {meta['format']}, {len(meta['members'])} Mitglieder")
    if meta["error"]:
        line(f"      Fehler: {meta['error']}")
    attrs = {k: v for k, v in list(meta["attrs"].items())[:16]
             if isinstance(v, (str, int, float))}
    if attrs:
        line(f"      Attribute: {json.dumps(attrs, ensure_ascii=False)[:700]}")
    missing = [n for n in meta["members"] if n in WANTED or n in ALIASES]
    line(f"      von uns gesucht und vorhanden: {missing}")
    for name, v in meta["vars"].items():
        line(f"      {name:46s} shape={v.get('shape')} chunks={v.get('chunks')} "
             f"dtype={v.get('dtype')} comp={v.get('compressor')} {v.get('attrs') or ''}")
    if not meta["members"]:
        return
    line(f"      alle Mitglieder: {meta['members'][:60]}")
    try:
        chunk_costs(bucket, store)
    except Exception as exc:  # noqa: BLE001
        line(f"      Chunk-Größen: {type(exc).__name__}: {exc}")


def probe_bucket(bucket: str, prefixes: list[str], title: str, deep_patterns=()) -> list[str]:
    line("\n" + "=" * 78)
    line(title)
    line("=" * 78)
    found: list[str] = []
    for prefix in prefixes:
        try:
            subs, items, err = gcs_list(bucket, prefix)
        except Exception as exc:  # noqa: BLE001
            line(f"\n-- {bucket}/{prefix}: {type(exc).__name__}: {exc}")
            continue
        line(f"\n-- {bucket}/{prefix}   ({len(subs)} Ordner, {len(items)} Objekte) {err}")
        found.extend(subs)
        for s in subs[:60]:
            line(f"   [dir] {s}")
        for it in items[:8]:
            line(f"   [obj] {it['name']}  {size_of(it)/1e6:.2f} MB")
        if deep_patterns:
            picks = [s for s in subs if any(p in s.lower() for p in deep_patterns)]
            for store in picks[:8]:
                show_store(bucket, store)
    return found


# ------------------------------------------------------------------ 4. AWS S3
def probe_aws() -> None:
    line("\n" + "=" * 78)
    line("4) AWS Open Data (anonyme S3-Auflistung)")
    line("=" * 78)
    for bucket, prefix in (("open-meteo-data", ""), ("era5-pds", "")):
        q = urllib.parse.urlencode({"list-type": "2", "delimiter": "/", "max-keys": "60",
                                    "prefix": prefix})
        status, body, err = get(f"https://{bucket}.s3.amazonaws.com/?{q}", timeout=60)
        line(f"\n-- {bucket}: HTTP {status} {err}")
        if status != 200:
            line(f"   {body[:300].decode('utf-8', 'replace')}")
            continue
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(body)
            ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
            for p in root.findall(".//s3:CommonPrefixes/s3:Prefix", ns)[:60]:
                line(f"   [dir] {p.text}")
            for k in root.findall(".//s3:Contents", ns)[:10]:
                name = k.findtext("s3:Key", default="", namespaces=ns)
                size = k.findtext("s3:Size", default="0", namespaces=ns)
                line(f"   [obj] {name}  {int(size)/1e6:.2f} MB")
        except Exception as exc:  # noqa: BLE001
            line(f"   XML-Fehler: {exc}; roh: {body[:400].decode('utf-8', 'replace')}")
        # Unterordner von open-meteo-data genauer ansehen
        if bucket == "open-meteo-data":
            for sub in ("era5/", "era5_land/", "cmip6/"):
                q2 = urllib.parse.urlencode({"list-type": "2", "delimiter": "/",
                                             "max-keys": "40", "prefix": sub})
                st2, body2, err2 = get(f"https://{bucket}.s3.amazonaws.com/?{q2}", timeout=60)
                line(f"   -- {bucket}/{sub}: HTTP {st2} {err2}")
                if st2 == 200:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(body2)
                        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
                        for p in root.findall(".//s3:CommonPrefixes/s3:Prefix", ns)[:20]:
                            line(f"      [dir] {p.text}")
                        for k in root.findall(".//s3:Contents", ns)[:8]:
                            name = k.findtext("s3:Key", default="", namespaces=ns)
                            size = k.findtext("s3:Size", default="0", namespaces=ns)
                            line(f"      [obj] {name}  {int(size)/1e6:.2f} MB")
                    except Exception as exc:  # noqa: BLE001
                        line(f"      XML-Fehler: {exc}")


# ------------------------------------------------------------- 5. Lesetest Zarr
def read_test(bucket: str, store: str, sample_days: int = 3, month: str = "2024-07") -> None:
    """Misst am echten Speicher: erst eine 3-Tage-Stichprobe (Kosten!), dann –
    nur wenn die Hochrechnung günstig ist – ein ganzer Monat."""
    line("\n" + "=" * 78)
    line(f"5) Lesetest gs://{bucket}/{store}")
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
    for fname in ("cat_ranges", "cat_file", "cat"):
        orig = getattr(fs, fname, None)
        if orig is None:
            continue

        def make(orig=orig, fname=fname):
            def patched(*a, **kw):
                res = orig(*a, **kw)
                try:
                    if fname == "cat_ranges":
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
            return patched

        setattr(fs, fname, make())

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

    names = sorted(ds.data_vars)
    line(f"  {len(names)} Variablen: {names[:60]}")
    line(f"  Koordinaten: {sorted(ds.coords)}")
    hits = [v for v in WANTED + ALIASES if v in ds.data_vars]
    line(f"  gesuchte Variablen vorhanden: {hits}")
    tcoord = next((c for c in ("valid_time", "time") if c in ds.coords), None)
    lats = next((c for c in ("latitude", "lat") if c in ds.coords), None)
    lons = next((c for c in ("longitude", "lon") if c in ds.coords), None)
    if not (tcoord and lats and lons):
        line("  !! keine Zeit-/Geokoordinaten – Ausschnitt nicht möglich")
        return
    line(f"  Zeit: {str(ds[tcoord].values[0])[:19]} .. {str(ds[tcoord].values[-1])[:19]} "
         f"({ds.sizes[tcoord]} Schritte)")

    pick = [v for v in ("maximum_2m_temperature", "minimum_2m_temperature",
                        "2m_dewpoint_temperature", "total_precipitation",
                        "2m_temperature", "sunshine_duration",
                        "total_sky_direct_solar_radiation_at_surface",
                        "surface_solar_radiation_downwards") if v in ds.data_vars]
    if not pick:
        line("  !! keine der benötigten Variablen vorhanden")
        return
    line(f"  Lesetest mit: {pick}")

    lat_lo, lat_hi = BBOX["lat_min"], BBOX["lat_max"]
    latvals = ds[lats].values
    if len(latvals) > 1 and latvals[0] > latvals[-1]:
        lat_lo, lat_hi = lat_hi, lat_lo
    bbox_sel = {lats: slice(lat_lo, lat_hi), lons: slice(BBOX["lon_min"], BBOX["lon_max"])}

    def messen(label: str, tsel, projektion_tage: int) -> float | None:
        try:
            sub = ds[pick].sel({**bbox_sel, tcoord: tsel})
        except Exception as exc:  # noqa: BLE001
            line(f"    !! Auswahl {label}: {type(exc).__name__}: {str(exc)[:200]}")
            return None
        before = dict(stats)
        t0 = time.time()
        try:
            arr = sub.load()
        except Exception as exc:  # noqa: BLE001
            line(f"    !! Lesen {label}: {type(exc).__name__}: {str(exc)[:300]}")
            return None
        dt = time.time() - t0
        mb = (stats["bytes"] - before["bytes"]) / 1e6
        calls = stats["calls"] - before["calls"]
        line(f"    {label}: {dict(sub.sizes)} -> {dt:.1f}s, {calls} HTTP-Aufrufe, {mb:.1f} MB, "
             f"RAM {arr.nbytes/1e6:.1f} MB")
        for v in pick:
            vals = arr[v].values.astype("float64")
            fin = vals[vals == vals]
            if fin.size == 0:
                line(f"      {v}: nur NaN")
                continue
            line(f"      {v}: min {fin.min():.4g} · max {fin.max():.4g} · "
                 f"mittel {fin.mean():.4g} · NaN {(vals != vals).mean()*100:.2f}%")
        if dt <= 0 or mb <= 0:
            return dt
        hoch_gb = mb / 1e3 * (projektion_tage / max(1, sample_days))
        hoch_min = dt / 60 * (projektion_tage / max(1, sample_days))
        line(f"    -> Hochrechnung 10 Jahre ({projektion_tage} Tage, Europa-Ausschnitt): "
             f"~{hoch_gb:.1f} GB, ~{hoch_min:.0f} min Lesezeit (ein Thread)")
        return dt

    t_probe = slice(f"{month}-01T00", f"{month}-0{sample_days}T23")
    dt = messen(f"Stichprobe {sample_days} Tage ({month})", t_probe, 3653)
    if dt is not None and dt < 120:
        messen(f"ganzer Monat ({month})", slice(f"{month}-01T00", f"{month}-31T23"), 3653)
    line(f"  Gesamtlauf: {stats['calls']} HTTP-Aufrufe, {stats['bytes']/1e6:.1f} MB")


def main() -> None:
    line("Datenquellen-Probe für 'Mein perfektes Klima'")
    line(f"BBOX: {BBOX}  |  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    probe_open_meteo()

    wb = probe_bucket("weatherbench2", ["datasets/", "datasets/era5/", "datasets/era5_derived/"],
                      "2) WeatherBench2 (gs://weatherbench2) – sucht Tagesstatistiken",
                      deep_patterns=("daily", "0p25", "1440x721"))
    arco = probe_bucket("gcp-public-data-arco-era5", ["ar/", "co/", "raw/"],
                        "3) Google ARCO ERA5 (gs://gcp-public-data-arco-era5)",
                        deep_patterns=("full_37-1h-0p25deg-chunk-1.zarr-v3",
                                       "full_37-6h-0p25deg_derived",
                                       "1h-360x181", "single-level-surface"))
    probe_aws()

    # Lesetests: zuerst alles mit 'daily' (Tageswerte = 24x weniger Daten),
    # danach der große 0,25°-Stundenspeicher als Referenz.
    candidates = [s for s in wb + arco if "daily" in s.lower()][:2]
    candidates += [s for s in arco if "full_37-1h-0p25deg-chunk-1.zarr-v3" in s][:1]
    candidates += [s for s in arco if "derived" in s.lower()][:1]
    candidates += [s for s in wb if "6h-1440x721" in s][:1]
    seen, tests = set(), []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        tests.append(c)
    line(f"\nLesetests für: {tests}")
    for store in tests[:4]:
        try:
            read_test("weatherbench2" if store.startswith("datasets/") else
                      "gcp-public-data-arco-era5", store)
        except Exception as exc:  # noqa: BLE001
            line(f"!! Lesetest {store}: {type(exc).__name__}: {exc}")
    line("\nENDE")


if __name__ == "__main__":
    main()
