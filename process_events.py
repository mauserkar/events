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

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import (
    export_csv,
    export_processed_json,
    format_duration,
    group_appointments,
    load_events_from_file,
)

# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def print_summary(groups: dict) -> None:
    total_appts = sum(len(g["appointments"]) for g in groups.values())
    total_min = sum(g["total_minutes"] for g in groups.values())

    print("\n" + "=" * 60)
    print("  APPOINTMENTS GROUPED BY NAME — SUMMARY")
    print("=" * 60)
    print(f"  Unique groups      : {len(groups)}")
    print(f"  Total appointments : {total_appts}")
    print(f"  Total time         : {format_duration(total_min)}")
    print("=" * 60)

    for group in groups.values():
        n = len(group["appointments"])
        total = format_duration(group["total_minutes"])
        print(f"\n📅  {group['display_name']}  |  Appointments: {n}  |  Total: {total}")

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
    p.set_defaults(normalize=True, export_csv=True)
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

    groups = group_appointments(events, normalize=args.normalize)
    print_summary(groups)

    output_dir = Path("reports") / "processed"
    json_out = output_dir / input_path.name
    export_processed_json(groups, json_out)

    if args.export_csv:
        csv_out = output_dir / input_path.with_suffix(".csv").name
        export_csv(groups, csv_out)


if __name__ == "__main__":
    main()
