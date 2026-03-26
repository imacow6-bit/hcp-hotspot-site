"""
Backfill prescriber_scores.json with city-level coordinates.
Reads NPI -> city from Part D and places prescribers using
deterministic state-aware ZIP anchors.
"""
import json, csv, zipfile, random, os, hashlib
from collections import defaultdict

random.seed(42)

print("=== Prescriber Geo-Backfill (Real City Coordinates) ===\n")

# ─── Step 1: Load uscities.csv → (CITY_UPPER, STATE) → (lat, lng) ────────────
print("[1/4] Loading uscities.csv city geocoder...")

# Common abbreviations in CMS data → full form in uscities.csv
CITY_ALIASES = {
    "ST ": "SAINT ",
    "ST. ": "SAINT ",
    "FT ": "FORT ",
    "FT. ": "FORT ",
    "MT ": "MOUNT ",
    "MT. ": "MOUNT ",
    "N ": "NORTH ",
    "S ": "SOUTH ",
    "E ": "EAST ",
    "W ": "WEST ",
}

def normalize_city(name):
    """Normalize city name for matching: uppercase, expand abbreviations."""
    name = name.strip().upper()
    # Expand leading abbreviations
    for abbr, full in CITY_ALIASES.items():
        if name.startswith(abbr):
            name = full + name[len(abbr):]
            break
    return name

city_coords = {}  # (CITY_UPPER, STATE) → (lat, lng)
with open("uscities.csv", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        city = normalize_city(row["CITY"])
        state = row["STATE_CODE"].strip().upper()
        lat = float(row["LATITUDE"])
        lng = float(row["LONGITUDE"])
        key = (city, state)
        if key not in city_coords:
            city_coords[key] = (lat, lng)

print(f"  Loaded {len(city_coords):,} unique (city, state) pairs")

# ─── Step 2: Build NPI → (city, state) from Part D ───────────────────────────
print("\n[2/4] Reading Part D for NPI → city mapping...")
npi_city = {}
rows_read = 0
with zipfile.ZipFile("partd_by_drug.csv.zip") as zf:
    csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
    with zf.open(csv_name) as f:
        reader = csv.DictReader(line.decode("utf-8", errors="replace") for line in f)
        for row in reader:
            rows_read += 1
            npi = row.get("Prscrbr_NPI", "").strip()
            if not npi or npi in npi_city:
                continue
            city = row.get("Prscrbr_City", "").strip().upper()
            state = row.get("Prscrbr_State_Abrvtn", "").strip().upper()
            if city and state:
                npi_city[npi] = (city, state)
            if rows_read % 5_000_000 == 0:
                print(f"  ... {rows_read:,} rows, {len(npi_city):,} NPIs mapped")

print(f"  Done: {rows_read:,} rows → {len(npi_city):,} NPI city mappings")

# ─── Step 3: Build state centroids as last-resort fallback ────────────────────
print("\n[3/4] Building state centroids (fallback only)...")

with open("src/zip_level_data.json") as f:
    zip_data = json.load(f)

# Build state bounding boxes + sorted ZIP anchors per state
state_bounds = defaultdict(lambda: {
    "min_lat": 90, "max_lat": -90,
    "min_lng": 180, "max_lng": -180,
    "count": 0
})
state_zip_anchors = defaultdict(list)

for z in zip_data:
    s = state_bounds[z["state"]]
    s["min_lat"] = min(s["min_lat"], z["lat"])
    s["max_lat"] = max(s["max_lat"], z["lat"])
    s["min_lng"] = min(s["min_lng"], z["lng"])
    s["max_lng"] = max(s["max_lng"], z["lng"])
    s["count"] += 1
    docs_total = sum(z.get("docs", {}).values())
    state_zip_anchors[z["state"]].append({
        "lat": z["lat"],
        "lng": z["lng"],
        "weight": docs_total if docs_total > 0 else 1,
    })

for state in state_zip_anchors:
    anchors = sorted(state_zip_anchors[state], key=lambda x: x["weight"], reverse=True)
    state_zip_anchors[state] = anchors[:250]

print(f"  Built ZIP anchors for {len(state_zip_anchors)} states")

def stable_int(value, bits=64):
    """Stable hash helper (independent of PYTHONHASHSEED)."""
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest()
    return int.from_bytes(digest, "big") & ((1 << bits) - 1)

def city_to_coords(city, state):
    """Deterministically map a city to a weighted ZIP anchor within its state."""
    bounds = state_bounds.get(state)
    anchors = state_zip_anchors.get(state)
    if not bounds or not anchors:
        return None

    key = f"{city}|{state}"
    h1 = stable_int(f"{key}|anchor")
    h2 = stable_int(f"{key}|jitter")

    anchor = anchors[h1 % len(anchors)]

    lat_range = max(bounds["max_lat"] - bounds["min_lat"], 1.0)
    lng_range = max(bounds["max_lng"] - bounds["min_lng"], 1.0)
    lat_jitter = (((h2 & 0xFFFF) / 0xFFFF) - 0.5) * (0.06 * lat_range)
    lng_jitter = ((((h2 >> 16) & 0xFFFF) / 0xFFFF) - 0.5) * (0.06 * lng_range)

    return (anchor["lat"] + lat_jitter, anchor["lng"] + lng_jitter)

# ─── Step 4: Patch prescriber_scores.json ─────────────────────────────────────
print("\n[4/4] Patching prescriber_scores.json with real coordinates...")

with open("public/prescriber_scores.json") as f:
    prescribers = json.load(f)

print(f"  Loaded {len(prescribers):,} prescribers")

exact_match = 0
normalized_match = 0
state_fallback = 0
dropped = 0

def lookup_city(city_raw, state):
    """Try to find real coordinates for a city. Returns (lat, lng) or None."""
    # Exact match (already uppercase from Part D)
    key = (city_raw, state)
    if key in city_coords:
        return city_coords[key]

    # Normalized match (expand abbreviations)
    norm = normalize_city(city_raw)
    key_norm = (norm, state)
    if key_norm in city_coords:
        return city_coords[key_norm]

    # Try without hyphens/periods
    cleaned = norm.replace("-", " ").replace(".", "")
    key_clean = (cleaned, state)
    if key_clean in city_coords:
        return city_coords[key_clean]

    return None

for p in prescribers:
    npi = str(p.get("npi", ""))
    city_state = npi_city.get(npi)

    if city_state:
        city_raw, state = city_state
        coords = lookup_city(city_raw, state)

        if coords:
            # Real city coordinates + small jitter for metro spread
            jitter_lat = random.gauss(0, 0.012)  # ~0.8 mile std dev
            jitter_lng = random.gauss(0, 0.015)
            p["lat"] = round(coords[0] + jitter_lat, 4)
            p["lng"] = round(coords[1] + jitter_lng, 4)
            p["city"] = city_raw.title()  # Save city name for future use
            if (city_raw, state) in city_coords:
                exact_match += 1
            else:
                normalized_match += 1
            continue

    # Last-resort fallback: deterministic ZIP anchor for the state
    state = p.get("state", "")
    coords = city_to_coords("UNKNOWN", state)
    if coords:
        p["lat"] = round(coords[0], 4)
        p["lng"] = round(coords[1], 4)
        state_fallback += 1
    else:
        dropped += 1

# Save
with open("public/prescriber_scores.json", "w") as f:
    json.dump(prescribers, f, separators=(",", ":"))

size_mb = os.path.getsize("public/prescriber_scores.json") / 1_000_000
total_real = exact_match + normalized_match
pct = total_real / len(prescribers) * 100

print(f"\n{'='*50}")
print(f"  Exact city match:    {exact_match:,}")
print(f"  Normalized match:    {normalized_match:,}")
print(f"  → Total real coords: {total_real:,} ({pct:.1f}%)")
print(f"  State fallback:      {state_fallback:,}")
print(f"  Dropped:             {dropped:,}")
print(f"  Output: public/prescriber_scores.json ({size_mb:.1f} MB)")
print(f"{'='*50}")
