"""
Builds static/js/nova_mars_features.js: the curated Mars features the Solar System
page's Mars card names (onto the disc, and the next time each is on the central
meridian).

Source: Gazetteer of Planetary Nomenclature, IAU WGPSN / USGS Astrogeology, public
domain. Mars's centre points as a shapefile:
    https://planetarynames.wr.usgs.gov/shapefiles/MARS_nomenclature_center_pts.zip
The same source, and the same dBASE file read with the standard library, as
tools/gen_moon_features.py.

Usage (from the Nova folder):
    python3 tools/gen_mars_features.py [path/to/MARS_nomenclature_center_pts.zip]
Without a path the zip is downloaded (which is why it takes a path: a Python
installation without a CA bundle, as some macOS builds are, cannot verify the
download and needs the zip fetched some other way first).

The selection is the list below, by exact IAU name; every name must be in the
gazetteer and every feature must be adopted by the IAU, or the script stops. From the
gazetteer it takes the centre, the diameter in km and the type.

  tier  1: the features an imager identifies first; 2: the rest.
  type  albedo (the classical dark and bright markings, gazetteer "Albedo Feature" and
        the only type whose gazetteer diameter is 0), mons, planum, planitia, vallis,
        chasma, labyrinthus, chaos, patera, tholus, terra, vastitas, fossa, mensa,
        rupes, crater. Feature names are IAU names and never translated; the page
        translates the type.

Coordinates: the gazetteer's Mars latitudes are planetocentric and its longitudes are
east-positive 0-360 (its own metadata says the latitudes are planetocentric; the
direction is from the data - Gale at +137.8 and Jezero at +77.7 are the published
+137.4 E and +77.6 E of the two rovers' landing sites, and Olympus Mons's +18.65 is the
planetocentric latitude, its planetographic one being +18.9). Written here -180..180
east-positive like the Moon's, so the two feature files read the same way; the card's own
longitude convention is the other one (west-positive planetographic, what JPL Horizons
prints as ObsSub-LON), and it is the card that converts, not this.
"""
import datetime
import io
import json
import os
import struct
import sys
import urllib.request
import zipfile

URL = 'https://planetarynames.wr.usgs.gov/shapefiles/MARS_nomenclature_center_pts.zip'
DBF = 'MARS_nomenclature_center_pts.dbf'
OUT = os.path.join('static', 'js', 'nova_mars_features.js')
CREDIT = 'Gazetteer of Planetary Nomenclature, IAU WGPSN / USGS Astrogeology'

# name: tier. The classical albedo markings first: they are what a small telescope
# actually shows, and what the central meridian is read against.
SELECTION = {
    # the classical albedo markings
    'Syrtis Major': 1, 'Solis Lacus': 1, 'Hellas': 1, 'Mare Acidalium': 1,
    'Mare Erythraeum': 1, 'Mare Sirenum': 1, 'Mare Tyrrhenum': 2, 'Mare Cimmerium': 2,
    'Sinus Meridiani': 1, 'Sinus Sabaeus': 1, 'Aurorae Sinus': 2, 'Oxia Palus': 2,
    'Margaritifer Sinus': 2, 'Trivium Charontis': 2, 'Cerberus': 2, 'Deucalionis Regio': 2,
    'Protei Regio': 2, 'Pyrrhae Regio': 2, 'Hesperia': 2, 'Noachis': 2, 'Phaethontis': 2,
    'Tharsis': 2, 'Elysium': 2, 'Utopia': 2, 'Amazonis': 2, 'Chryse': 2, 'Argyre': 2,
    'Aeolis': 2, 'Zephyria': 2, 'Memnonia': 2, 'Eridania': 2, 'Electris': 2, 'Candor': 2,
    'Coprates': 2,
    # the volcanoes
    'Olympus Mons': 1, 'Alba Mons': 1, 'Ascraeus Mons': 1, 'Pavonis Mons': 1,
    'Arsia Mons': 1, 'Elysium Mons': 1, 'Hecates Tholus': 2, 'Albor Tholus': 2,
    'Ceraunius Tholus': 2, 'Tharsis Tholus': 2, 'Uranius Tholus': 2,
    'Apollinaris Mons': 2, 'Hadriacus Mons': 2, 'Tyrrhenus Mons': 2,
    'Uranius Patera': 2, 'Alba Patera': 2, 'Amphitrites Patera': 2, 'Peneus Patera': 2,
    # the great basins and plains
    'Hellas Planitia': 1, 'Argyre Planitia': 1, 'Isidis Planitia': 1, 'Chryse Planitia': 1,
    'Utopia Planitia': 1, 'Elysium Planitia': 1, 'Acidalia Planitia': 1,
    'Amazonis Planitia': 1, 'Arcadia Planitia': 2, 'Syrtis Major Planum': 1,
    'Hesperia Planum': 2, 'Syria Planum': 2, 'Lunae Planum': 2, 'Sinai Planum': 2,
    'Daedalia Planum': 2, 'Icaria Planum': 2, 'Ascuris Planum': 2,
    'Vastitas Borealis': 1, 'Noachis Terra': 2, 'Terra Cimmeria': 2, 'Terra Sirenum': 2,
    'Arabia Terra': 1, 'Terra Sabaea': 2, 'Tempe Terra': 2, 'Aonia Terra': 2,
    # the canyons and valleys
    'Valles Marineris': 1, 'Noctis Labyrinthus': 1, 'Candor Chasma': 1, 'Ophir Chasma': 2,
    'Melas Chasma': 2, 'Coprates Chasma': 1, 'Ganges Chasma': 2, 'Echus Chasma': 2,
    'Ius Chasma': 2, 'Tithonium Chasma': 2, 'Hebes Chasma': 2, 'Kasei Valles': 1,
    'Ares Vallis': 1, 'Mawrth Vallis': 2, 'Tiu Valles': 2, 'Nirgal Vallis': 2,
    'Mangala Valles': 2, 'Uzboi Vallis': 2, 'Ladon Valles': 2, 'Scamander Vallis': 2,
    'Naktong Vallis': 2,
    # the rough ground
    'Aureum Chaos': 1, 'Iani Chaos': 2, 'Hydraotes Chaos': 2, 'Nili Fossae': 1,
    'Cerberus Fossae': 2, 'Sirenum Fossae': 2, 'Icaria Fossae': 2, 'Mareotis Fossae': 2,
    'Tantalus Fossae': 2, 'Ceraunius Fossae': 2, 'Ulysses Fossae': 2, 'Alba Fossae': 2,
    'Nepenthes Mensae': 2, 'Aeolis Mensae': 2, 'Amenthes Rupes': 2, 'Claritas Rupes': 2,
    'Ogygis Rupes': 2, 'Bosporos Rupes': 2, 'Elysium Rupes': 2,
    # craters worth naming, and the two the rovers are in
    'Gale': 1, 'Jezero': 1, 'Huygens': 2, 'Schiaparelli': 2, 'Newton': 2,
    'Copernicus': 2, 'Becquerel': 2, 'Korolev': 2,
}

TYPES = {'AL': 'albedo', 'MO': 'mons', 'PM': 'planum', 'PL': 'planitia', 'VA': 'vallis',
         'CM': 'chasma', 'LB': 'labyrinthus', 'CH': 'chaos', 'PE': 'patera', 'TH': 'tholus',
         'TA': 'terra', 'VS': 'vastitas', 'FO': 'fossa', 'MN': 'mensa', 'RU': 'rupes',
         'AA': 'crater'}


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
    for name, tier in SELECTION.items():
        row = by_name.get(name)
        if row is None:
            sys.exit(f'{name}: not in the gazetteer')
        if row['approval'] != 'Adopted by IAU':
            sys.exit(f"{name}: not adopted ({row['approval']})")
        code = row['code']
        if code not in TYPES:
            sys.exit(f"{name}: no type for gazetteer code {code!r}")
        lon = (float(row['center_lon']) + 180.0) % 360.0 - 180.0
        features.append({
            'name': name, 'type': TYPES[code],
            'lat': round(float(row['center_lat']), 2), 'lon': round(lon, 2),
            'diameterKm': round(float(row['diameter']), 1), 'tier': tier,
        })

    body = ',\n'.join('        ' + json.dumps(f, ensure_ascii=False) for f in features)
    js = f"""/*
 * nova_mars_features.js — the Martian features the Solar System page's Mars card names.
 * Generated by tools/gen_mars_features.py; do not edit by hand.
 *
 * Source: {CREDIT}
 * (public domain), centre points as of {source_date}.
 * {len(features)} features, curated by name. Latitude and longitude in degrees,
 * planetocentric and longitude east-positive -180..180 (IAU: Syrtis Major at +70, Gale
 * at +137.8); diameter in km, and 0 for the albedo features, whose extent the gazetteer
 * does not give. tier 1: the features an imager identifies first. Names are IAU names and
 * are not translated; the page translates the type.
 */
(function (root, factory) {{
    if (typeof module === 'object' && module.exports) {{
        module.exports = factory();
    }} else {{
        root.NovaMarsFeatures = factory();
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
