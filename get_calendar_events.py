"""
get_calendar_events.py
----------------------
Downloads calendar events from Microsoft Graph for one or more months
and saves them as JSON files under reports/events/.

Features
--------
- Automatic pagination: follows @odata.nextLink so you never miss events
  when a single response is truncated (Graph returns ≤ 1 000 items/page).
- Multi-month support: pass --month 3 4 5 or --month-range 1 6 to fetch
  several months in one run.
- Token can be supplied via --token or the TOKEN env variable.

Usage examples
--------------
    python get_calendar_events.py -m 3 -y 2026 -t <TOKEN>
    python get_calendar_events.py --month 1 2 3 -y 2026
    python get_calendar_events.py --month-range 1 6 -y 2026
    TOKEN=<token> python get_calendar_events.py -m 5 -y 2026
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from pathlib import Path
import requests

# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEZONE_HEADER = "Europe/Madrid"


def _build_url(year: int, month: int) -> str:
    _, last_day = calendar.monthrange(year, month)
    start = f"{year}-{month:02d}-01T00:00:00Z"
    end = f"{year}-{month:02d}-{last_day:02d}T23:59:59Z"
    return (
        f"{GRAPH_BASE}/me/calendar/calendarView"
        f"?startDateTime={start}&endDateTime={end}"
        f"&$top=999"  # max items per page
    )


def fetch_events_for_month(token: str, year: int, month: int) -> list[dict]:
    """
    Fetch ALL calendar events for *year*/*month* from Microsoft Graph,
    following pagination (@odata.nextLink) until exhausted.

    Raises SystemExit on authentication or network errors.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": f'outlook.timezone="{TIMEZONE_HEADER}"',
    }

    url: str | None = _build_url(year, month)
    all_events: list[dict] = []
    page = 1

    while url:
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            print(f"  ❌ HTTP {status} error fetching page {page}: {exc}")
            if exc.response is not None:
                err_body = exc.response.text[:400]
                print(f"  Response: {err_body}")
            sys.exit(1)
        except requests.exceptions.RequestException as exc:
            print(f"  ❌ Network error on page {page}: {exc}")
            sys.exit(1)

        data = response.json()
        page_events = data.get("value", [])
        all_events.extend(page_events)

        next_link = data.get("@odata.nextLink")
        if next_link:
            print(
                f"    📄 Page {page}: {len(page_events)} events — following next page…"
            )
            page += 1
            url = next_link
        else:
            url = None

    return all_events


def save_events(events: list[dict], year: int, month: int, output_dir: Path) -> Path:
    """Persist *events* to a JSON file and return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"events_{year}_{month:02d}.json"
    file_path = output_dir / file_name
    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump(events, fh, indent=2, ensure_ascii=False)
    return file_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _month_list(args: argparse.Namespace) -> list[int]:
    """Return the resolved list of months to fetch."""
    if args.month_range:
        lo, hi = args.month_range
        if not (1 <= lo <= hi <= 12):
            print("❌ --month-range must be two values M1 M2 where 1 ≤ M1 ≤ M2 ≤ 12")
            sys.exit(1)
        return list(range(lo, hi + 1))
    return args.month  # already validated by choices


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Download calendar events from Microsoft Graph for one or more months."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            __doc__.split("Usage examples")[1] if "Usage examples" in __doc__ else ""
        ),
    )
    p.add_argument(
        "-t",
        "--token",
        help="Microsoft Graph OAuth access token (fallback: TOKEN env var)",
    )
    # Month: one or many explicit values
    p.add_argument(
        "-m",
        "--month",
        type=int,
        nargs="+",
        choices=range(1, 13),
        metavar="MONTH",
        help="Month(s) to fetch (1-12). Accepts multiple values: -m 1 2 3",
    )
    # Month: continuous range
    p.add_argument(
        "--month-range",
        type=int,
        nargs=2,
        metavar=("FROM", "TO"),
        help="Inclusive range of months: --month-range 1 6 fetches Jan-Jun",
    )
    p.add_argument(
        "-y",
        "--year",
        type=int,
        required=True,
        help="4-digit year (e.g. 2026)",
    )
    p.add_argument(
        "--output-dir",
        default=os.path.join("reports", "events"),
        help="Directory where JSON files are saved (default: reports/events)",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.month and not args.month_range:
        parser.error("Provide --month (-m) or --month-range.")

    token = os.environ.get("TOKEN") or args.token
    if not token:
        print("❌ Microsoft Graph token missing.")
        print("   Set the TOKEN environment variable or pass --token <TOKEN>.")
        print(
            "   Get a temporary token at:"
            " https://developer.microsoft.com/en-us/graph/graph-explorer"
        )
        print(
            "add url: https://graph.microsoft.com/v1.0/me/calendar/calendarView"
            "🔒 Ensure your token has 'Calendars.Read' permissions granted in the portal."
        )
        sys.exit(1)

    months = _month_list(args)
    output_dir = Path(args.output_dir)

    print(f"\n🗓️  Fetching {len(months)} month(s) for year {args.year}…")
    print(f"📂 Output directory: {output_dir}\n")

    for month in months:
        _, last_day = calendar.monthrange(args.year, month)
        print(
            f"  [{month:02d}/{args.year}] "
            f"Fetching {args.year}-{month:02d}-01 → {args.year}-{month:02d}-{last_day:02d}…"
        )

        events = fetch_events_for_month(token, args.year, month)
        file_path = save_events(events, args.year, month, output_dir)

        print(f"  ✅ {len(events)} event(s) saved to {file_path}")

        if events:
            sample = events[:8]
            for i, ev in enumerate(sample, 1):
                print(f"     {i}. {ev.get('subject', '(no title)')}")
            if len(events) > 8:
                print(f"     … and {len(events) - 8} more")
        print()

    print("=" * 50)
    print(f"🎉 Done — {len(months)} file(s) saved in {output_dir}")
    print("=" * 50)


if __name__ == "__main__":
    main()
