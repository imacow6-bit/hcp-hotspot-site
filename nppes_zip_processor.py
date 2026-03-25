"""
NPPES ZIP-Level HCP Processor (v2 — All ZIPs, No Metro Filtering)
==================================================================
Reads the raw NPPES CSV + Practice Location Reference file, extracts
PRACTICE LOCATION ZIPs (NOT mailing/PO box), maps taxonomy → specialty,
and outputs a flat JSON of every US ZIP with at least 1 provider in our
6 specialties.

The output is a single flat array — no metro grouping. The frontend
decides what subset to analyze (city radius, state, freeform region).

Two NPPES files are processed:
  1. Main file  → primary practice location ZIP
  2. Practice Location Reference file → secondary practice location ZIPs
  A doctor who practices in 3 ZIPs counts in all 3.

Usage:
  1. Extract NPPES_Data_Dissemination_March_2026_V2.zip somewhere
  2. Download uszips.csv from https://simplemaps.com/data/us-zips
  3. Update the 3 paths in CONFIG below
  4. python nppes_zip_processor.py

Requirements:
  pip install pandas
"""

import os
import json
import math
import pandas as pd
from collections import defaultdict

# ──────────────────────────────────────────────────
# CONFIG — UPDATE THESE 3 PATHS
# ──────────────────────────────────────────────────

# 1. Main NPPES CSV (the big ~11GB file)
#    Inside the extracted folder, it's named something like:
#    npidata_pfile_20050523-20260309.csv
MAIN_NPPES_PATH = r"C:\Users\aseem\OneDrive\Desktop\NPPES_Data_Dissemination_March_2026_V2\npidata_pfile_20050523-20260309.csv"

# 2. Practice Location Reference file (secondary practice locations)
#    Inside the same extracted folder, named something like:
#    pl_pfile_20050523-20260309.csv
PRACTICE_LOCATION_PATH = r"C:\Users\aseem\OneDrive\Desktop\NPPES_Data_Dissemination_March_2026_V2\pl_pfile_20050523-20260309.csv"

# 3. ZIP centroid lookup (free from https://simplemaps.com/data/us-zips)
#    Needs columns: zip, lat, lng, state_id
ZIP_CENTROID_PATH = r"C:\Users\aseem\OneDrive\Desktop\hcp hotspot site\uszips.csv"

# Output — goes into the hotspot site's src folder for the frontend
OUTPUT_PATH = r"C:\Users\aseem\OneDrive\Desktop\hcp hotspot site\src\zip_level_data.json"

# Chunk size for streaming the big CSV
CHUNK_SIZE = 100_000

# ──────────────────────────────────────────────────
# COLUMNS TO READ (keeps memory low)
# ──────────────────────────────────────────────────

# Main file — we read the PRACTICE LOCATION zip, NOT the mailing address
MAIN_COLS = [
    "NPI",
    "Entity Type Code",
    "Provider Business Practice Location Address Postal Code",
    "Healthcare Provider Taxonomy Code_1",
    "NPI Deactivation Date",
    "Healthcare Provider Primary Taxonomy Switch_1",
]

# Practice Location Reference file — secondary practice ZIPs
# This file has NPI + secondary practice address columns
PL_COLS = [
    "NPI",
    "Provider Secondary Practice Location Address - Postal Code",
]

# ──────────────────────────────────────────────────
# TAXONOMY → SPECIALTY MAPPING
# ──────────────────────────────────────────────────

TAXONOMY_TO_SPECIALTY = {
    # Oncology
    "207RH0003X": "Oncology",
    "207VX0000X": "Oncology",
    "2086S0120X": "Oncology",
    "2086S0105X": "Oncology",
    "207RX0202X": "Oncology",

    # Cardiology
    "207RC0000X": "Cardiology",
    "207RI0011X": "Cardiology",
    "207RC0001X": "Cardiology",
    "207RC0200X": "Cardiology",

    # Orthopedics
    "207X00000X": "Orthopedics",
    "207XS0114X": "Orthopedics",
    "207XX0004X": "Orthopedics",
    "207XS0106X": "Orthopedics",
    "207XX0801X": "Orthopedics",
    "207XP3100X": "Orthopedics",

    # Neurology
    "2084N0400X": "Neurology",
    "2084N0402X": "Neurology",
    "2084P0800X": "Neurology",
    "2084P0802X": "Neurology",
    "2084P0804X": "Neurology",
    "2084B0040X": "Neurology",

    # Endocrinology
    "207RE0101X": "Endocrinology",
    "2080P0210X": "Endocrinology",

    # Pulmonology
    "207RP1001X": "Pulmonology",
    "2080P0205X": "Pulmonology",
    "207RT0003X": "Pulmonology",
}


# ──────────────────────────────────────────────────
# STEP 1: Load ZIP centroid lookup
# ──────────────────────────────────────────────────

def load_zip_centroids(path):
    """
    Load ZIP → (lat, lng, state) from the simplemaps CSV.
    Returns dict: { "60611": { "lat": 41.89, "lng": -87.62, "state": "IL" }, ... }
    """
    df = pd.read_csv(path, dtype={"zip": str})
    centroids = {}
    for _, row in df.iterrows():
        z = str(row["zip"]).zfill(5)[:5]
        centroids[z] = {
            "lat": round(float(row["lat"]), 4),
            "lng": round(float(row["lng"]), 4),
            "state": str(row["state_id"]),
        }
    print(f"  Loaded {len(centroids):,} ZIP centroids")
    return centroids


# ──────────────────────────────────────────────────
# STEP 2: Process MAIN NPPES file → (NPI → specialty) + (ZIP → specialty counts)
# ──────────────────────────────────────────────────

def process_main_file(path):
    """
    Stream the main NPPES CSV. For each row:
      1. Filter: Entity Type 1, active, primary taxonomy = Y
      2. Map taxonomy → specialty (skip if not one of our 6)
      3. Extract 5-digit PRACTICE LOCATION zip (not mailing address)

    Returns:
      - npi_specialty: dict { NPI: specialty } for joining with secondary locations
      - zip_counts: dict { zip: { specialty: count } } accumulated counts
    """
    npi_specialty = {}  # NPI → specialty (for joining with practice location file)
    zip_counts = defaultdict(lambda: defaultdict(int))
    total_matched = 0
    total_rows = 0

    reader = pd.read_csv(
        path,
        usecols=MAIN_COLS,
        dtype=str,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    )

    for chunk_num, chunk in enumerate(reader):
        total_rows += len(chunk)

        # Filters
        chunk = chunk[chunk["Entity Type Code"] == "1"]           # Individual only
        chunk = chunk[chunk["NPI Deactivation Date"].isna()]      # Active only
        chunk = chunk[chunk["Healthcare Provider Primary Taxonomy Switch_1"] == "Y"]

        for _, row in chunk.iterrows():
            taxonomy = str(row["Healthcare Provider Taxonomy Code_1"]).strip()
            specialty = TAXONOMY_TO_SPECIALTY.get(taxonomy)
            if not specialty:
                continue

            npi = str(row["NPI"]).strip()
            npi_specialty[npi] = specialty

            # PRACTICE LOCATION zip — NOT mailing address
            raw_zip = str(row[
                "Provider Business Practice Location Address Postal Code"
            ]).strip()
            zip5 = raw_zip[:5].zfill(5)

            if len(zip5) == 5 and zip5.isdigit():
                zip_counts[zip5][specialty] += 1
                total_matched += 1

        if (chunk_num + 1) % 10 == 0:
            print(f"  [Main] ... {total_rows:,} rows, {total_matched:,} matched")

    print(f"  [Main] Done: {total_rows:,} rows, {total_matched:,} matched, "
          f"{len(npi_specialty):,} unique NPIs with specialty")
    return npi_specialty, zip_counts


# ──────────────────────────────────────────────────
# STEP 3: Process PRACTICE LOCATION REFERENCE file → secondary ZIPs
# ──────────────────────────────────────────────────

def process_secondary_locations(path, npi_specialty, zip_counts):
    """
    Stream the Practice Location Reference file.
    For each row, look up the NPI's specialty from the main file,
    then count the secondary practice ZIP under that specialty.

    This catches doctors who practice at multiple locations —
    e.g., a cardiologist with offices in both downtown and the suburbs.
    """
    secondary_matched = 0
    total_rows = 0

    reader = pd.read_csv(
        path,
        usecols=PL_COLS,
        dtype=str,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    )

    for chunk_num, chunk in enumerate(reader):
        total_rows += len(chunk)

        for _, row in chunk.iterrows():
            npi = str(row["NPI"]).strip()
            specialty = npi_specialty.get(npi)
            if not specialty:
                continue  # NPI not in our specialty set

            raw_zip = str(row[
                "Provider Secondary Practice Location Address - Postal Code"
            ]).strip()
            zip5 = raw_zip[:5].zfill(5)

            if len(zip5) == 5 and zip5.isdigit():
                zip_counts[zip5][specialty] += 1
                secondary_matched += 1

        if (chunk_num + 1) % 10 == 0:
            print(f"  [Secondary] ... {total_rows:,} rows, {secondary_matched:,} matched")

    print(f"  [Secondary] Done: {total_rows:,} rows, {secondary_matched:,} additional locations")
    return zip_counts


# ──────────────────────────────────────────────────
# STEP 4: Build the output JSON
# ──────────────────────────────────────────────────

def build_output(zip_counts, zip_centroids):
    """
    Merge ZIP counts with centroid coordinates.
    Output: flat array of every ZIP with at least 1 provider.

    Shape:
    [
      {
        "zip": "60611",
        "lat": 41.8953,
        "lng": -87.6189,
        "state": "IL",
        "docs": { "Oncology": 45, "Cardiology": 62, ... }
      },
      ...
    ]

    Each entry has the same {lat, lng, docs} shape as existing metro data,
    so weightedCentroid() works on it without modification.
    """
    ALL_SPECIALTIES = [
        "Oncology", "Cardiology", "Orthopedics",
        "Neurology", "Endocrinology", "Pulmonology"
    ]

    output = []
    skipped = 0

    for zip_code in sorted(zip_counts.keys()):
        geo = zip_centroids.get(zip_code)
        if not geo:
            skipped += 1
            continue  # ZIP not in centroid lookup (rare edge cases)

        docs = dict(zip_counts[zip_code])
        # Ensure all 6 specialties present (0 if missing)
        for spec in ALL_SPECIALTIES:
            docs.setdefault(spec, 0)

        output.append({
            "zip": zip_code,
            "lat": geo["lat"],
            "lng": geo["lng"],
            "state": geo["state"],
            "docs": docs,
        })

    if skipped > 0:
        print(f"  Skipped {skipped} ZIPs not found in centroid lookup")

    return output


# ──────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("NPPES ZIP-Level HCP Processor v2")
    print("All ZIPs · No Metro Filtering · Primary + Secondary Locations")
    print("=" * 60)

    # Verify files exist before starting the long process
    for label, path in [("Main NPPES", MAIN_NPPES_PATH),
                        ("Practice Location", PRACTICE_LOCATION_PATH),
                        ("ZIP Centroids", ZIP_CENTROID_PATH)]:
        if not os.path.exists(path):
            print(f"\n  ERROR: {label} file not found at:\n  {path}")
            print(f"  Update the path in CONFIG section and re-run.")
            return

    print("\n[1/4] Loading ZIP centroid lookup...")
    zip_centroids = load_zip_centroids(ZIP_CENTROID_PATH)

    print("\n[2/4] Processing main NPPES file (primary practice locations)...")
    npi_specialty, zip_counts = process_main_file(MAIN_NPPES_PATH)

    print("\n[3/4] Processing Practice Location Reference file (secondary locations)...")
    zip_counts = process_secondary_locations(PRACTICE_LOCATION_PATH, npi_specialty, zip_counts)

    print("\n[4/4] Building output JSON...")
    output = build_output(zip_counts, zip_centroids)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f)  # No indent — keeps file smaller (~2MB vs ~5MB)

    # Summary
    total_docs = sum(sum(z["docs"].values()) for z in output)
    states = set(z["state"] for z in output)
    print(f"\n{'=' * 60}")
    print(f"Output: {OUTPUT_PATH}")
    print(f"ZIP codes with providers: {len(output):,}")
    print(f"States covered: {len(states)}")
    print(f"Total provider-location pairs: {total_docs:,}")
    print(f"{'=' * 60}")
    print(f"\nNote: provider-location pairs > unique providers because")
    print(f"doctors with multiple practice locations are counted at each ZIP.")


if __name__ == "__main__":
    main()
