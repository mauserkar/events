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
  - Company grouping support (auto-enabled if config.yaml exists)

Usage
-----
    python report_events.py ./data/
    python report_events.py ./data/ --output report.html
    python report_events.py ./data/ --no-normalize
    python report_events.py ./data/ --csv          # also export a combined CSV
    python report_events.py ./data/ --no-company   # disable company grouping
"""

import argparse
import sys
from pathlib import Path

from core import (
    generate_html,
    group_appointments_by_company,
    load_events_from_file,
    format_duration,
    _build_file_result,
    load_company_mapping,
    is_company_grouping_enabled,
)

# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------


def process_directory(
    directory: Path,
    normalize: bool = True,
    export_csv_flag: bool = False,
    use_company_grouping: bool = None,
) -> list[dict]:
    """
    Load and process every *.json in *directory* (skipping *_grouped.json files).
    Returns a list of file-result dicts ready for HTML rendering.

    Args:
        directory: Directory containing JSON files
        normalize: Whether to normalize client names
        export_csv_flag: Whether to export CSV files
        use_company_grouping: If None, auto-enable if mapping file exists
    """
    # Load company mapping if it exists
    if use_company_grouping is None:
        load_company_mapping()
        use_company_grouping = is_company_grouping_enabled()

    json_files = sorted(
        f for f in directory.glob("*.json") if not f.stem.endswith("_grouped")
    )
    if not json_files:
        return []

    print(f"\n📂 Found {len(json_files)} file(s) in '{directory}'")
    if use_company_grouping:
        print(f"   🏢 Company grouping ENABLED")
    else:
        print(f"   📋 Company grouping DISABLED")
    print()

    results: list[dict] = []

    for path in json_files:
        print(f"  Processing: {path.name} ...", end=" ", flush=True)
        events = load_events_from_file(path)
        if events is None:
            print("skipped")
            continue

        groups = group_appointments_by_company(
            events, normalize=normalize, use_company_grouping=use_company_grouping
        )

        total_min = 0
        total_appts = 0
        unique_names = 0

        for company_data in groups.values():
            total_appts += company_data["total_appointments"]
            total_min += company_data["total_minutes"]
            unique_names += len(company_data["clients"])

        if export_csv_flag:
            csv_path = path.with_suffix(".csv")
            from core import export_csv as _export_csv

            _export_csv(groups, csv_path)

        result = _build_file_result(
            path.name, groups, total_appts, total_min, unique_names
        )
        results.append(result)
        print(
            f"✅  {total_appts} appointments, {unique_names} names, "
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
        "--no-company",
        dest="use_company",
        action="store_false",
        help="Disable company grouping (even if config.yaml exists)",
    )
    p.add_argument(
        "--csv",
        dest="export_csv",
        action="store_true",
        help="Export a CSV file alongside each JSON source file",
    )
    p.set_defaults(normalize=True, export_csv=False, use_company=True)
    return p


def main() -> None:
    args = build_parser().parse_args()

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"❌ Not a directory: {directory}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else directory / "report.html"

    use_company = args.use_company

    results = process_directory(
        directory,
        normalize=args.normalize,
        export_csv_flag=args.export_csv,
        use_company_grouping=use_company,
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
