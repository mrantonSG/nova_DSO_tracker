"""
Builds static/js/nova_moon_features.js: the curated lunar features the Solar System
page's Moon card names (terminator, libration hint, Calendar).

Source: Gazetteer of Planetary Nomenclature, IAU WGPSN / USGS Astrogeology, public
domain. The Moon's centre points as a shapefile:
    https://planetarynames.wr.usgs.gov/shapefiles/MOON_nomenclature_center_pts.zip

Usage (from the Nova folder):
    python3 tools/gen_moon_features.py [path/to/MOON_nomenclature_center_pts.zip]
Without a path the zip is downloaded. Only the standard library is used: the .dbf
inside the zip is read directly.

The selection is the list below, by exact IAU name (diacritics included); every name
must be in the gazetteer and every feature must be adopted by the IAU, or the script
stops. From the gazetteer it takes the centre (the shapefile's longitude is 0-360
east-positive; written -180..180 east-positive), the diameter in km and the type.

  tier  1: the well-known imaging targets, ranked first; 2: the rest.
  limb  true for the features a libration hint may name: those near the mean limb, plus
        Mare Crisium, the classic libration-sensitive mare.
  type  crater, mare, oceanus, sinus, lacus, mons, montes, vallis, rupes, rima,
        promontorium. Feature names are IAU Latin and never translated; the page
        translates the type.
"""
import datetime
import io
import json
import os
import struct
import sys
import urllib.request
import zipfile

URL = 'https://planetarynames.wr.usgs.gov/shapefiles/MOON_nomenclature_center_pts.zip'
DBF = 'MOON_nomenclature_center_pts.dbf'
OUT = os.path.join('static', 'js', 'nova_moon_features.js')
CREDIT = 'Gazetteer of Planetary Nomenclature, IAU WGPSN / USGS Astrogeology'

# name: (tier, limb)
SELECTION = {
    # maria, bays, lakes
    'Mare Imbrium': (1, False), 'Mare Serenitatis': (1, False), 'Mare Tranquillitatis': (1, False),
    'Mare Crisium': (1, True), 'Mare Fecunditatis': (2, False), 'Mare Nectaris': (2, False),
    'Mare Nubium': (2, False), 'Mare Humorum': (2, False), 'Mare Frigoris': (2, False),
    'Mare Vaporum': (2, False), 'Oceanus Procellarum': (2, False), 'Sinus Iridum': (1, False),
    'Sinus Medii': (2, False), 'Sinus Roris': (2, False), 'Lacus Mortis': (2, False),
    # near the limb
    'Mare Orientale': (1, True), 'Mare Humboldtianum': (1, True), 'Mare Smythii': (1, True),
    'Mare Marginis': (1, True), 'Mare Australe': (2, True), 'Mare Undarum': (2, True),
    'Bailly': (2, True), 'Grimaldi': (2, True), 'Riccioli': (2, True), 'Hevelius': (2, True),
    'Schickard': (2, True), 'Wargentin': (2, True), 'Pythagoras': (2, True), 'Gauss': (2, True),
    'Endymion': (2, True), 'Einstein': (2, True),
    # mountains, valleys, scarps, rilles, capes
    'Montes Apenninus': (1, False), 'Montes Alpes': (1, False), 'Montes Caucasus': (2, False),
    'Montes Carpatus': (2, False), 'Montes Jura': (2, False), 'Mons Piton': (1, False),
    'Mons Pico': (1, False), 'Montes Teneriffe': (2, False), 'Montes Recti': (2, False),
    'Rupes Recta': (1, False), 'Rupes Altai': (2, False), 'Vallis Alpes': (1, False),
    'Vallis Schröteri': (1, False), 'Vallis Rheita': (2, False), 'Rima Hyginus': (2, False),
    'Rima Ariadaeus': (2, False), 'Promontorium Heraclides': (2, False),
    'Promontorium Laplace': (2, False),
    # craters
    'Copernicus': (1, False), 'Tycho': (1, False), 'Clavius': (1, False), 'Plato': (1, False),
    'Archimedes': (1, False), 'Aristarchus': (1, False), 'Gassendi': (1, False),
    'Ptolemaeus': (1, False), 'Alphonsus': (1, False), 'Arzachel': (1, False),
    'Theophilus': (1, False), 'Cyrillus': (1, False), 'Catharina': (1, False),
    'Posidonius': (1, False), 'Petavius': (1, False), 'Langrenus': (1, False),
    'Werner': (1, False), 'Eratosthenes': (1, False), 'Kepler': (1, False),
    'Blanchinus': (2, False), 'La Caille': (2, False), 'Purbach': (2, False), 'Ukert': (2, False),
    'Maginus': (2, False), 'Hipparchus': (2, False), 'Albategnius': (2, False),
    'Pitatus': (2, False), 'Cassini': (2, False), 'Aristoteles': (2, False), 'Eudoxus': (2, False),
    'Messier': (2, False),
}

TYPES = {'AA': 'crater', 'ME': 'mare', 'OC': 'oceanus', 'SI': 'sinus', 'LC': 'lacus',
         'VA': 'vallis', 'RU': 'rupes', 'RI': 'rima', 'PR': 'promontorium'}


def read_dbf(data):
    """The records of a dBASE III table as dicts of stripped strings."""
    count, header_len, record_len = struct.unpack('<IHH', data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        d = data[pos:pos + 32]
        fields.append((d[:11].split(b'\0')[0].decode('ascii'), d[16]))
        pos += 32
    out = []
    for i in range(count):
        rec = data[header_len + i * record_len: header_len + (i + 1) * record_len]
        at, row = 1, {}
        for name, length in fields:
            row[name] = rec[at:at + length].decode('utf-8').strip()
            at += length
        out.append(row)
    return out


def feature_type(row):
    if row['code'] == 'MO':
        return 'montes' if row['name'].startswith('Montes ') else 'mons'
    t = TYPES.get(row['code'])
    if t is None:
        sys.exit(f"{row['name']}: no type for gazetteer code {row['code']!r}")
    return t


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'rb') as fh:
            raw = fh.read()
    else:
        with urllib.request.urlopen(URL, timeout=120) as resp:
            raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        info = z.getinfo(DBF)
        records = read_dbf(z.read(DBF))
    source_date = datetime.date(*info.date_time[:3]).isoformat()
    by_name = {r['name']: r for r in records}

    features = []
    for name, (tier, limb) in SELECTION.items():
        row = by_name.get(name)
        if row is None:
            sys.exit(f'{name}: not in the gazetteer')
        if row['approval'] != 'Adopted by IAU':
            sys.exit(f"{name}: not adopted ({row['approval']})")
        lon = (float(row['center_lon']) + 180.0) % 360.0 - 180.0
        features.append({
            'name': name, 'type': feature_type(row),
            'lat': round(float(row['center_lat']), 2), 'lon': round(lon, 2),
            'diameterKm': round(float(row['diameter']), 1), 'tier': tier, 'limb': limb,
        })

    body = ',\n'.join('        ' + json.dumps(f, ensure_ascii=False) for f in features)
    js = f"""/*
 * nova_moon_features.js — the lunar features the Solar System page's Moon card names.
 * Generated by tools/gen_moon_features.py; do not edit by hand.
 *
 * Source: {CREDIT}
 * (public domain), centre points as of {source_date}.
 * {len(features)} features, curated by name. Latitude and longitude in degrees, longitude
 * east-positive -180..180 (IAU: Mare Crisium at +59); diameter in km. tier 1: well-known
 * imaging targets; limb: may be named by the libration hint. Names are IAU Latin and are
 * not translated; the page translates the type.
 */
(function (root, factory) {{
    if (typeof module === 'object' && module.exports) {{
        module.exports = factory();
    }} else {{
        root.NovaMoonFeatures = factory();
    }}
}})(typeof self !== 'undefined' ? self : this, function () {{
    'use strict';

    const SOURCE = Object.freeze({{ credit: {json.dumps(CREDIT)}, date: {json.dumps(source_date)} }});

    const FEATURES = Object.freeze([
{body},
    ].map(f => Object.freeze(f)));

    return {{ SOURCE: SOURCE, FEATURES: FEATURES }};
}});
"""
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(js)
    print(f'{len(features)} features ({source_date}) -> {OUT}')


if __name__ == '__main__':
    main()
