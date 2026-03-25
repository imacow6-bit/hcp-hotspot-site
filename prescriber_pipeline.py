"""
Prescriber Scoring Pipeline
============================
Combines Medicare Part D prescribing volume with Open Payments competitor
engagement data to produce a scored prescriber JSON for the HCP Hotspot Map.

Outputs: public/prescriber_scores.json
  One record per Tier 1 or Tier 2 prescriber with:
    - lat/lng (from ZIP centroid)
    - specialty, tier, total claims
    - competitor_engaged flag + company names

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATA SOURCES — download before running
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Medicare Part D Prescribers — by Provider and Drug (2022)
   URL: https://data.cms.gov/provider-summary-by-type-of-service/
        medicare-part-d-prescribers/medicare-part-d-prescribers-by-provider-and-drug
   Click "Download" → CSV. File is ~2-4 GB. Rename to partd_by_drug.csv.
   Why this file (not "by Provider"): it has Prscrbr_Zip5 for lat/lng join.

2. Open Payments — General Payments (most recent year)
   URL: https://openpaymentsdata.cms.gov/datasets/general-payment-data-with-
        deleted-records-publication-year-2023-data-from-the-open-payments-program
   Click "Export" → CSV. File is ~2-5 GB. Rename to open_payments.csv.
   We only read 3 columns so memory is manageable despite the file size.

3. ZIP centroids — auto-loaded from src/zip_level_data.json (already present).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usage:
  pip install pandas
  python prescriber_pipeline.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import math
import pandas as pd
from collections import defaultdict

# ─────────────────────────────────────────────────────────
# CONFIG — update paths if files are elsewhere
# ─────────────────────────────────────────────────────────

PART_D_PATH       = "partd_by_drug.csv"
OPEN_PAYMENTS_PATH = "open_payments.csv"
ZIP_DATA_PATH     = "src/zip_level_data.json"
OUTPUT_PATH       = "public/prescriber_scores.json"

CHUNK_SIZE = 100_000

# Tier thresholds (percentile within specialty)
# Top 25% = Tier 1 (gold — untouched priority targets)
# 25–75%  = Tier 2 (blue — worth inviting)
# Bottom 25% omitted from output (too low volume)
TIER1_PERCENTILE = 0.75   # top 25%  → Tier 1
TIER2_PERCENTILE = 0.25   # top 75%  → Tier 2  (Tier 2 = between these two)

# ─────────────────────────────────────────────────────────
# SPECIALTY MAPPING
# Part D uses different names than NPPES taxonomy.
# Map all relevant Prscrbr_Type values → our 6 specialties.
# ─────────────────────────────────────────────────────────

PART_D_SPECIALTY_MAP = {
    # Cardiology
    "Cardiology":                                       "Cardiology",
    "Interventional Cardiology":                        "Cardiology",
    "Clinical Cardiac Electrophysiology":               "Cardiology",
    "Advanced Heart Failure and Transplant Cardiology": "Cardiology",
    "Cardiac Surgery":                                  "Cardiology",

    # Oncology
    "Hematology/Oncology":                              "Oncology",
    "Medical Oncology":                                 "Oncology",
    "Gynecological/Oncology":                           "Oncology",
    "Radiation Oncology":                               "Oncology",
    "Surgical Oncology":                                "Oncology",
    "Hematology":                                       "Oncology",

    # Neurology
    "Neurology":                                        "Neurology",
    "Neuropsychiatry":                                  "Neurology",
    "Neuromuscular Medicine":                           "Neurology",
    "Neurological Surgery":                             "Neurology",

    # Endocrinology
    "Endocrinology":                                    "Endocrinology",

    # Pulmonology
    "Pulmonary Disease":                                "Pulmonology",
    "Pulmonary/Critical Care Medicine":                 "Pulmonology",
    "Pulmonary Disease/Critical Care Medicine":         "Pulmonology",
    "Critical Care (Intensivists)":                     "Pulmonology",

    # Orthopedics
    "Orthopedic Surgery":                               "Orthopedics",
    "Sports Medicine":                                  "Orthopedics",
    "Physical Medicine and Rehabilitation":             "Orthopedics",
}

# Part D columns we actually need (keeps memory ~80% lower)
PART_D_COLS = [
    "Prscrbr_NPI",
    "Prscrbr_Last_Org_Name",
    "Prscrbr_First_Name",
    "Prscrbr_Type",
    "Prscrbr_State_Abrvtn",
    "Prscrbr_Zip5",
    "Tot_Clms",
]

# Open Payments columns we need
OP_COLS = [
    "Covered_Recipient_NPI",
    "Covered_Recipient_Type",
    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
]


# ─────────────────────────────────────────────────────────
# STEP 1 — ZIP centroid lookup
# ─────────────────────────────────────────────────────────

def load_zip_centroids(path):
    """Build { zip5: {lat, lng, state} } from existing zip_level_data.json."""
    with open(path) as f:
        data = json.load(f)
    centroids = {}
    for entry in data:
        centroids[entry["zip"]] = {
            "lat": entry["lat"],
            "lng": entry["lng"],
            "state": entry["state"],
        }
    print(f"  Loaded {len(centroids):,} ZIP centroids from {path}")
    return centroids


# ─────────────────────────────────────────────────────────
# STEP 2 — Process Part D: aggregate claims by NPI
# ─────────────────────────────────────────────────────────

def process_part_d(path, zip_centroids):
    """
    Stream Part D by Provider and Drug CSV.
    Aggregate total claims per NPI across all drugs (bridge table matching
    can be added here later for campaign-specific filtering).

    Returns dict: { npi: {specialty, tot_clms, zip, state, lat, lng, name} }
    """
    npi_data = {}   # npi → aggregated record
    skipped_specialty = 0
    skipped_zip = 0
    total_rows = 0

    reader = pd.read_csv(
        path,
        usecols=PART_D_COLS,
        dtype=str,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    )

    for chunk_num, chunk in enumerate(reader):
        total_rows += len(chunk)

        for _, row in chunk.iterrows():
            prscrbr_type = str(row.get("Prscrbr_Type", "")).strip()
            specialty = PART_D_SPECIALTY_MAP.get(prscrbr_type)
            if not specialty:
                skipped_specialty += 1
                continue

            npi = str(row.get("Prscrbr_NPI", "")).strip()
            if not npi or npi == "nan":
                continue

            # Parse claim count (may be suppressed "<11" for small values)
            raw_clms = str(row.get("Tot_Clms", "0")).strip()
            try:
                clms = int(float(raw_clms))
            except ValueError:
                clms = 5   # suppressed value — treat as low volume

            # ZIP → lat/lng
            raw_zip = str(row.get("Prscrbr_Zip5", "")).strip()[:5].zfill(5)
            geo = zip_centroids.get(raw_zip)
            if not geo:
                skipped_zip += 1
                # Still track the NPI — we'll drop it in the output phase
                geo = None

            if npi not in npi_data:
                last  = str(row.get("Prscrbr_Last_Org_Name", "")).strip()
                first = str(row.get("Prscrbr_First_Name",    "")).strip()
                npi_data[npi] = {
                    "specialty": specialty,
                    "tot_clms":  clms,
                    "zip":       raw_zip,
                    "state":     str(row.get("Prscrbr_State_Abrvtn", "")).strip(),
                    "name":      f"{last}, {first}" if first else last,
                    "lat":       geo["lat"] if geo else None,
                    "lng":       geo["lng"] if geo else None,
                }
            else:
                # Accumulate claims across multiple drug rows for this NPI
                npi_data[npi]["tot_clms"] += clms

        if (chunk_num + 1) % 10 == 0:
            print(f"  [Part D] ... {total_rows:,} rows | "
                  f"{len(npi_data):,} unique NPIs matched")

    print(f"  [Part D] Done: {total_rows:,} rows | "
          f"{len(npi_data):,} NPIs | "
          f"{skipped_specialty:,} skipped (specialty) | "
          f"{skipped_zip:,} skipped (ZIP not found)")
    return npi_data


# ─────────────────────────────────────────────────────────
# STEP 3 — Compute prescriber tiers within each specialty
# ─────────────────────────────────────────────────────────

def compute_tiers(npi_data):
    """
    Rank prescribers within their specialty by total claims.
    Tier 1 = top 25%   (gold — White Space priority)
    Tier 2 = 25–75%    (blue  — solid targets)
    Tier 3 = bottom 25% (omitted from output)

    Adds 'tier' key to each record in-place.
    Returns npi_data with tiers set.
    """
    # Group NPIs by specialty
    by_specialty = defaultdict(list)
    for npi, rec in npi_data.items():
        by_specialty[rec["specialty"]].append((npi, rec["tot_clms"]))

    for specialty, npis in by_specialty.items():
        npis.sort(key=lambda x: x[1], reverse=True)  # highest claims first
        n = len(npis)
        t1_cutoff = math.ceil(n * TIER1_PERCENTILE)  # top 25% boundary index
        t2_cutoff = math.ceil(n * TIER2_PERCENTILE)  # bottom 25% boundary index

        for rank, (npi, _) in enumerate(npis):
            if rank < (n - t1_cutoff):
                tier = 1
            elif rank < (n - t2_cutoff):
                tier = 2
            else:
                tier = 3
            npi_data[npi]["tier"] = tier

        t1 = n - t1_cutoff
        t2 = t1_cutoff - t2_cutoff
        print(f"  {specialty:20s}: {n:6,} prescribers | "
              f"Tier 1: {t1:,} | Tier 2: {t2:,}")

    return npi_data


# ─────────────────────────────────────────────────────────
# STEP 4 — Process Open Payments: competitor engagement flag
# ─────────────────────────────────────────────────────────

def process_open_payments(path, target_npis):
    """
    Stream Open Payments general payments CSV.
    Only process records where:
      - Covered_Recipient_Type = "Covered Recipient Physician"
      - Covered_Recipient_NPI is in our target NPI set

    Returns dict: { npi: { competitor_engaged: True, companies: [list] } }
    """
    engagement = {}   # npi → { companies: set }
    matched = 0
    total_rows = 0

    reader = pd.read_csv(
        path,
        usecols=OP_COLS,
        dtype=str,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    )

    for chunk_num, chunk in enumerate(reader):
        total_rows += len(chunk)

        # Only physician recipients
        chunk = chunk[
            chunk["Covered_Recipient_Type"].str.strip()
            == "Covered Recipient Physician"
        ]

        for _, row in chunk.iterrows():
            npi = str(row.get("Covered_Recipient_NPI", "")).strip()
            if npi not in target_npis:
                continue

            company = str(
                row.get(
                    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
                    ""
                )
            ).strip()

            if npi not in engagement:
                engagement[npi] = {"companies": set()}
            if company and company != "nan":
                engagement[npi]["companies"].add(company)
            matched += 1

        if (chunk_num + 1) % 20 == 0:
            print(f"  [Open Payments] ... {total_rows:,} rows | "
                  f"{len(engagement):,} target NPIs flagged")

    print(f"  [Open Payments] Done: {total_rows:,} rows | "
          f"{len(engagement):,} NPIs flagged as competitor-engaged")
    return engagement


# ─────────────────────────────────────────────────────────
# STEP 5 — Build output JSON
# ─────────────────────────────────────────────────────────

def build_output(npi_data, engagement):
    """
    Merge prescriber tiers with competitor engagement.
    Only output Tier 1 and Tier 2 prescribers with valid lat/lng.

    Output shape per record:
    {
      "npi":                "1234567890",
      "name":               "Smith, John",
      "lat":                41.8953,
      "lng":                -87.6189,
      "state":              "IL",
      "specialty":          "Cardiology",
      "tier":               1,
      "tot_clms":           4250,
      "competitor_engaged": true,
      "companies":          ["AstraZeneca", "Pfizer"]
    }
    """
    output = []
    skipped_tier3 = 0
    skipped_no_geo = 0

    for npi, rec in npi_data.items():
        tier = rec.get("tier", 3)

        if tier == 3:
            skipped_tier3 += 1
            continue

        if rec["lat"] is None or rec["lng"] is None:
            skipped_no_geo += 1
            continue

        op = engagement.get(npi, {})
        companies = sorted(op.get("companies", set()))[:5]  # cap at 5 names

        output.append({
            "npi":                npi,
            "name":               rec["name"],
            "lat":                rec["lat"],
            "lng":                rec["lng"],
            "state":              rec["state"],
            "specialty":          rec["specialty"],
            "tier":               tier,
            "tot_clms":           rec["tot_clms"],
            "competitor_engaged": npi in engagement,
            "companies":          companies,
        })

    # Sort: Tier 1 first, then by claim volume descending
    output.sort(key=lambda x: (x["tier"], -x["tot_clms"]))

    print(f"\n  Output records : {len(output):,}")
    print(f"  Tier 3 omitted : {skipped_tier3:,}")
    print(f"  No geo omitted : {skipped_no_geo:,}")

    # Summary by specialty + tier
    from collections import Counter
    counts = Counter((r["specialty"], r["tier"]) for r in output)
    print("\n  Breakdown by specialty / tier:")
    for spec in sorted(set(r["specialty"] for r in output)):
        t1 = counts.get((spec, 1), 0)
        t2 = counts.get((spec, 2), 0)
        ce = sum(1 for r in output
                 if r["specialty"] == spec and r["competitor_engaged"])
        ws = sum(1 for r in output
                 if r["specialty"] == spec and not r["competitor_engaged"]
                 and r["tier"] == 1)
        print(f"    {spec:20s}  T1:{t1:5,}  T2:{t2:5,}  "
              f"Competitor-engaged:{ce:5,}  White Space:{ws:5,}")

    return output


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("Prescriber Scoring Pipeline")
    print("Part D Volume Signal  +  Open Payments Competitor Signal")
    print("=" * 65)

    # Verify input files
    missing = []
    for label, path in [
        ("Part D by Provider and Drug", PART_D_PATH),
        ("Open Payments General Payments", OPEN_PAYMENTS_PATH),
        ("ZIP centroid data", ZIP_DATA_PATH),
    ]:
        if not os.path.exists(path):
            missing.append((label, path))

    if missing:
        print("\n  Missing files — download before running:\n")
        for label, path in missing:
            print(f"  ✗  {label}")
            print(f"     Expected at: {path}")
        if any(p == PART_D_PATH for _, p in missing):
            print(f"\n  Part D download:")
            print(f"  https://data.cms.gov/provider-summary-by-type-of-service/"
                  f"medicare-part-d-prescribers/"
                  f"medicare-part-d-prescribers-by-provider-and-drug")
        if any(p == OPEN_PAYMENTS_PATH for _, p in missing):
            print(f"\n  Open Payments download:")
            print(f"  https://openpaymentsdata.cms.gov/datasets/"
                  f"general-payment-data-with-deleted-records-"
                  f"publication-year-2023-data-from-the-open-payments-program")
        return

    print("\n[1/5] Loading ZIP centroids...")
    zip_centroids = load_zip_centroids(ZIP_DATA_PATH)

    print("\n[2/5] Processing Part D prescribing data...")
    npi_data = process_part_d(PART_D_PATH, zip_centroids)

    print("\n[3/5] Computing prescriber tiers within each specialty...")
    npi_data = compute_tiers(npi_data)

    # Only pass Tier 1+2 NPIs to the Open Payments step
    target_npis = {
        npi for npi, rec in npi_data.items()
        if rec.get("tier", 3) <= 2
    }
    print(f"\n  {len(target_npis):,} Tier 1+2 NPIs passed to Open Payments step")

    print("\n[4/5] Processing Open Payments competitor engagement...")
    engagement = process_open_payments(OPEN_PAYMENTS_PATH, target_npis)

    print("\n[5/5] Building output JSON...")
    output = build_output(npi_data, engagement)

    os.makedirs("public", exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_mb = os.path.getsize(OUTPUT_PATH) / 1_000_000
    print(f"\n{'=' * 65}")
    print(f"Output: {OUTPUT_PATH}  ({size_mb:.1f} MB)")
    print(f"Records: {len(output):,} prescribers (Tier 1 + Tier 2)")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
