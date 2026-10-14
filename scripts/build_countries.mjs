// Konvertiert das world-atlas TopoJSON (Natural Earth 1:10m) in GeoJSON.
//
//   data/raw/countries-world.geojson – Länderflächen (Zuordnung Stadt → Land)
//   data/raw/borders-world.geojson   – nur die Landgrenzen zwischen zwei Ländern,
//                                      jede Grenze genau einmal (topojson.mesh)
//
// Die Karte zeichnet nur noch die Landgrenzen als Linien: Küsten zeigt schon die
// Grundkarte, und Polygon-Umrisse würden am Kartenausschnitt künstliche gerade
// Kanten erzeugen.
// Aufruf: node scripts/build_countries.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as topojson from "topojson-client";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "data/raw/world-atlas/countries-10m.json");
const dst = path.join(root, "data/raw/countries-world.geojson");
const bordersDst = path.join(root, "data/raw/borders-world.geojson");

const topo = JSON.parse(fs.readFileSync(src, "utf8"));
const geo = topojson.feature(topo, topo.objects.countries);
fs.writeFileSync(dst, JSON.stringify(geo));
console.log(`Ländergrenzen geschrieben: ${dst} (${geo.features.length} Features)`);

const borders = topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b);
fs.writeFileSync(bordersDst, JSON.stringify({ type: "Feature", properties: {}, geometry: borders }));
console.log(`Landgrenzen geschrieben: ${bordersDst} (${borders.coordinates.length} Linien)`);
