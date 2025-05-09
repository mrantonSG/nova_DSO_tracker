import math
import numpy as np
import requests
import time
import re
from astroquery.simbad import Simbad
from astroquery.vizier import Vizier

# Script to retrieve Deep Space Object data (Type, Magnitude, Size, SB)
# Uses astroquery (SIMBAD, VizieR) with fallback to Stellarium Remote Control API
# Designed to be importable as a module.
# Last Updated: 2025-05-09

__all__ = [
    "get_astronomical_data",
    # ... (other functions remain in __all__)
]


#############################
#   Astroquery Functions    #
#############################

# --- Object Type ---
def get_object_type_from_simbad(object_name):
    """Fetch object type from SIMBAD, trying otype, morph_type, and otypedef."""
    print(f"[DEBUG] Attempting SIMBAD query for type of '{object_name}'...")
    custom_simbad = Simbad()
    custom_simbad.TIMEOUT = 60
    custom_simbad.ROW_LIMIT = 1
    custom_simbad.add_votable_fields('otype', 'morph_type', 'otypedef')

    try:
        result_table = custom_simbad.query_object(object_name)
        if result_table is None or len(result_table) == 0:
            print(f"[DEBUG] SIMBAD query for '{object_name}' returned no result table.")
            return None

        def get_field_value(table, field_name_variants):
            for name_variant in field_name_variants:
                actual_col_name = None
                if name_variant in table.colnames:
                    actual_col_name = name_variant
                elif name_variant.upper() in table.colnames:
                    actual_col_name = name_variant.upper()
                elif name_variant.lower() in table.colnames:
                    actual_col_name = name_variant.lower()

                if actual_col_name and len(table[actual_col_name]) > 0 and not np.ma.is_masked(
                        table[actual_col_name][0]):
                    raw_value = table[actual_col_name][0]
                    if isinstance(raw_value, bytes):
                        return raw_value.decode('utf-8').strip()
                    elif isinstance(raw_value, str):
                        return raw_value.strip()
                    elif raw_value is not None:
                        try:
                            return str(raw_value).strip()
                        except Exception:
                            pass
            return None

        otype = get_field_value(result_table, ['otype', 'OTYPE'])
        morph_type = get_field_value(result_table, ['morph_type', 'MORPH_TYPE'])
        otype_def = get_field_value(result_table, ['otypedef', 'OTYPEDEF'])

        print(
            f"[DEBUG] SIMBAD raw types for '{object_name}': otype='{otype}', morph_type='{morph_type}', otype_def='{otype_def}'")

        generic_otypes = ['--', '?', '*', 'Object', 'Region', 'Unknown', 'Candidate', '']
        galaxy_activity_types = ["AGN", "SyG", "Sy1", "Sy2", "LINER", "SBG", "GiG",
                                 "BiC"]  # Galaxy in Cluster, Blazar Candidate etc.

        # Priority 1: Specific otype (that isn't a generic galaxy activity type if better morph is available)
        if otype and otype not in generic_otypes:
            if otype in galaxy_activity_types:
                # If otype is an activity type, check if morph_type is more descriptive of the host
                if morph_type and morph_type not in generic_otypes and morph_type.lower() != "galaxy":
                    print(
                        f"[INFO] SIMBAD using morph_type '{morph_type}' (host) for AGN-like otype '{otype}' for '{object_name}'")
                    return morph_type
                else:  # morph_type is generic or missing, use the activity type or a general "Galaxy"
                    # If otype_def gives a more specific galaxy type (e.g. "Starburst Galaxy" for SBG)
                    if otype_def and "galaxy" in otype_def.lower() and otype_def.lower() != "galaxy":
                        if "starburst" in otype_def.lower(): return "SBG"
                        print(
                            f"[INFO] SIMBAD using otypedef '{otype_def}' for AGN-like otype '{otype}' for '{object_name}'")
                        return otype_def  # Could be "Seyfert 1 Galaxy", etc.
                    print(f"[INFO] SIMBAD object type (from otype - activity) found for '{object_name}': {otype}")
                    return otype  # Return the specific activity type like AGN, SyG
            else:  # Not an activity type, but a specific otype (e.g., PN, SNR)
                print(f"[INFO] SIMBAD object type (from specific otype) found for '{object_name}': {otype}")
                return otype

        # Priority 2: Morphological type (if otype was generic/missing)
        if morph_type and morph_type not in generic_otypes:
            print(f"[INFO] SIMBAD object type (from morph_type) found for '{object_name}': {morph_type}")
            return morph_type

        # Priority 3: otypedef (description of the main type)
        if otype_def and otype_def not in generic_otypes:
            # Try to map common descriptions to codes
            otype_dscr_lower = otype_def.lower()
            if "starburst galaxy" in otype_dscr_lower: return "SBG"
            if "seyfert 1 galaxy" in otype_dscr_lower: return "Sy1"  # More specific
            if "seyfert 2 galaxy" in otype_dscr_lower: return "Sy2"  # More specific
            if "seyfert galaxy" in otype_dscr_lower: return "SyG"
            if "liner-type active galaxy nucleus" in otype_dscr_lower: return "LINER"
            if "galaxy" in otype_dscr_lower: return "Galaxy"
            if "planetary nebula" in otype_dscr_lower: return "PN"
            if "hii region" in otype_dscr_lower: return "HII"
            if "emission nebula" in otype_dscr_lower: return "EmN"
            if "reflection nebula" in otype_dscr_lower: return "RfN"
            if "dark nebula" in otype_dscr_lower: return "DkN"
            if "open cluster" in otype_dscr_lower: return "OpC"
            if "globular cluster" in otype_dscr_lower: return "GlC"
            if "supernova remnant" in otype_dscr_lower: return "SNR"
            if "nebula" in otype_dscr_lower: return "Nebula"
            if len(otype_def) < 30:
                print(f"[INFO] SIMBAD object type (from otypedef) for '{object_name}': {otype_def}")
                return otype_def

        print(f"[DEBUG] No definitive type found in SIMBAD for '{object_name}'.")
        return None

    except Exception as e:
        if "is not one of the accepted options" in str(e):
            print(f"[ERROR] SIMBAD field name error for '{object_name}': {e}. Check Simbad.list_votable_fields().")
        else:
            print(f"[ERROR] Failed query/process SIMBAD type for '{object_name}': {e}")
        return None


# --- Magnitude ---
def get_magnitude_from_simbad(object_name):
    """Fetch apparent magnitude (V-band) from SIMBAD."""
    custom_simbad = Simbad();
    custom_simbad.TIMEOUT = 60;
    custom_simbad.ROW_LIMIT = 1
    custom_simbad.add_votable_fields('V')  # Corrected from 'flux(V)'
    try:
        result = custom_simbad.query_object(object_name)
        if result is None or len(result) == 0: return None
        mag_col_name = None
        potential_mag_cols = ['V', 'FLUX_V']
        for col in potential_mag_cols:
            actual_col_name = None
            if col in result.colnames:
                actual_col_name = col
            elif col.upper() in result.colnames:
                actual_col_name = col.upper()
            elif col.lower() in result.colnames:
                actual_col_name = col.lower()
            if actual_col_name and len(result[actual_col_name]) > 0 and not np.ma.is_masked(result[actual_col_name][0]):
                mag_col_name = actual_col_name
                break
        if mag_col_name:
            vmag = result[mag_col_name][0]
            if isinstance(vmag, (int, float)) and not np.isnan(vmag):
                print(f"[DEBUG] SIMBAD magnitude ({mag_col_name}) for {object_name}: {vmag}")
                return float(vmag)
        return None
    except Exception as e:
        print(f"[ERROR] Failed query SIMBAD magnitude for {object_name}: {e}")
        return None


def get_magnitude_from_vizier(object_name):
    """Fetch magnitude from multiple VizieR catalogs."""
    vizier_catalogs = [
        "VII/118/ngc2000", "VII/267/sac_81", "I/297/out", "VII/202A/diffuse",
        "V/84A/PKGB", "VII/42/bgc", "VII/239", "I/306/out", "I/253/out",
        "J/ApJS/227/24/opennNGC"
    ]
    try:
        vizier = Vizier(columns=['*'], catalog=vizier_catalogs, timeout=60)
        result_tables = vizier.query_object(object_name)
        if not result_tables or len(result_tables) == 0: return None
        for table in result_tables:
            cat_id = table.meta.get('ID', 'Unknown Catalog')
            mag_cols_priority = ['Vmag', 'Bmag', 'mag']
            for col_name in mag_cols_priority:
                if col_name in table.colnames and len(table[col_name]) > 0:
                    mag_val = table[col_name][0]
                    if mag_val is not None and not np.ma.is_masked(mag_val):
                        try:
                            mag_float = float(mag_val)
                            if not np.isnan(mag_float):
                                print(f"[DEBUG] VizieR {col_name} for {object_name} from {cat_id}: {mag_float}")
                                return mag_float
                        except (ValueError, TypeError):
                            pass
        return None
    except Exception as e:
        print(f"[ERROR] Failed to query VizieR for magnitude of {object_name}: {e}")
        return None


def get_magnitude_from_hyperleda(object_name):
    """Fetch magnitude from HyperLEDA (Bmag values) via VizieR."""
    try:
        vizier = Vizier(columns=['*'], catalog="VII/237", timeout=60)
        result_tables = vizier.query_object(object_name)
        if not result_tables or len(result_tables) == 0: return None
        table = result_tables[0]
        mag_cols = ['vtmag', 'btmag', 'Bmag', 'Vmag', 'Imag']
        for col_name in mag_cols:
            if col_name in table.colnames and len(table[col_name]) > 0:
                magnitude_val = table[col_name][0]
                if not np.ma.is_masked(magnitude_val) and isinstance(magnitude_val, (int, float)) and not np.isnan(
                        magnitude_val):
                    print(f"[DEBUG] HyperLEDA {col_name} for {object_name}: {magnitude_val}")
                    return float(magnitude_val)
        return None
    except Exception as e:
        print(f"[ERROR] Failed query HyperLEDA magnitude for {object_name}: {e}")
        return None


def get_magnitude(object_name):
    """Try multiple sources to fetch the apparent magnitude, tracking the source."""
    magnitude_val = get_magnitude_from_simbad(object_name)
    if magnitude_val is not None: return {"value": magnitude_val, "source": "SIMBAD"}
    print(f"[INFO] Magnitude not found in SIMBAD for {object_name}, trying VizieR...")
    magnitude_val = get_magnitude_from_vizier(object_name)
    if magnitude_val is not None: return {"value": magnitude_val, "source": "VizieR"}
    print(f"[INFO] Magnitude not found in VizieR for {object_name}, trying HyperLEDA...")
    magnitude_val = get_magnitude_from_hyperleda(object_name)
    if magnitude_val is not None: return {"value": magnitude_val, "source": "HyperLEDA"}
    print(f"[WARN] No magnitude found via Astroquery for {object_name}")
    return {"value": None, "source": None}


# --- Angular Size ---
def get_angular_size_from_simbad(object_name):
    """Fetch angular size (major axis in arcmin) from SIMBAD."""
    custom_simbad = Simbad();
    custom_simbad.TIMEOUT = 60;
    custom_simbad.ROW_LIMIT = 1
    custom_simbad.add_votable_fields('galdim_majaxis', 'dimensions')
    try:
        result = custom_simbad.query_object(object_name)
        if result is None or len(result) == 0: return None
        size_col_candidates = ['GALDIM_MAJAXIS', 'galdim_majaxis']
        for col_name in size_col_candidates:
            actual_col_name = None
            if col_name in result.colnames:
                actual_col_name = col_name
            elif col_name.upper() in result.colnames:
                actual_col_name = col_name.upper()
            elif col_name.lower() in result.colnames:
                actual_col_name = col_name.lower()
            if actual_col_name and len(result[actual_col_name]) > 0 and not np.ma.is_masked(result[actual_col_name][0]):
                value = result[actual_col_name][0]
                try:
                    size = float(value)
                    if not np.isnan(size) and size > 0:
                        print(f"[DEBUG] SIMBAD angular size ('{actual_col_name}') for {object_name}: {size} arcmin")
                        return {"value": size, "source": "SIMBAD (galdim_majaxis)"}
                except (ValueError, TypeError) as e:
                    print(f"[DEBUG] Error converting SIMBAD '{actual_col_name}' ({value}): {e}")
        dimensions_col_name = None
        dim_variants = ['dimensions', 'DIMENSIONS']
        for dv in dim_variants:
            if dv in result.colnames:
                dimensions_col_name = dv
                break
        if dimensions_col_name and len(result[dimensions_col_name]) > 0 and not np.ma.is_masked(
                result[dimensions_col_name][0]):
            dims_bytes = result[dimensions_col_name][0]
            dims = dims_bytes.decode('utf-8').strip() if isinstance(dims_bytes, bytes) else str(dims_bytes).strip()
            if dims and dims != "--" and dims != "":
                try:
                    tokens = dims.replace('x', ' ').split()
                    if len(tokens) > 0:
                        match = re.search(r'([-+]?\d*\.?\d+)', tokens[0])
                        if match:
                            size_str = match.group(0);
                            size = float(size_str)
                            if not np.isnan(size) and size > 0:
                                print(
                                    f"[DEBUG] SIMBAD angular size (from dimensions '{dims}') for {object_name}: {size} arcmin")
                                return {"value": size, "source": "SIMBAD (dimensions)"}
                except Exception as e:
                    print(f"[DEBUG] Error parsing dimensions '{dims}': {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Failed to query SIMBAD for angular size of {object_name}: {e}")
        return None


def get_angular_size_from_vizier(object_name):
    """Fetch angular size (in arcmin) from VizieR catalogs"""
    vizier_catalogs = [
        "VII/118/ngc2000", "VII/267/sac_81", "VII/239", "VII/202A/diffuse",
        "V/84A/PKGB", "VII/42/bgc", "I/297/out", "I/306/out", "I/253/out",
        "J/ApJS/227/24/opennNGC"
    ]
    try:
        vizier = Vizier(columns=['*'], catalog=vizier_catalogs, timeout=60)
        result_tables = vizier.query_object(object_name)
        if not result_tables or len(result_tables) == 0: return None
        for table in result_tables:
            cat_id = table.meta.get('ID', 'Unknown Catalog')
            size_cols_to_check = {
                'MajAx': 1, 'maj': 1, 'Diam': 1, 'diam': 1,
                'Size': 1, 'AngSize': 1, 'D25': 1, ' основных размеров ': 1, 'rad': 1,
                'MajAxs': 1 / 60.0, 'majax': 1 / 60.0, 'Diamas': 1 / 60.0,
            }
            for col, multiplier in size_cols_to_check.items():
                if col in table.colnames and len(table[col]) > 0:
                    size_val = table[col][0]
                    if size_val is not None and not np.ma.is_masked(size_val):
                        try:
                            if isinstance(size_val, (str, bytes)):
                                if isinstance(size_val, bytes): size_val = size_val.decode('utf-8')
                                match = re.search(r'([-+]?\d*\.?\d+)', size_val)
                                if match:
                                    size_num_str = match.group(0)
                                    size_num = float(size_num_str) * multiplier
                                else:
                                    continue
                            else:
                                size_num = float(size_val) * multiplier
                            if not np.isnan(size_num) and size_num > 0:
                                print(
                                    f"[DEBUG] VizieR {col} angular size for {object_name} from {cat_id}: {size_num:.2f} arcmin")
                                return {"value": size_num, "source": f"VizieR ({cat_id})"}
                        except (ValueError, TypeError) as e:
                            pass
        return None
    except Exception as e:
        print(f"[ERROR] Failed to query VizieR for angular size of {object_name}: {e}")
        return None


def get_angular_size_from_hyperleda(object_name):
    """Fetch angular size from HyperLEDA (using logd25 value) via VizieR."""
    try:
        vizier = Vizier(columns=['logd25'], catalog="VII/237", timeout=60)
        result_tables = vizier.query_object(object_name)
        if not result_tables or len(result_tables) == 0: return None
        table = result_tables[0]
        if 'logd25' in table.colnames and len(table['logd25']) > 0:
            logd25 = table['logd25'][0]
            if logd25 is not None and not np.ma.is_masked(logd25) and isinstance(logd25, (int, float)) and not np.isnan(
                    logd25):
                try:
                    diameter_in_tenth_arcmin = (10 ** float(logd25))
                    diameter_arcmin = diameter_in_tenth_arcmin / 10.0
                    if not np.isnan(diameter_arcmin) and diameter_arcmin > 0:
                        print(
                            f"[DEBUG] HyperLEDA angular size (from logd25={logd25}) for {object_name}: {diameter_arcmin:.2f} arcmin")
                        return {"value": diameter_arcmin, "source": "HyperLEDA"}
                except (ValueError, OverflowError) as calc_e:
                    print(f"[DEBUG] Error calculating diameter from logd25 {logd25}: {calc_e}")
        return None
    except Exception as e:
        print(f"[ERROR] Failed query HyperLEDA angular size for {object_name}: {e}")
        return None


def get_angular_size(object_name):
    """Try multiple sources to fetch the angular size (major axis in arcmin) with source info."""
    size = get_angular_size_from_simbad(object_name)
    if size and size.get("value") is not None: return size
    print(f"[INFO] Angular size not found in SIMBAD for {object_name}, trying VizieR...")
    size = get_angular_size_from_vizier(object_name)
    if size and size.get("value") is not None: return size
    print(f"[INFO] Angular size not found in VizieR for {object_name}, trying HyperLEDA...")
    size = get_angular_size_from_hyperleda(object_name)
    if size and size.get("value") is not None: return size
    print(f"[WARN] No angular size found via Astroquery for {object_name}")
    return {"value": None, "source": None}


def get_stellarium_data(object_name, stellarium_ip="localhost", port=8090):
    """Fetch data from Stellarium API. (Constellation removed)"""
    url = f"http://{stellarium_ip}:{port}/api/objects/info";
    params = {"name": object_name, "format": "json"}
    stell_data = {
        "magnitude": None, "mag_source": None,
        "size_arcmin": None, "size_source": None,
        "object_type": None, "type_source": None,
    }
    try:
        response = requests.get(url, params=params, timeout=5);
        response.raise_for_status();
        data = response.json()
        if not data.get("found", False):
            print(f"⚠️ Stellarium API: Object '{object_name}' not found.");
            return None
        print(f"✅ Stellarium Data Found for {object_name}.")
        magnitude_val = data.get("vmag", data.get("magnitude"))
        if magnitude_val is not None:
            try:
                stell_data["magnitude"] = float(magnitude_val); stell_data["mag_source"] = "Stellarium"
            except (ValueError, TypeError):
                pass
        size_deg = data.get("size-dd", None);
        size_arcmin_str = data.get("angularSize", [None])[0];
        final_size_arcmin = None
        if size_deg is not None:
            try:
                final_size_arcmin = float(size_deg) * 60
            except (ValueError, TypeError):
                final_size_arcmin = None
        if final_size_arcmin is None and size_arcmin_str:
            try:
                match = re.search(r'([-+]?\d*\.?\d+)', size_arcmin_str)
                if match:
                    size_val = float(match.group(0))
                    if '°' in size_arcmin_str or 'deg' in size_arcmin_str.lower():
                        final_size_arcmin = size_val * 60
                    elif '"' in size_arcmin_str or 'arcsec' in size_arcmin_str.lower():
                        final_size_arcmin = size_val / 60.0
                    elif "'" in size_arcmin_str or 'arcmin' in size_arcmin_str.lower():
                        final_size_arcmin = size_val
                    else:
                        final_size_arcmin = size_val
            except (ValueError, TypeError):
                final_size_arcmin = None
        if final_size_arcmin is not None and final_size_arcmin > 0:
            stell_data["size_arcmin"] = final_size_arcmin;
            stell_data["size_source"] = "Stellarium"
        obj_type = data.get("objectType")
        if obj_type:
            obj_type_str = str(obj_type).strip()
            type_mapping_stellarium = {
                "Planetary Nebula": "PN", "Open Cluster": "OpC", "Globular Cluster": "GlC",
                "Galaxy": "Galaxy", "Bright Nebula": "Nebula", "Dark Nebula": "DkN",
                "Emission Nebula": "EmN", "Reflection Nebula": "RfN", "Supernova Remnant": "SNR",
            }
            stell_data["object_type"] = type_mapping_stellarium.get(obj_type_str, obj_type_str)
            stell_data["type_source"] = "Stellarium"
        return stell_data
    except requests.exceptions.ConnectionError:
        print(f"⚠️ Stellarium connection failed at {stellarium_ip}:{port}."); return None
    except requests.exceptions.Timeout:
        print(f"⚠️ Stellarium request timed out for {object_name}."); return None
    except requests.exceptions.RequestException as e:
        if e.response is not None and e.response.status_code == 404:
            print(f"⚠️ Stellarium request failed for {object_name}: Object not found (404).")
        else:
            print(f"⚠️ Stellarium request failed unexpectedly for {object_name}: {e}")
        return None
    except Exception as e:
        print(f"⚠️ An unexpected error occurred processing Stellarium data for {object_name}: {e}"); return None


def calculate_surface_brightness(magnitude, size_arcmin):
    """Calculate average surface brightness (mag/arcmin²)."""
    if magnitude is None or size_arcmin is None or size_arcmin <= 0: return None
    try:
        area_arcmin2 = math.pi * (float(size_arcmin) / 2.0) ** 2
        if area_arcmin2 <= 0: return None
        sb = float(magnitude) + 2.5 * math.log10(area_arcmin2)
        return round(sb, 2)
    except (ValueError, TypeError, OverflowError) as e:
        print(f"[ERROR] Could not calculate SB for mag={magnitude}, size={size_arcmin}. Error: {e}")
        return None


def get_astronomical_data(object_name, stellarium_ip="localhost", port=8090):
    """
    Main function to retrieve astronomical data for a given object name.
    Queries SIMBAD, VizieR, and optionally Stellarium.
    Returns a dictionary with the findings (Type, Mag, Size, SB).
    """
    print(f"\n{'=' * 10} Searching for: {object_name} {'=' * 10}")
    obj_type = get_object_type_from_simbad(object_name)
    type_source = "SIMBAD" if obj_type else None
    mag_data = get_magnitude(object_name)
    size_data = get_angular_size(object_name)
    stell_data = None
    needs_fallback = (obj_type is None) or (mag_data["value"] is None) or (size_data["value"] is None)
    if needs_fallback:
        print(f"--- Checking Stellarium for missing data for {object_name} ---")
        stell_data = get_stellarium_data(object_name, stellarium_ip, port)
        if stell_data:
            if obj_type is None and stell_data.get("object_type"):
                obj_type = stell_data["object_type"];
                type_source = stell_data["type_source"]
                print(f"--> Using Stellarium fallback for Object Type: {obj_type}")
            if mag_data["value"] is None and stell_data.get("magnitude") is not None:
                mag_data = {"value": stell_data["magnitude"], "source": stell_data["mag_source"]}
                print(f"--> Using Stellarium fallback for Magnitude: {mag_data['value']:.2f}")
            if size_data["value"] is None and stell_data.get("size_arcmin") is not None:
                size_data = {"value": stell_data["size_arcmin"], "source": stell_data["size_source"]}
                print(f"--> Using Stellarium fallback for Angular Size: {size_data['value']:.2f} arcmin")
        elif needs_fallback:
            print(f"--- Stellarium fallback failed or provided no usable data for {object_name} ---")
    sb = calculate_surface_brightness(mag_data["value"], size_data["value"])
    final_data = {
        "object_name": object_name, "object_type": obj_type, "type_source": type_source,
        "magnitude": mag_data["value"], "mag_source": mag_data.get('source'),
        "size_arcmin": size_data["value"], "size_source": size_data.get('source'),
        "surface_brightness": sb,
    }
    if final_data["object_type"] is None: print(
        f"[DATA_MISSING] Object Type not found for {object_name} from any source.")
    if final_data["magnitude"] is None: print(f"[DATA_MISSING] Magnitude not found for {object_name} from any source.")
    if final_data["size_arcmin"] is None: print(
        f"[DATA_MISSING] Angular Size not found for {object_name} from any source.")
    return final_data


def suggest_aperture(object_type=None, surface_brightness=None, angular_size=None):
    """Suggest aperture based on object size, brightness, and type."""
    if object_type: object_type = object_type.lower()
    if angular_size and angular_size > 30 and object_type and \
            ('nebula' in object_type or 'emission' in object_type or 'hii' in object_type or 'cl*' in object_type):
        return "50–100 mm (wide-field)"
    if surface_brightness:
        if surface_brightness < 12:
            return "100–150 mm"
        elif surface_brightness < 14:
            return "150–200 mm"
        else:
            return "200+ mm"
    return "N/A"


def compute_imaging_recommendations(magnitude, surface_brightness, angular_size_arcmin, object_type=None):
    """Calculate recommended FOV ranges (simple estimate)."""
    recs = {'fov_min_fit': "N/A", 'fov_min_detail': "N/A"}
    if angular_size_arcmin:
        try:
            fov_fit_min = int(angular_size_arcmin * 1.1);
            fov_fit_max = int(angular_size_arcmin * 1.3)
            fov_detail_min = int(angular_size_arcmin * 0.7);
            fov_detail_max = int(angular_size_arcmin * 1.0)
            recs['fov_min_fit'] = f"{fov_fit_min}–{fov_fit_max}′"
            recs['fov_min_detail'] = f"{fov_detail_min}–{fov_detail_max}′"
        except Exception:
            pass
    return recs


if __name__ == '__main__':
    objects_to_test = ["M1", "M31", "NGC 253", "M82", "NGC 1316", "FakeObject"]
    print(f"Starting TEST data retrieval for {len(objects_to_test)} objects...")
    for target_object in objects_to_test:
        print(f"\n{'=' * 10} Querying: {target_object} {'=' * 10}")
        try:
            object_data = get_astronomical_data(target_object)
            print("\n--- Result ---")
            for key, value in object_data.items():
                if isinstance(value, float):
                    print(f"  {key}: {value:.2f}")
                else:
                    print(f"  {key}: {value}")
            print(f"--- Finished query for {target_object} ---")
        except Exception as e:
            print(f"[CRITICAL ERROR] Unhandled exception during query for {target_object}: {e}")
        time.sleep(1)
    print("\nTest finished.")
