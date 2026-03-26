"""
Backfill prescriber_scores.json with city-level coordinates.
Reads NPI -> city from Part D, then geocodes using uscities.csv (real coordinates).
"""
import json, csv, zipfile, random, os
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

state_center = defaultdict(lambda: {"lat_sum": 0, "lng_sum": 0, "count": 0})
for z in zip_data:
    s = state_center[z["state"]]
    s["lat_sum"] += z["lat"]
    s["lng_sum"] += z["lng"]
    s["count"] += 1

state_centroids = {}
for st, s in state_center.items():
    state_centroids[st] = (s["lat_sum"] / s["count"], s["lng_sum"] / s["count"])

print(f"  Built centroids for {len(state_centroids)} states")

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

    # Last-resort fallback: state centroid with wider jitter
    state = p.get("state", "")
    centroid = state_centroids.get(state)
    if centroid:
        p["lat"] = round(centroid[0] + random.gauss(0, 0.15), 4)
        p["lng"] = round(centroid[1] + random.gauss(0, 0.18), 4)
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
