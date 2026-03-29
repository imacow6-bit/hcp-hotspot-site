"""
Export HCP Hotspot data to CSV files for Tableau and Power BI dashboards.

Generates 5 CSV files in the exports/ directory:
1. prescribers.csv          — 71K+ individual prescribers with tier, claims, engagement
2. zip_level.csv            — 84K ZIPs with specialty provider counts
3. metros.csv               — 50 metros with population and density metrics
4. competitor_analysis.csv  — Company × State × Specialty engagement breakdown
5. event_opportunities.csv  — Per-company, per-state opportunity scoring for sales teams

Usage:
    python export_for_dashboards.py
"""

import json
import csv
import os
import re
from collections import Counter, defaultdict

EXPORTS_DIR = os.path.join(os.path.dirname(__file__), "exports")
SPECIALTIES = ["Oncology", "Cardiology", "Orthopedics", "Neurology", "Endocrinology", "Pulmonology"]


def load_prescribers():
    with open(os.path.join(os.path.dirname(__file__), "public", "prescriber_scores.json")) as f:
        return json.load(f)


def load_zip_data():
    with open(os.path.join(os.path.dirname(__file__), "src", "zip_level_data.json")) as f:
        return json.load(f)


def load_metro_data():
    """Parse the JS module hcp_hotspot_data.js and extract the metro array."""
    path = os.path.join(os.path.dirname(__file__), "src", "hcp_hotspot_data.js")
    with open(path) as f:
        content = f.read()
    # Extract the array between the first [ and last ]
    start = content.index("[")
    end = content.rindex("]") + 1
    raw = content[start:end]
    # JS object keys aren't quoted — add quotes for JSON parsing
    # Handle keys like: city:, state:, lat:, lng:, pop:, docs:
    raw = re.sub(r'(?<=[{,\s])(\w+)\s*:', r'"\1":', raw)
    # Remove trailing commas before } or ]
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    return json.loads(raw)


def export_prescribers(data):
    """Export 1: Individual prescriber records."""
    path = os.path.join(EXPORTS_DIR, "prescribers.csv")
    fields = ["npi", "name", "lat", "lng", "state", "specialty", "tier",
              "tot_clms", "competitor_engaged", "companies", "company_count"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in data:
            companies = r.get("companies", [])
            w.writerow({
                "npi": r["npi"],
                "name": r.get("name", ""),
                "lat": r["lat"],
                "lng": r["lng"],
                "state": r["state"],
                "specialty": r["specialty"],
                "tier": r["tier"],
                "tot_clms": r.get("tot_clms", 0),
                "competitor_engaged": r.get("competitor_engaged", False),
                "companies": "; ".join(companies),
                "company_count": len(companies),
            })
    print(f"  prescribers.csv: {len(data):,} rows")


def export_zip_level(zip_data):
    """Export 2: ZIP-level provider counts."""
    path = os.path.join(EXPORTS_DIR, "zip_level.csv")
    fields = ["zip", "lat", "lng", "state"] + SPECIALTIES + ["total", "dominant_specialty"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for z in zip_data:
            docs = z.get("docs", {})
            counts = {s: docs.get(s, 0) for s in SPECIALTIES}
            total = sum(counts.values())
            dominant = max(counts, key=counts.get) if total > 0 else ""
            row = {"zip": z["zip"], "lat": z["lat"], "lng": z["lng"],
                   "state": z.get("state", "")}
            row.update(counts)
            row["total"] = total
            row["dominant_specialty"] = dominant
            w.writerow(row)
    print(f"  zip_level.csv: {len(zip_data):,} rows")


def export_metros(metros):
    """Export 3: Metro-level summary with density."""
    path = os.path.join(EXPORTS_DIR, "metros.csv")
    fields = ["city", "state", "lat", "lng", "pop"] + SPECIALTIES + ["total_docs", "docs_per_100k"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in metros:
            docs = m.get("docs", {})
            total = sum(docs.get(s, 0) for s in SPECIALTIES)
            density = round(total / m["pop"] * 100000, 1) if m["pop"] > 0 else 0
            row = {"city": m["city"], "state": m["state"],
                   "lat": m["lat"], "lng": m["lng"], "pop": m["pop"]}
            for s in SPECIALTIES:
                row[s] = docs.get(s, 0)
            row["total_docs"] = total
            row["docs_per_100k"] = density
            w.writerow(row)
    print(f"  metros.csv: {len(metros)} rows")


def export_competitor_analysis(data):
    """Export 4: Company × State × Specialty engagement breakdown."""
    path = os.path.join(EXPORTS_DIR, "competitor_analysis.csv")
    # Aggregate: (company, state, specialty) → count, claims
    agg = defaultdict(lambda: {"count": 0, "claims": 0})
    for r in data:
        for co in r.get("companies", []):
            key = (co, r["state"], r["specialty"])
            agg[key]["count"] += 1
            agg[key]["claims"] += r.get("tot_clms", 0)

    fields = ["company", "state", "specialty", "engaged_prescriber_count",
              "total_claims_engaged", "avg_claims"]
    rows = []
    for (co, state, spec), v in sorted(agg.items()):
        rows.append({
            "company": co,
            "state": state,
            "specialty": spec,
            "engaged_prescriber_count": v["count"],
            "total_claims_engaged": v["claims"],
            "avg_claims": round(v["claims"] / v["count"], 1) if v["count"] > 0 else 0,
        })
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  competitor_analysis.csv: {len(rows):,} rows")


def export_event_opportunities(data):
    """Export 5: Per-company, per-state opportunity scoring.

    For each (company, state) pair, compute:
    - How many prescribers the company engages there
    - Total Tier 1 + Tier 2 prescribers in that state (across all companies)
    - How many are white space (not engaged by ANY company)
    - Competitor count (other companies active in that state)
    - Event opportunity score

    This is the key table for the Power BI "sales pitch" dashboard.
    """
    path = os.path.join(EXPORTS_DIR, "event_opportunities.csv")

    # State-level totals (all prescribers)
    state_totals = defaultdict(lambda: {
        "total": 0, "tier1": 0, "tier2": 0,
        "engaged": 0, "white_space": 0, "total_claims": 0
    })
    for r in data:
        st = r["state"]
        state_totals[st]["total"] += 1
        state_totals[st]["total_claims"] += r.get("tot_clms", 0)
        if r["tier"] == 1:
            state_totals[st]["tier1"] += 1
        else:
            state_totals[st]["tier2"] += 1
        if r.get("competitor_engaged"):
            state_totals[st]["engaged"] += 1
        else:
            state_totals[st]["white_space"] += 1

    # Per-company, per-state engagement
    company_state = defaultdict(lambda: {
        "count": 0, "tier1_engaged": 0, "claims": 0, "specialties": set()
    })
    for r in data:
        for co in r.get("companies", []):
            key = (co, r["state"])
            company_state[key]["count"] += 1
            company_state[key]["claims"] += r.get("tot_clms", 0)
            company_state[key]["specialties"].add(r["specialty"])
            if r["tier"] == 1:
                company_state[key]["tier1_engaged"] += 1

    # All companies active per state (for competitor counting)
    state_companies = defaultdict(set)
    for (co, st) in company_state:
        state_companies[st].add(co)

    # Top 50 companies only (by total engaged prescribers) to keep it manageable
    company_totals = Counter()
    for r in data:
        for co in r.get("companies", []):
            company_totals[co] += 1
    top_companies = {co for co, _ in company_totals.most_common(50)}

    # Also compute: states where competitors are active but this company is NOT
    # This is the "opportunity gap"
    rows = []
    for co in sorted(top_companies):
        co_states = {st for (c, st) in company_state if c == co}
        all_states = set(state_totals.keys())

        for st in sorted(all_states):
            st_data = state_totals[st]
            if st_data["total"] < 5:  # skip tiny states
                continue

            cs = company_state.get((co, st))
            if cs:
                own_engaged = cs["count"]
                own_tier1 = cs["tier1_engaged"]
                own_claims = cs["claims"]
                own_specialties = len(cs["specialties"])
            else:
                own_engaged = 0
                own_tier1 = 0
                own_claims = 0
                own_specialties = 0

            competitor_count = len(state_companies[st]) - (1 if co in state_companies[st] else 0)
            market_share = round(own_engaged / st_data["engaged"] * 100, 1) if st_data["engaged"] > 0 else 0
            saturation = round(st_data["engaged"] / st_data["total"] * 100, 1) if st_data["total"] > 0 else 0

            # Opportunity score: high when there are lots of doctors, low company presence,
            # and meaningful white space
            gap_score = (
                (st_data["tier1"] * 3 + st_data["tier2"])  # market size weight
                * (1 - market_share / 100)                   # inverse of own presence
                * (st_data["white_space"] / max(st_data["total"], 1))  # white space ratio
            )

            is_gap = co not in state_companies.get(st, set())

            rows.append({
                "company": co,
                "state": st,
                "total_prescribers": st_data["total"],
                "tier1_in_state": st_data["tier1"],
                "tier2_in_state": st_data["tier2"],
                "engaged_in_state": st_data["engaged"],
                "white_space_in_state": st_data["white_space"],
                "own_engaged": own_engaged,
                "own_tier1_engaged": own_tier1,
                "own_claims": own_claims,
                "own_specialty_count": own_specialties,
                "market_share_pct": market_share,
                "saturation_pct": saturation,
                "competitor_count": competitor_count,
                "is_opportunity_gap": is_gap,
                "opportunity_score": round(gap_score, 1),
            })

    fields = ["company", "state", "total_prescribers", "tier1_in_state", "tier2_in_state",
              "engaged_in_state", "white_space_in_state", "own_engaged", "own_tier1_engaged",
              "own_claims", "own_specialty_count", "market_share_pct", "saturation_pct",
              "competitor_count", "is_opportunity_gap", "opportunity_score"]

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  event_opportunities.csv: {len(rows):,} rows")


def export_company_overlap(data):
    """Export 6: Company co-occurrence matrix (top 20 companies).

    Shows how many prescribers two companies share — key for competitive analysis.
    """
    path = os.path.join(EXPORTS_DIR, "company_overlap.csv")

    # Get top 20 companies
    company_totals = Counter()
    for r in data:
        for co in r.get("companies", []):
            company_totals[co] += 1
    top20 = [co for co, _ in company_totals.most_common(20)]

    # Count co-occurrence
    pair_counts = Counter()
    for r in data:
        cos = sorted(set(r.get("companies", [])) & set(top20))
        for i in range(len(cos)):
            for j in range(i + 1, len(cos)):
                pair_counts[(cos[i], cos[j])] += 1

    fields = ["company_a", "company_b", "shared_prescribers",
              "company_a_total", "company_b_total", "overlap_pct_of_a", "overlap_pct_of_b"]
    rows = []
    for (a, b), shared in sorted(pair_counts.items(), key=lambda x: -x[1]):
        a_total = company_totals[a]
        b_total = company_totals[b]
        rows.append({
            "company_a": a,
            "company_b": b,
            "shared_prescribers": shared,
            "company_a_total": a_total,
            "company_b_total": b_total,
            "overlap_pct_of_a": round(shared / a_total * 100, 1),
            "overlap_pct_of_b": round(shared / b_total * 100, 1),
        })

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  company_overlap.csv: {len(rows)} rows")


def main():
    os.makedirs(EXPORTS_DIR, exist_ok=True)

    print("Loading data...")
    prescribers = load_prescribers()
    zip_data = load_zip_data()
    metros = load_metro_data()

    print(f"\nExporting to {EXPORTS_DIR}/")
    export_prescribers(prescribers)
    export_zip_level(zip_data)
    export_metros(metros)
    export_competitor_analysis(prescribers)
    export_event_opportunities(prescribers)
    export_company_overlap(prescribers)

    print(f"\nDone! 6 CSV files ready for Power BI and Tableau.")
    print(f"\nRecommended Power BI import order:")
    print(f"  1. prescribers.csv        — fact table (main data)")
    print(f"  2. event_opportunities.csv — pre-scored for sales pitch dashboard")
    print(f"  3. company_overlap.csv     — competitive relationship network")
    print(f"  4. competitor_analysis.csv  — drilldown by company/state/specialty")
    print(f"  5. metros.csv              — dimension table for metro context")
    print(f"  6. zip_level.csv           — dimension table for ZIP context")


if __name__ == "__main__":
    main()
