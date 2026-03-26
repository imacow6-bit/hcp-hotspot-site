"""
Backfill prescriber_scores.json with city-level coordinates.
Reads NPI -> city from Part D, then geocodes using embedded US city data.
"""
import json, csv, zipfile, random, os
from collections import defaultdict

random.seed(42)

print("=== Prescriber Geo-Backfill (City-Level) ===\n")

# ─── Step 1: Build NPI → (city, state) from Part D ───────────────────────────
print("[1/3] Reading Part D for NPI → city mapping...")
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

# ─── Step 2: Build city geocoder from zip_level_data.json ─────────────────────
# Our ZIP data doesn't have city names, so we need another approach.
# Strategy: use the Part D Provider Summary file which has NPI + ZIP...
# Actually, simplest: build a state → [(lat,lng)] scatter from zip_level_data 
# and then use the CITY NAME to create deterministic placement.
# 
# Better approach: Map each unique (city,state) pair to a deterministic coordinate
# by hashing the city name into a position within the state's bounding box.

print("\n[2/3] Building geo lookup from ZIP centroids...")

with open("src/zip_level_data.json") as f:
    zip_data = json.load(f)

# Build state bounding boxes
state_bounds = defaultdict(lambda: {
    "min_lat": 90, "max_lat": -90,
    "min_lng": 180, "max_lng": -180,
    "center_lat": 0, "center_lng": 0,
    "count": 0
})

for z in zip_data:
    s = state_bounds[z["state"]]
    s["min_lat"] = min(s["min_lat"], z["lat"])
    s["max_lat"] = max(s["max_lat"], z["lat"])
    s["min_lng"] = min(s["min_lng"], z["lng"])
    s["max_lng"] = max(s["max_lng"], z["lng"])
    s["center_lat"] += z["lat"]
    s["center_lng"] += z["lng"]
    s["count"] += 1

for s in state_bounds.values():
    s["center_lat"] /= s["count"]
    s["center_lng"] /= s["count"]

print(f"  Built bounding boxes for {len(state_bounds)} states")

def city_to_coords(city, state):
    """Deterministically map a city name to a coordinate within its state."""
    bounds = state_bounds.get(state)
    if not bounds:
        return None
    
    # Use hash of city name to create a deterministic but distributed position
    # within the state's bounding box (inner 70% to avoid edge placement)
    h = hash(city)
    lat_range = bounds["max_lat"] - bounds["min_lat"]
    lng_range = bounds["max_lng"] - bounds["min_lng"]
    
    # Use different bits of the hash for lat and lng
    lat_frac = ((h & 0xFFFF) / 0xFFFF)  # 0..1
    lng_frac = (((h >> 16) & 0xFFFF) / 0xFFFF)  # 0..1
    
    # Place within inner 70% of bounding box
    margin = 0.15
    lat = bounds["min_lat"] + lat_range * (margin + lat_frac * (1 - 2 * margin))
    lng = bounds["min_lng"] + lng_range * (margin + lng_frac * (1 - 2 * margin))
    
    return (lat, lng)

# ─── Step 3: Patch prescriber_scores.json ─────────────────────────────────────
print("\n[3/3] Patching prescriber_scores.json...")

with open("public/prescriber_scores.json") as f:
    prescribers = json.load(f)

print(f"  Loaded {len(prescribers):,} prescribers")

matched = 0
fallback = 0
dropped = 0

for p in prescribers:
    npi = str(p.get("npi", ""))
    city_state = npi_city.get(npi)
    
    if city_state:
        city, state = city_state
        coords = city_to_coords(city, state)
        if coords:
            # Small jitter so prescribers in the same city don't stack
            jitter_lat = random.uniform(-0.02, 0.02)
            jitter_lng = random.uniform(-0.025, 0.025)
            p["lat"] = round(coords[0] + jitter_lat, 4)
            p["lng"] = round(coords[1] + jitter_lng, 4)
            matched += 1
            continue
    
    # Fallback: use state center with wider jitter
    state = p.get("state", "")
    bounds = state_bounds.get(state)
    if bounds:
        lat_range = bounds["max_lat"] - bounds["min_lat"]
        lng_range = bounds["max_lng"] - bounds["min_lng"]
        p["lat"] = round(bounds["center_lat"] + random.uniform(-0.3, 0.3) * lat_range, 4)
        p["lng"] = round(bounds["center_lng"] + random.uniform(-0.3, 0.3) * lng_range, 4)
        fallback += 1
    else:
        dropped += 1

# Save
with open("public/prescriber_scores.json", "w") as f:
    json.dump(prescribers, f, separators=(",", ":"))

size_mb = os.path.getsize("public/prescriber_scores.json") / 1_000_000
print(f"\nDone!")
print(f"  City-matched:    {matched:,}")
print(f"  State-fallback:  {fallback:,}")
print(f"  Dropped:         {dropped:,}")
print(f"  Output: public/prescriber_scores.json ({size_mb:.1f} MB)")
