// Konvertiert das world-atlas TopoJSON (Natural Earth 1:10m) in GeoJSON-Ländergrenzen.
// Aufruf: node scripts/build_countries.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as topojson from "topojson-client";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "data/raw/world-atlas/countries-10m.json");
const dst = path.join(root, "data/raw/countries-world.geojson");

const topo = JSON.parse(fs.readFileSync(src, "utf8"));
const geo = topojson.feature(topo, topo.objects.countries);
fs.writeFileSync(dst, JSON.stringify(geo));
console.log(`Ländergrenzen geschrieben: ${dst} (${geo.features.length} Features)`);
