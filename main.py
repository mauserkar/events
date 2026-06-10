"""
main.py
-------
Full calendar-events pipeline orchestrator:
  1. Fetch  — Download events from Microsoft Graph (with pagination)
  2. Process — Group by name, sum hours, export JSON + CSV
  3. Report  — Generate a self-contained interactive HTML report

Supports company grouping: create reports/company_mapping.json to enable.

Usage examples
--------------
    # Full run: fetch → process → report
    python main.py -t <TOKEN> -m 3 -y 2026

    # Multiple months in one shot
    python main.py -t <TOKEN> --month 1 2 3 -y 2026
    python main.py -t <TOKEN> --month-range 1 6 -y 2026

    # Skip network fetch (reuse existing JSON files)
    python main.py --skip-fetch -y 2026

    # Process a single file you already have
    python main.py --input-file ./data/march.json

    # Skip both fetch and process (regenerate HTML from processed JSONs)
    python main.py --skip-fetch --skip-process

    # Custom output directory for the HTML report
    python main.py -t <TOKEN> -m 5 -y 2026 --output-dir ./my_reports

    # Disable company grouping
    python main.py -t <TOKEN> -m 3 -y 2026 --no-company
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
import os
from pathlib import Path

import requests

from core import (
    _build_file_result,
    export_csv,
    export_processed_json,
    format_duration,
    generate_html,
    group_appointments_by_company,
    load_events_from_file,
    load_company_mapping,
    is_company_grouping_enabled,
)

# ---------------------------------------------------------------------------
# Step 1 – Fetch events from Microsoft Graph
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEZONE_HEADER = "Europe/Madrid"


def _calendar_url(year: int, month: int) -> str:
    _, last_day = calendar.monthrange(year, month)
    start = f"{year}-{month:02d}-01T00:00:00Z"
    end = f"{year}-{month:02d}-{last_day:02d}T23:59:59Z"
    return (
        f"{GRAPH_BASE}/me/calendar/calendarView"
        f"?startDateTime={start}&endDateTime={end}&$top=999"
    )


def fetch_month(token: str, year: int, month: int) -> list[dict]:
    """
    Fetch all events for one month from Graph API, following @odata.nextLink
    pagination until exhausted.
    Returns list of event dicts, or raises SystemExit on error.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": f'outlook.timezone="{TIMEZONE_HEADER}"',
    }
    url: str | None = _calendar_url(year, month)
    all_events: list[dict] = []
    page = 1

    while url:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            print(f"  ❌ HTTP {code} — {exc}")
            if exc.response is not None:
                print(f"  {exc.response.text[:400]}")
            return []
        except requests.exceptions.RequestException as exc:
            print(f"  ❌ Network error: {exc}")
            return []

        data = resp.json()
        events = data.get("value", [])
        all_events.extend(events)

        next_link = data.get("@odata.nextLink")
        if next_link:
            print(f"    📄 Page {page}: {len(events)} events, loading next page…")
            page += 1
            url = next_link
        else:
            url = None

    return all_events


def step_fetch(
    token: str,
    year: int,
    months: list[int],
    events_dir: Path,
) -> list[Path]:
    """Download events for each month and return the list of saved file paths."""
    print(f"\n🔍 Step 1: Fetching {len(months)} month(s) from Microsoft Graph…")
    saved: list[Path] = []

    for month in months:
        _, last = calendar.monthrange(year, month)
        print(
            f"  [{month:02d}/{year}] "
            f"{year}-{month:02d}-01 → {year}-{month:02d}-{last:02d} …",
            end=" ",
            flush=True,
        )
        events = fetch_month(token, year, month)
        if not events and events != []:
            print("failed — skipping")
            continue

        events_dir.mkdir(parents=True, exist_ok=True)
        out = events_dir / f"events_{year}_{month:02d}.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(events, fh, indent=2, ensure_ascii=False)

        print(f"✅ {len(events)} events → {out.name}")
        saved.append(out)

    return saved


# ---------------------------------------------------------------------------
# Step 2 – Process events
# ---------------------------------------------------------------------------


def step_process(
    input_files: list[Path],
    processed_dir: Path,
    normalize: bool = True,
    do_csv: bool = True,
    use_company_grouping: bool = None,
) -> list[dict]:
    """Group events from each file and export JSON + CSV."""
    # Load company mapping if needed
    if use_company_grouping is None:
        load_company_mapping()
        use_company_grouping = is_company_grouping_enabled()

    print(f"\n📊 Step 2: Processing {len(input_files)} file(s)…")
    if use_company_grouping:
        print(f"   🏢 Company grouping ENABLED")
    else:
        print(f"   📋 Company grouping DISABLED")
    print()

    results: list[dict] = []

    for path in input_files:
        print(f"  {path.name} …", end=" ", flush=True)
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

        json_out = processed_dir / path.name
        export_processed_json(groups, json_out)

        if do_csv:
            csv_out = processed_dir / path.with_suffix(".csv").name
            export_csv(groups, csv_out)

        result = _build_file_result(
            path.name, groups, total_appts, total_min, unique_names
        )
        results.append(result)
        print(
            f"✅ {total_appts} appointments, {unique_names} names, "
            f"{format_duration(total_min)}"
        )

    return results


def _load_processed_files(processed_dir: Path) -> list[dict]:
    """Reconstruct file-result dicts from already-processed JSON files."""
    proc_files = sorted(processed_dir.glob("events_*.json"))
    if not proc_files:
        return []

    print(f"\n📂 Loading {len(proc_files)} existing processed file(s)…")
    results: list[dict] = []

    for pf in proc_files:
        try:
            with open(pf, encoding="utf-8") as fh:
                data: list[dict] = json.load(fh)
        except Exception as exc:
            print(f"  ⚠️  Could not load {pf.name}: {exc}")
            continue

        # Check if this is company-grouped JSON
        if data and "company" in data[0]:
            # Company-grouped format
            total_min = sum(item.get("total_minutes", 0) for item in data)
            total_appts = sum(item.get("total_appointments", 0) for item in data)
            unique_names = sum(len(item.get("clients", [])) for item in data)

            groups_list = []
            for item in data:
                clients_list = []
                for client in item.get("clients", []):
                    clients_list.append(
                        {
                            "name": client.get("name", ""),
                            "count": client.get("total_appointments", 0),
                            "total_minutes": client.get("total_minutes", 0),
                            "total_duration": client.get("total_time_formatted", "0m"),
                            "appointments": client.get("appointments", []),
                        }
                    )
                groups_list.append(
                    {
                        "name": item.get("company", ""),
                        "is_company": True,
                        "total_minutes": item.get("total_minutes", 0),
                        "total_duration": item.get("total_time_formatted", "0m"),
                        "total_appointments": item.get("total_appointments", 0),
                        "clients": clients_list,
                    }
                )
        else:
            # Flat format (legacy)
            total_min = sum(item.get("total_minutes", 0) for item in data)
            total_appts = sum(item.get("total_appointments", 0) for item in data)
            unique_names = len(data)

            groups_list = [
                {
                    "name": item.get("name", ""),
                    "count": item.get("total_appointments", 0),
                    "total_minutes": item.get("total_minutes", 0),
                    "total_duration": item.get("total_time_formatted", "0m"),
                    "appointments": item.get("appointments", []),
                }
                for item in data
            ]

        results.append(
            {
                "filename": pf.name,
                "total_appointments": total_appts,
                "total_minutes": total_min,
                "total_duration": format_duration(total_min),
                "unique_names": unique_names,
                "is_company_grouped": "company" in data[0] if data else False,
                "groups": groups_list,
            }
        )

        print(f"  ✅ {pf.name}: {total_appts} appointments, {unique_names} names")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Complete pipeline: fetch → process → HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Auth
    p.add_argument(
        "-t", "--token", help="Microsoft Graph OAuth access token (or TOKEN env var)"
    )

    # Month selection
    month_grp = p.add_mutually_exclusive_group()
    month_grp.add_argument(
        "-m",
        "--month",
        type=int,
        nargs="+",
        choices=range(1, 13),
        metavar="MONTH",
        help="Month(s) to fetch (1–12). Accepts multiple: -m 1 2 3",
    )
    month_grp.add_argument(
        "--month-range",
        type=int,
        nargs=2,
        metavar=("FROM", "TO"),
        help="Inclusive month range: --month-range 1 6",
    )

    p.add_argument("-y", "--year", type=int, help="4-digit year (e.g. 2026)")

    # Processing flags
    p.add_argument(
        "--no-normalize",
        dest="normalize",
        action="store_false",
        help="Disable name normalisation",
    )
    p.add_argument(
        "--no-csv",
        dest="do_csv",
        action="store_false",
        help="Skip CSV export in Step 2",
    )
    p.add_argument(
        "--no-company",
        dest="use_company",
        action="store_false",
        help="Disable company grouping (even if company_mapping.json exists)",
    )

    # Skip flags
    p.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip Step 1 (reuse existing JSON files in reports/events/)",
    )
    p.add_argument(
        "--skip-process",
        action="store_true",
        help="Skip Step 2 (regenerate report from existing processed JSONs)",
    )

    # Single-file shortcut
    p.add_argument(
        "--input-file",
        metavar="FILE",
        help="Process a single JSON file instead of fetching from the API",
    )

    # Output
    p.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for the HTML report (default: reports/)",
    )

    p.set_defaults(normalize=True, do_csv=True, use_company=True)
    return p


def _resolve_months(args: argparse.Namespace) -> list[int]:
    if args.month_range:
        lo, hi = args.month_range
        if not (1 <= lo <= hi <= 12):
            print("❌ --month-range must satisfy 1 ≤ FROM ≤ TO ≤ 12")
            sys.exit(1)
        return list(range(lo, hi + 1))
    return args.month or []


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    token = os.environ.get("TOKEN") or args.token

    # ── Validate ──────────────────────────────────────────────────────────
    needs_fetch = not args.skip_fetch and not args.input_file
    if needs_fetch and not token:
        parser.error(
            "Provide --token (or set TOKEN env var) when fetching from the API.\n"
            "Skip with --skip-fetch or --input-file."
        )
    if needs_fetch and not args.year:
        parser.error("--year is required when fetching from the API.")
    if needs_fetch and not args.month and not args.month_range:
        parser.error("Provide --month or --month-range when fetching from the API.")

    # ── Directories ───────────────────────────────────────────────────────
    reports_dir = Path("reports")
    events_dir = reports_dir / "events"
    processed_dir = reports_dir / "processed"
    output_html_dir = Path(args.output_dir) if args.output_dir else reports_dir

    # Load company mapping if it exists
    use_company = args.use_company
    if use_company:
        load_company_mapping()
        if is_company_grouping_enabled():
            print(f"🏢 Company grouping ENABLED")
        else:
            print(f"📋 No company mapping found - using flat grouping")
            print(f"   Create reports/company_mapping.json to enable company grouping")
    else:
        print(f"📋 Company grouping DISABLED by --no-company")

    results: list[dict] = []

    # ── Step 1: Fetch ─────────────────────────────────────────────────────
    input_files: list[Path] = []

    if args.input_file:
        fp = Path(args.input_file)
        if not fp.exists():
            print(f"❌ Input file not found: {fp}")
            sys.exit(1)
        input_files = [fp]
        print(f"\n📂 Using provided file: {fp}")

    elif args.skip_fetch:
        input_files = sorted(events_dir.glob("events_*.json"))
        if not input_files:
            print(f"❌ No events_*.json files found in {events_dir}")
            sys.exit(1)
        print(f"\n📂 Reusing {len(input_files)} existing file(s) in {events_dir}")

    else:
        months = _resolve_months(args)
        input_files = step_fetch(token, args.year, months, events_dir)
        if not input_files:
            print("❌ No events were fetched. Aborting.")
            sys.exit(1)

    # ── Step 2: Process ───────────────────────────────────────────────────
    if args.skip_process:
        results = _load_processed_files(processed_dir)
        if not results:
            print(f"❌ No processed JSONs found in {processed_dir}")
            sys.exit(1)
    else:
        results = step_process(
            input_files,
            processed_dir,
            normalize=args.normalize,
            do_csv=args.do_csv,
            use_company_grouping=use_company,
        )
        if not results:
            print("❌ No events could be processed. Aborting.")
            sys.exit(1)

    # ── Step 3: HTML Report ───────────────────────────────────────────────
    print("\n📄 Step 3: Generating HTML report…")
    output_html_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_html_dir / "report.html"

    html = generate_html(results, footer_label="main.py · Calendar Events Pipeline")
    report_path.write_text(html, encoding="utf-8")

    total_appts = sum(r["total_appointments"] for r in results)
    total_time = format_duration(sum(r["total_minutes"] for r in results))

    print("\n" + "=" * 60)
    print("🎉  PIPELINE COMPLETED")
    print("=" * 60)
    print(f"   Files processed : {len(results)}")
    print(f"   Appointments    : {total_appts}")
    print(f"   Total time      : {total_time}")
    print(f"   HTML report     : {report_path}")
    print(f"   Open in browser : file://{report_path.absolute()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
