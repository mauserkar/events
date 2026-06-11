"""
process_events.py
-----------------
Processes a single calendar events JSON file (Microsoft Graph / Outlook format),
groups appointments by subject, sums their durations, and exports:
  - A processed JSON summary  →  reports/processed/<original_name>.json
  - A CSV flat export         →  reports/processed/<original_name>.csv

Usage
-----
    python process_events.py events.json
    python process_events.py events.json --no-normalize   # use raw subject strings
    python process_events.py events.json --no-csv         # skip CSV export

Input / output
--------------
    Input:   events.json
    Output:  reports/processed/events.json
             reports/processed/events.csv
"""

import argparse
import sys
from pathlib import Path

from core import (
    export_csv,
    export_processed_json,
    format_duration,
    group_appointments_by_company,
    load_company_mapping,
    is_company_grouping_enabled,
    load_events_from_file,
)

# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def print_summary(groups: dict, use_company_grouping: bool) -> None:
    """Print a summary of grouped appointments to the console."""
    if use_company_grouping:
        total_appts = sum(
            c["total_appointments"]
            for company in groups.values()
            for c in company["clients"].values()
        )
        total_min = sum(company["total_minutes"] for company in groups.values())
        unique_names = sum(len(company["clients"]) for company in groups.values())
    else:
        ungrouped = groups.get("__ungrouped__", {})
        clients = ungrouped.get("clients", {})
        total_appts = sum(len(c["appointments"]) for c in clients.values())
        total_min = sum(c["total_minutes"] for c in clients.values())
        unique_names = len(clients)

    print("\n" + "=" * 60)
    print("  APPOINTMENTS GROUPED BY NAME — SUMMARY")
    print("=" * 60)
    print(f"  Unique groups      : {unique_names}")
    print(f"  Total appointments : {total_appts}")
    print(f"  Total time         : {format_duration(total_min)}")
    print("=" * 60)

    if use_company_grouping:
        for company_name, company_data in groups.items():
            print(
                f"\n🏢  {company_data['display_name']}  |  "
                f"Appointments: {company_data['total_appointments']}  |  "
                f"Total: {format_duration(company_data['total_minutes'])}"
            )
            for client_data in company_data["clients"].values():
                n = client_data["total_appointments"]
                total = client_data["total_duration"]
                print(
                    f"     👤  {client_data['display_name']}  |  Appointments: {n}  |  Total: {total}"
                )
    else:
        ungrouped = groups.get("__ungrouped__", {})
        for client_data in ungrouped.get("clients", {}).values():
            n = client_data["total_appointments"]
            total = client_data["total_duration"]
            print(
                f"\n📅  {client_data['display_name']}  |  Appointments: {n}  |  Total: {total}"
            )

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Group calendar appointments by name, sum hours, and export results."
    )
    p.add_argument("file", help="Path to the events JSON file")
    p.add_argument(
        "--no-normalize",
        dest="normalize",
        action="store_false",
        help="Disable name normalisation (use raw subject strings as-is)",
    )
    p.add_argument(
        "--no-csv",
        dest="export_csv",
        action="store_false",
        help="Skip the CSV export",
    )
    p.add_argument(
        "--no-company",
        dest="use_company",
        action="store_false",
        help="Disable company grouping (even if company_mapping exists)",
    )
    p.set_defaults(normalize=True, export_csv=True, use_company=True)
    return p


def main() -> None:
    args = build_parser().parse_args()

    input_path = Path(args.file)
    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    events = load_events_from_file(input_path)
    if events is None:
        print("❌ Could not load events from the file.")
        sys.exit(1)

    use_company = args.use_company
    if use_company:
        load_company_mapping()
        use_company = is_company_grouping_enabled()

    groups = group_appointments_by_company(
        events, normalize=args.normalize, use_company_grouping=use_company
    )
    print_summary(groups, use_company)

    output_dir = Path("reports") / "processed"
    json_out = output_dir / input_path.name
    export_processed_json(groups, json_out)

    if args.export_csv:
        csv_out = output_dir / input_path.with_suffix(".csv").name
        export_csv(groups, csv_out)


if __name__ == "__main__":
    main()
