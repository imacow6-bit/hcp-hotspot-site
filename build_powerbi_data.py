"""
build_powerbi_data.py
=====================
Reads the three raw CMS zip files and produces 5 clean CSVs for Power BI.

Usage:
    python build_powerbi_data.py

Edit the three paths at the top of the CONFIG section to match your machine.
All output goes to an 'exports/' folder next to this script.

Output files:
    exports/physicians.csv          — individual prescribers (Entity Type 1)
    exports/organizations.csv       — hospitals / health systems (Entity Type 2)
    exports/payments.csv            — Open Payments detail (per NPI × company × type)
    exports/prescriber_drugs.csv    — Part D drug-level prescribing (per NPI × drug)
    exports/drug_reference.csv      — unique drug → specialty lookup table
"""

import csv
import io
import json
import os
import zipfile
from collections import defaultdict

# =============================================================================
# CONFIG — edit these three paths
# =============================================================================
NPPES_ZIP     = r"C:\Users\aseem\Downloads\NPPES data.zip"
PAYMENTS_ZIP  = r"C:\Users\aseem\Downloads\open_payments.csv.zip"
PARTD_ZIP     = r"C:\Users\aseem\Downloads\Medicare Part D Prescribers - by Provider and Drug.zip"

EXPORTS_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
CHUNK_SIZE    = 100_000   # rows to read at once — reduces memory usage
# =============================================================================


# ---------------------------------------------------------------------------
# Specialty taxonomy mappings (individuals — Entity Type 1)
# ---------------------------------------------------------------------------
INDIVIDUAL_TAXONOMY = {
    # Oncology
    "207RH0003X": "Oncology", "207VX0000X": "Oncology",
    "2086S0120X": "Oncology", "2086S0105X": "Oncology",
    "207RX0202X": "Oncology",
    # Cardiology
    "207RC0000X": "Cardiology", "207RI0011X": "Cardiology",
    "207RC0001X": "Cardiology", "207RC0200X": "Cardiology",
    # Orthopedics
    "207X00000X": "Orthopedics", "207XS0114X": "Orthopedics",
    "207XX0004X": "Orthopedics", "207XS0106X": "Orthopedics",
    "207XX0801X": "Orthopedics", "207XP3100X": "Orthopedics",
    # Neurology
    "2084N0400X": "Neurology", "2084N0402X": "Neurology",
    "2084P0800X": "Neurology", "2084P0802X": "Neurology",
    "2084P0804X": "Neurology", "2084B0040X": "Neurology",
    # Endocrinology
    "207RE0101X": "Endocrinology", "2080P0210X": "Endocrinology",
    # Pulmonology
    "207RP1001X": "Pulmonology", "2080P0205X": "Pulmonology",
    "207RT0003X": "Pulmonology",
}

# Organization taxonomy codes that identify hospitals/health systems/cancer centers
ORG_TAXONOMY = {
    "282N00000X": "General Acute Care Hospital",
    "282NC0060X": "Critical Access Hospital",
    "282NC2000X": "Children's Hospital",
    "282NR1301X": "Rural Hospital",
    "282NW0100X": "Women's Hospital",
    "283Q00000X": "Psychiatric Hospital",
    "283X00000X": "Rehabilitation Hospital",
    "286500000X": "Military Hospital",
    "2865M2000X": "Military General Hospital",
    "287300000X": "Christian Science Sanitorium",
    "291900000X": "Military Clinical Medical Laboratory",
    "291U00000X": "Clinical Medical Laboratory",
    "292200000X": "Dental Laboratory",
    "293D00000X": "Physiological Laboratory",
    "261Q00000X": "Clinic/Center",
    "261QC0050X": "Critical Access Hospital Clinic",
    "261QM1300X": "Multi-Specialty Clinic",
    "261QX0203X": "Cancer Clinic",
    "261QX0200X": "Oncology Clinic",
    "261QC1800X": "Cancer Treatment Center",
    "273100000X": "Epilepsy Unit",
    "275N00000X": "Medicare Defined Swing Bed Unit",
    "276400000X": "Rehabilitation Unit",
    "273R00000X": "Psychiatric Unit",
    "276400000X": "Rehabilitation Unit",
    "353BL0002X": "Lactation Consultant",
    "3416L0300X": "Lithotripsy",
    "3416A0800X": "Ambulatory Surgical",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_csv_in_zip(zf, exclude_keywords=("FileHeader", "othername", "endpoint")):
    """Return the name of the largest CSV in the zip (skipping header/ref files)."""
    candidates = []
    for name in zf.namelist():
        if not name.lower().endswith(".csv"):
            continue
        if any(kw.lower() in name.lower() for kw in exclude_keywords):
            continue
        candidates.append((zf.getinfo(name).file_size, name))
    if not candidates:
        raise FileNotFoundError(f"No suitable CSV found in zip: {zf.filename}")
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_pl_csv_in_zip(zf):
    """Return the practice location reference file name."""
    for name in zf.namelist():
        if "pl_pfile" in name.lower() and name.lower().endswith(".csv"):
            return name
    return None


def stream_csv_chunks(zf, csv_name, usecols=None):
    """Yield DataFrames of CHUNK_SIZE rows from a CSV inside a zip."""
    import pandas as pd
    with zf.open(csv_name) as raw:
        for chunk in pd.read_csv(
            raw,
            chunksize=CHUNK_SIZE,
            dtype=str,
            usecols=usecols,
            on_bad_lines="skip",
            low_memory=False,
            encoding="utf-8",
            encoding_errors="replace",
        ):
            yield chunk


def load_zip_centroids():
    """Load ZIP → (lat, lng, state) from the existing zip_level_data.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "zip_level_data.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {str(r["zip"]).zfill(5): (r["lat"], r["lng"], r.get("state", "")) for r in data}


# ---------------------------------------------------------------------------
# 1. NPPES — physicians (Entity Type 1)
# ---------------------------------------------------------------------------

def export_physicians(zip_path, zip_centroids):
    """Extract individual providers from NPPES and write physicians.csv."""
    print("\n[1/5] Processing NPPES → physicians.csv ...")
    out_path = os.path.join(EXPORTS_DIR, "physicians.csv")

    COLS = [
        "NPI",
        "Entity Type Code",
        "Provider Last Name (Legal Name)",
        "Provider First Name",
        "Provider Credential Text",
        "Provider Sex Code",
        "Provider Business Practice Location Address City Name",
        "Provider Business Practice Location Address State Name",
        "Provider Business Practice Location Address Postal Code",
        "NPI Deactivation Date",
        "Healthcare Provider Taxonomy Code_1",
        "Healthcare Provider Primary Taxonomy Switch_1",
    ]

    rows_written = 0
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = find_csv_in_zip(zf)
        print(f"    Reading: {csv_name}")

        with open(out_path, "w", newline="", encoding="utf-8") as fout:
            writer = None

            for chunk in stream_csv_chunks(zf, csv_name, usecols=COLS):
                # Filter: individuals, active, primary taxonomy in our specialty set
                mask = (
                    (chunk["Entity Type Code"] == "1")
                    & chunk["NPI Deactivation Date"].isna()
                    & (chunk["Healthcare Provider Primary Taxonomy Switch_1"] == "Y")
                    & chunk["Healthcare Provider Taxonomy Code_1"].isin(INDIVIDUAL_TAXONOMY)
                )
                chunk = chunk[mask].copy()
                if chunk.empty:
                    continue

                chunk["specialty"] = chunk["Healthcare Provider Taxonomy Code_1"].map(INDIVIDUAL_TAXONOMY)
                chunk["zip5"] = chunk["Provider Business Practice Location Address Postal Code"].str[:5].str.zfill(5)

                # Add lat/lng from ZIP lookup
                chunk["lat"] = chunk["zip5"].map(lambda z: zip_centroids.get(z, (None, None, ""))[0])
                chunk["lng"] = chunk["zip5"].map(lambda z: zip_centroids.get(z, (None, None, ""))[1])

                out = chunk.rename(columns={
                    "NPI": "npi",
                    "Provider Last Name (Legal Name)": "last_name",
                    "Provider First Name": "first_name",
                    "Provider Credential Text": "credential",
                    "Provider Sex Code": "sex",
                    "Provider Business Practice Location Address City Name": "city",
                    "Provider Business Practice Location Address State Name": "state",
                })[["npi", "last_name", "first_name", "credential", "sex",
                     "city", "state", "zip5", "specialty", "lat", "lng"]]

                if writer is None:
                    writer = csv.DictWriter(fout, fieldnames=out.columns.tolist())
                    writer.writeheader()

                out.to_csv(fout, index=False, header=False)
                rows_written += len(out)

                if rows_written % 500_000 == 0:
                    print(f"    {rows_written:,} rows written...")

    print(f"    physicians.csv: {rows_written:,} rows")
    return rows_written


# ---------------------------------------------------------------------------
# 2. NPPES — organizations (Entity Type 2)
# ---------------------------------------------------------------------------

def export_organizations(zip_path, zip_centroids):
    """Extract hospital/health system organizations from NPPES and write organizations.csv."""
    print("\n[2/5] Processing NPPES → organizations.csv ...")
    out_path = os.path.join(EXPORTS_DIR, "organizations.csv")

    COLS = [
        "NPI",
        "Entity Type Code",
        "Provider Organization Name (Legal Business Name)",
        "Provider Business Practice Location Address City Name",
        "Provider Business Practice Location Address State Name",
        "Provider Business Practice Location Address Postal Code",
        "NPI Deactivation Date",
        "Healthcare Provider Taxonomy Code_1",
        "Healthcare Provider Primary Taxonomy Switch_1",
        "Is Organization Subpart",
        "Parent Organization LBN",
    ]

    rows_written = 0
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = find_csv_in_zip(zf)

        with open(out_path, "w", newline="", encoding="utf-8") as fout:
            writer = None

            for chunk in stream_csv_chunks(zf, csv_name, usecols=COLS):
                mask = (
                    (chunk["Entity Type Code"] == "2")
                    & chunk["NPI Deactivation Date"].isna()
                    & chunk["Healthcare Provider Taxonomy Code_1"].isin(ORG_TAXONOMY)
                )
                chunk = chunk[mask].copy()
                if chunk.empty:
                    continue

                chunk["org_type"] = chunk["Healthcare Provider Taxonomy Code_1"].map(ORG_TAXONOMY)
                chunk["zip5"] = chunk["Provider Business Practice Location Address Postal Code"].str[:5].str.zfill(5)
                chunk["lat"] = chunk["zip5"].map(lambda z: zip_centroids.get(z, (None, None, ""))[0])
                chunk["lng"] = chunk["zip5"].map(lambda z: zip_centroids.get(z, (None, None, ""))[1])

                out = chunk.rename(columns={
                    "NPI": "org_npi",
                    "Provider Organization Name (Legal Business Name)": "org_name",
                    "Provider Business Practice Location Address City Name": "city",
                    "Provider Business Practice Location Address State Name": "state",
                    "Is Organization Subpart": "is_subpart",
                    "Parent Organization LBN": "parent_org",
                })[["org_npi", "org_name", "org_type", "city", "state",
                     "zip5", "lat", "lng", "is_subpart", "parent_org"]]

                if writer is None:
                    writer = csv.DictWriter(fout, fieldnames=out.columns.tolist())
                    writer.writeheader()

                out.to_csv(fout, index=False, header=False)
                rows_written += len(out)

    print(f"    organizations.csv: {rows_written:,} rows")


# ---------------------------------------------------------------------------
# 3. Open Payments — payment detail
# ---------------------------------------------------------------------------

def export_payments(zip_path):
    """Extract Open Payments general payments and write payments.csv."""
    print("\n[3/5] Processing Open Payments → payments.csv ...")
    out_path = os.path.join(EXPORTS_DIR, "payments.csv")

    # These are the column names in the CMS Open Payments General file
    # Exact names from the 2023 dataset
    COLS = [
        "Covered_Recipient_NPI",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
        "Total_Amount_of_Payment_USDollars",
        "Nature_of_Payment_or_Transfer_of_Value",
        "Date_of_Payment",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
        "Covered_Recipient_Type",
        "Covered_Recipient_Specialty_1",
    ]

    # Payment type simplification map
    PAYMENT_TYPE_MAP = {
        "Food and Beverage": "Meal/Food",
        "Travel and Lodging": "Travel",
        "Education": "Education",
        "Consulting Fee": "Consulting",
        "Compensation for services other than consulting, including serving as faculty or as a speaker at a venue other than a continuing education program": "Speaking Fee",
        "Compensation for serving as faculty or as a speaker for a medical education program": "CME Speaking",
        "Grant": "Grant",
        "Charitable Contribution": "Charitable",
        "Royalty or License": "Royalty",
        "Current or prospective ownership or investment interest": "Ownership",
        "Research": "Research",
    }

    rows_written = 0
    with zipfile.ZipFile(zip_path) as zf:
        csv_name = find_csv_in_zip(zf)
        print(f"    Reading: {csv_name}")

        with open(out_path, "w", newline="", encoding="utf-8") as fout:
            writer = None

            for chunk in stream_csv_chunks(zf, csv_name, usecols=COLS):
                # Only physician recipients (not teaching hospitals)
                mask = (
                    chunk["Covered_Recipient_Type"].str.contains("Physician", na=False)
                    & chunk["Covered_Recipient_NPI"].notna()
                )
                chunk = chunk[mask].copy()
                if chunk.empty:
                    continue

                chunk["payment_type_clean"] = chunk["Nature_of_Payment_or_Transfer_of_Value"].map(
                    lambda x: PAYMENT_TYPE_MAP.get(x, "Other")
                )
                chunk["amount"] = chunk["Total_Amount_of_Payment_USDollars"].str.replace(",", "").astype(float, errors="ignore")
                chunk["year"] = chunk["Date_of_Payment"].str[-4:]  # extract year from MM/DD/YYYY

                out = chunk.rename(columns={
                    "Covered_Recipient_NPI": "npi",
                    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "company",
                    "Nature_of_Payment_or_Transfer_of_Value": "payment_type_raw",
                    "Date_of_Payment": "payment_date",
                    "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "drug_device",
                    "Covered_Recipient_Specialty_1": "specialty_raw",
                })[["npi", "company", "amount", "payment_type_clean",
                     "payment_type_raw", "payment_date", "year",
                     "drug_device", "specialty_raw"]]

                if writer is None:
                    writer = csv.DictWriter(fout, fieldnames=out.columns.tolist())
                    writer.writeheader()

                out.to_csv(fout, index=False, header=False)
                rows_written += len(out)

                if rows_written % 1_000_000 == 0:
                    print(f"    {rows_written:,} rows written...")

    print(f"    payments.csv: {rows_written:,} rows")


# ---------------------------------------------------------------------------
# 4 & 5. Medicare Part D — drug-level prescribing + drug reference
# ---------------------------------------------------------------------------

# Part D specialty → our 6 canonical specialties
PARTD_SPECIALTY_MAP = {
    "Hematology/Oncology":          "Oncology",
    "Surgical Oncology":            "Oncology",
    "Radiation Oncology":           "Oncology",
    "Medical Oncology":             "Oncology",
    "Gynecological Oncology":       "Oncology",
    "Interventional Cardiology":    "Cardiology",
    "Clinical Cardiac Electrophysiology": "Cardiology",
    "Cardiac Surgery":              "Cardiology",
    "Cardiology":                   "Cardiology",
    "Cardiovascular Disease (Cardiology)": "Cardiology",
    "Orthopedic Surgery":           "Orthopedics",
    "Hand Surgery":                 "Orthopedics",
    "Sports Medicine":              "Orthopedics",
    "Neurology":                    "Neurology",
    "Neuropsychiatry":              "Neurology",
    "Clinical Neurophysiology":     "Neurology",
    "Endocrinology":                "Endocrinology",
    "Diabetes":                     "Endocrinology",
    "Pulmonary Disease":            "Pulmonology",
    "Critical Care (Intensivists)": "Pulmonology",
    "Sleep Medicine":               "Pulmonology",
}


def export_prescriber_drugs(zip_path):
    """Extract drug-level prescribing from Part D and write prescriber_drugs.csv."""
    print("\n[4/5] Processing Part D → prescriber_drugs.csv + drug_reference.csv ...")
    drugs_path = os.path.join(EXPORTS_DIR, "prescriber_drugs.csv")
    ref_path   = os.path.join(EXPORTS_DIR, "drug_reference.csv")

    # Exact column names from the Part D by Provider and Drug dataset
    COLS = [
        "Prscrbr_NPI",
        "Prscrbr_Last_Org_Name",
        "Prscrbr_First_Name",
        "Prscrbr_State_Abrvtn",
        "Prscrbr_City",
        "Prscrbr_State_FIPS",
        "Prscrbr_Type",
        "Brnd_Name",
        "Gnrc_Name",
        "Tot_Clms",
        "Tot_30day_Fills",
        "Tot_Day_Suply",
        "Tot_Drug_Cst",
        "Tot_Benes",
        "GE65_Tot_Clms",
        "Prscrbr_Ruca",     # Rural-Urban Commuting Area code — useful for rural/urban segmentation
    ]

    drug_specialty_map = {}   # {(brand, generic): specialty}  for drug_reference.csv
    rows_written = 0

    with zipfile.ZipFile(zip_path) as zf:
        csv_name = find_csv_in_zip(zf)
        print(f"    Reading: {csv_name}")

        with open(drugs_path, "w", newline="", encoding="utf-8") as fout:
            writer = None

            for chunk in stream_csv_chunks(zf, csv_name, usecols=COLS):
                # Map specialty
                chunk["specialty"] = chunk["Prscrbr_Type"].map(PARTD_SPECIALTY_MAP)
                chunk = chunk[chunk["specialty"].notna()].copy()
                if chunk.empty:
                    continue

                # Handle suppressed claim values — CMS replaces counts < 11 with empty
                chunk["tot_clms"] = (
                    chunk["Tot_Clms"]
                    .replace("", "5")   # treat suppressed as ~5
                    .fillna("5")
                    .astype(float, errors="ignore")
                )
                chunk["tot_cost"] = (
                    chunk["Tot_Drug_Cst"]
                    .str.replace(",", "", regex=False)
                    .replace("", "0")
                    .fillna("0")
                    .astype(float, errors="ignore")
                )
                chunk["tot_benes"] = (
                    chunk["Tot_Benes"]
                    .replace("", "5")
                    .fillna("5")
                    .astype(float, errors="ignore")
                )

                # Track drug → specialty for reference table
                for _, row in chunk[["Brnd_Name", "Gnrc_Name", "specialty"]].drop_duplicates().iterrows():
                    key = (row["Brnd_Name"], row["Gnrc_Name"])
                    if key not in drug_specialty_map:
                        drug_specialty_map[key] = row["specialty"]

                out = chunk.rename(columns={
                    "Prscrbr_NPI":          "npi",
                    "Prscrbr_Last_Org_Name": "last_name",
                    "Prscrbr_First_Name":   "first_name",
                    "Prscrbr_State_Abrvtn": "state",
                    "Prscrbr_City":         "city",
                    "Prscrbr_Type":         "specialty_raw",
                    "Brnd_Name":            "brand_name",
                    "Gnrc_Name":            "generic_name",
                    "Tot_30day_Fills":      "tot_30day_fills",
                    "Tot_Drug_Cst":         "tot_drug_cost_raw",
                    "Tot_Benes":            "tot_benes_raw",
                    "Prscrbr_Ruca":         "ruca_code",
                })[["npi", "last_name", "first_name", "state", "city",
                     "specialty_raw", "specialty", "brand_name", "generic_name",
                     "tot_clms", "tot_cost", "tot_benes", "ruca_code"]]

                if writer is None:
                    writer = csv.DictWriter(fout, fieldnames=out.columns.tolist())
                    writer.writeheader()

                out.to_csv(fout, index=False, header=False)
                rows_written += len(out)

                if rows_written % 1_000_000 == 0:
                    print(f"    {rows_written:,} rows written...")

    print(f"    prescriber_drugs.csv: {rows_written:,} rows")

    # Write drug reference table
    with open(ref_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["brand_name", "generic_name", "specialty"])
        for (brand, generic), spec in sorted(drug_specialty_map.items()):
            w.writerow([brand, generic, spec])
    print(f"    drug_reference.csv: {len(drug_specialty_map):,} drugs")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(EXPORTS_DIR, exist_ok=True)

    # Validate files exist
    for label, path in [("NPPES", NPPES_ZIP), ("Open Payments", PAYMENTS_ZIP), ("Part D", PARTD_ZIP)]:
        if not os.path.exists(path):
            print(f"ERROR: Cannot find {label} at:\n  {path}\nEdit the CONFIG section at the top of this script.")
            return

    print("Loading ZIP centroid lookup...")
    zip_centroids = load_zip_centroids()
    print(f"  {len(zip_centroids):,} ZIP codes loaded")

    export_physicians(NPPES_ZIP, zip_centroids)
    export_organizations(NPPES_ZIP, zip_centroids)
    export_payments(PAYMENTS_ZIP)
    export_prescriber_drugs(PARTD_ZIP)

    print("""
=============================================================
Done! Files written to exports/

Power BI import order (Get Data → Text/CSV):
  1. physicians.csv        — individual providers (NPI is the join key)
  2. organizations.csv     — hospitals & health systems
  3. payments.csv          — Open Payments detail (join on npi)
  4. prescriber_drugs.csv  — Part D drug-level prescribing (join on npi)
  5. drug_reference.csv    — dimension table (brand/generic → specialty)

Data model relationships:
  physicians.npi      → payments.npi          (one-to-many)
  physicians.npi      → prescriber_drugs.npi  (one-to-many)
  drug_reference.brand_name → prescriber_drugs.brand_name (one-to-many)
  physicians.state    → organizations.state   (for geographic filtering)

NPI is always a TEXT field — do NOT let Power BI convert it to a number.
=============================================================
""")


if __name__ == "__main__":
    main()
