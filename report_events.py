"""
report_events.py
----------------
Scans a directory for calendar event JSON files (Microsoft Graph / Outlook format),
processes each one, groups appointments by name, sums hours, and generates
a single self-contained HTML report with:
  - Per-file collapsible sections with mini bar-charts
  - Live search (client name) and sort controls
  - Per-group invoice generation (opens a print-ready page)
  - Provider details persisted in localStorage across sessions

Usage
-----
    python report_events.py ./data/
    python report_events.py ./data/ --output report.html
    python report_events.py ./data/ --no-normalize
    python report_events.py ./data/ --csv          # also export a combined CSV
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import (
    export_csv,
    generate_html,
    group_appointments,
    load_events_from_file,
    format_duration,
    _build_file_result,
)

# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------


def process_directory(
    directory: Path, normalize: bool = True, export_csv_flag: bool = False
) -> list[dict]:
    """
    Load and process every *.json in *directory* (skipping *_grouped.json files).
    Returns a list of file-result dicts ready for HTML rendering.
    """
    json_files = sorted(
        f for f in directory.glob("*.json") if not f.stem.endswith("_grouped")
    )
    if not json_files:
        return []

    print(f"\n📂 Found {len(json_files)} file(s) in '{directory}'\n")
    results: list[dict] = []

    for path in json_files:
        print(f"  Processing: {path.name} ...", end=" ", flush=True)
        events = load_events_from_file(path)
        if events is None:
            print("skipped")
            continue

        groups = group_appointments(events, normalize=normalize)
        total_min = sum(g["total_minutes"] for g in groups.values())
        total_appts = sum(len(g["appointments"]) for g in groups.values())

        if export_csv_flag:
            csv_path = path.with_suffix(".csv")
            from core import export_csv as _export_csv

            _export_csv(groups, csv_path)

        result = _build_file_result(path.name, groups, total_appts, total_min)
        results.append(result)
        print(
            f"✅  {total_appts} appointments, {len(groups)} names, "
            f"{format_duration(total_min)}"
        )

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Scan a directory for calendar JSON files and generate a self-contained HTML report."
        )
    )
    p.add_argument("directory", help="Path to directory containing JSON files")
    p.add_argument(
        "--output",
        default=None,
        metavar="report.html",
        help="Output HTML file path (default: <directory>/report.html)",
    )
    p.add_argument(
        "--no-normalize",
        dest="normalize",
        action="store_false",
        help="Disable name normalisation (use raw subject strings as-is)",
    )
    p.add_argument(
        "--csv",
        dest="export_csv",
        action="store_true",
        help="Export a CSV file alongside each JSON source file",
    )
    p.set_defaults(normalize=True, export_csv=False)
    return p


def main() -> None:
    args = build_parser().parse_args()

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"❌ Not a directory: {directory}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else directory / "report.html"

    results = process_directory(
        directory, normalize=args.normalize, export_csv_flag=args.export_csv
    )
    if not results:
        print(f"❌ No valid JSON files found in: {directory}")
        sys.exit(1)

    html = generate_html(results, footer_label="report_events.py")
    output_path.write_text(html, encoding="utf-8")

    print(f"\n✅ Report generated: {output_path}")
    print(f"   Open in browser: file://{output_path}")


if __name__ == "__main__":
    main()
