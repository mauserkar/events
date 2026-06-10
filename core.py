"""
core.py
-------
Shared logic for the calendar events pipeline.
All data processing, formatting, HTML rendering, and invoice generation
lives here so that process_events.py, report_events.py, get_calendar_events.py
and main.py can import without any duplication.
"""

from __future__ import annotations

import base64
import csv
import json
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Date / time helpers
# ---------------------------------------------------------------------------


def parse_date(dt_str: str) -> datetime:
    """Parse an ISO 8601 datetime string (with or without timezone)."""
    # Strip trailing 'Z' so fromisoformat works on Python < 3.11
    return datetime.fromisoformat(dt_str.rstrip("Z"))


def duration_minutes(start: str, end: str) -> float:
    """Return the number of minutes between two ISO datetime strings."""
    return (parse_date(end) - parse_date(start)).total_seconds() / 60


def format_duration(minutes: float) -> str:
    """Convert minutes to a human-readable 'Xh Ym' string."""
    h, m = int(minutes // 60), int(minutes % 60)
    if h == 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


def format_dt(dt_str: str) -> str:
    """Format an ISO datetime string as 'DD/MM/YYYY HH:MM'."""
    return parse_date(dt_str).strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """
    Normalise a subject/name so near-duplicate variants are grouped together:
    - Unicode NFD decomposition → strip combining marks (removes accents)
    - Lowercase, strip leading/trailing whitespace
    - Keep only alphanumerics and spaces
    - Collapse internal whitespace
    """
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower().strip()
    name = "".join(c for c in name if c.isalnum() or c == " ")
    return " ".join(name.split())


# ---------------------------------------------------------------------------
# Event schema validation
# ---------------------------------------------------------------------------


def validate_event(event: dict, index: int) -> tuple[bool, str]:
    """
    Validate a single calendar event against the expected Microsoft Graph schema.
    Returns (is_valid, error_message).

    Expected minimal structure:
    {
        "subject": str (optional, defaults to "(no title)"),
        "start": {"dateTime": str, "timeZone": str (optional)},
        "end": {"dateTime": str, "timeZone": str (optional)}
    }
    """
    if not isinstance(event, dict):
        return False, f"Event {index}: not a dictionary (got {type(event).__name__})"

    # Validate start field
    start = event.get("start")
    if not isinstance(start, dict):
        return False, f"Event {index}: missing or invalid 'start' field (expected dict)"

    start_dt = start.get("dateTime")
    if not start_dt or not isinstance(start_dt, str):
        return (
            False,
            f"Event {index}: missing or invalid 'start.dateTime' (expected non-empty string)",
        )

    # Validate end field
    end = event.get("end")
    if not isinstance(end, dict):
        return False, f"Event {index}: missing or invalid 'end' field (expected dict)"

    end_dt = end.get("dateTime")
    if not end_dt or not isinstance(end_dt, str):
        return (
            False,
            f"Event {index}: missing or invalid 'end.dateTime' (expected non-empty string)",
        )

    # Validate datetime format
    try:
        parse_date(start_dt)
        parse_date(end_dt)
    except ValueError as exc:
        return False, f"Event {index}: invalid datetime format - {exc}"

    # Check that end is after start
    try:
        if parse_date(end_dt) <= parse_date(start_dt):
            return False, f"Event {index}: end time must be after start time"
    except Exception:
        pass  # Already validated above

    return True, ""


def validate_events(events: list[dict], max_errors: int = 5) -> tuple[list[dict], int]:
    """
    Validate a list of events, filtering out invalid ones.
    Returns (valid_events, skipped_count).

    Args:
        events: List of event dictionaries to validate
        max_errors: Maximum number of error messages to print

    Returns:
        Tuple of (valid_events_list, number_of_skipped_events)
    """
    valid = []
    skipped = 0
    errors_printed = 0

    for i, event in enumerate(events):
        is_valid, error = validate_event(event, i)
        if is_valid:
            valid.append(event)
        else:
            skipped += 1
            if errors_printed < max_errors:
                print(f"  ⚠️  {error}")
                errors_printed += 1
            elif errors_printed == max_errors:
                print(f"  ⚠️  ... and more errors (suppressed)")
                errors_printed += 1

    return valid, skipped


# ---------------------------------------------------------------------------
# Core grouping logic
# ---------------------------------------------------------------------------


def group_appointments(events: list[dict], normalize: bool = True) -> dict[str, dict]:
    """
    Group a list of Graph API events by subject and accumulate durations.

    Returns an OrderedDict-like dict sorted by total_minutes descending.
    Each value has the shape::

        {
            "display_name": str,
            "appointments": [
                {
                    "subject": str,
                    "start": str,   # raw ISO string
                    "end": str,     # raw ISO string
                    "duration": str,          # e.g. "1h 30m"
                    "duration_min": float,
                    "timezone": str,
                }
            ],
            "total_minutes": float,
        }

    Malformed events are skipped with a warning (max 3 printed).
    """
    # First validate all events
    valid_events, validation_skipped = validate_events(events)

    groups: dict[str, dict] = defaultdict(
        lambda: {"display_name": "", "appointments": [], "total_minutes": 0.0}
    )
    skipped = validation_skipped

    for event in valid_events:
        try:
            subject = event.get("subject", "(no title)")
            key = normalize_name(subject) if normalize else subject
            start = event["start"]["dateTime"]
            end = event["end"]["dateTime"]
            dur = duration_minutes(start, end)
        except (KeyError, TypeError, ValueError) as exc:
            skipped += 1
            if skipped <= 3:
                print(
                    f"  ⚠️  Skipped event (subject={event.get('subject','?')!r}): {exc}"
                )
            continue

        if not groups[key]["display_name"]:
            groups[key]["display_name"] = subject

        groups[key]["total_minutes"] += dur
        groups[key]["appointments"].append(
            {
                "subject": subject,
                "start": start,
                "end": end,
                "duration": format_duration(dur),
                "duration_min": dur,
                "timezone": event.get("start", {}).get("timeZone", ""),
            }
        )

    if skipped:
        print(f"  ⚠️  {skipped} event(s) skipped due to missing or invalid fields.")

    return dict(
        sorted(groups.items(), key=lambda x: x[1]["total_minutes"], reverse=True)
    )


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def load_events_from_file(path: Path, validate: bool = True) -> list[dict] | None:
    """
    Load a Graph API JSON file.  Accepts:
    - A plain list  → returned as-is
    - A dict with a 'value' key  → value returned

    Args:
        path: Path to the JSON file
        validate: If True, validates event schema and filters invalid events

    Returns None and prints a warning on any error.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ⚠️  Skipping {path.name}: {exc}")
        return None

    if isinstance(data, list):
        if len(data) == 0:
            print(f"  ⚠️  {path.name}: empty list")
        events = data
    elif isinstance(data, dict):
        if "value" in data:
            events = data["value"]
        else:
            print(f"  ⚠️  {path.name}: unexpected dict keys: {list(data.keys())[:8]}")
            return None
    else:
        print(f"  ⚠️  {path.name}: unexpected JSON type: {type(data).__name__}")
        return None

    # Validate events if requested
    if validate and events:
        valid_events, skipped = validate_events(events)
        if skipped:
            print(f"  ⚠️  {path.name}: {skipped} invalid event(s) skipped during load")
        return valid_events

    return events


def export_processed_json(groups: dict[str, dict], output_path: Path) -> None:
    """Serialise grouped appointments to a JSON file."""
    result = [
        {
            "name": g["display_name"],
            "total_appointments": len(g["appointments"]),
            "total_minutes": g["total_minutes"],
            "total_time_formatted": format_duration(g["total_minutes"]),
            "appointments": g["appointments"],
        }
        for g in groups.values()
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"  ✅ Processed JSON exported to: {output_path}")


def export_csv(groups: dict[str, dict], output_path: Path) -> None:
    """
    Export all appointments to a flat CSV file with columns:
    group_name, subject, start, end, duration_min, duration, timezone
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "group_name",
                "subject",
                "start",
                "end",
                "duration_min",
                "duration",
                "timezone",
            ]
        )
        for group in groups.values():
            for appt in group["appointments"]:
                writer.writerow(
                    [
                        group["display_name"],
                        appt["subject"],
                        appt["start"],
                        appt["end"],
                        round(appt["duration_min"], 2),
                        appt["duration"],
                        appt.get("timezone", ""),
                    ]
                )
    print(f"  ✅ CSV exported to: {output_path}")


def process_directory(directory: Path, normalize: bool = True) -> list[dict]:
    """
    Scan *directory* for *.json files (excluding *_grouped.json), process each,
    and return a list of file-result dicts ready for HTML rendering.
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

        results.append(_build_file_result(path.name, groups, total_appts, total_min))
        print(
            f"✅  {total_appts} appointments, {len(groups)} names, {format_duration(total_min)}"
        )

    return results


def _build_file_result(
    filename: str,
    groups: dict[str, dict],
    total_appts: int,
    total_min: float,
) -> dict:
    return {
        "filename": filename,
        "total_appointments": total_appts,
        "total_minutes": total_min,
        "total_duration": format_duration(total_min),
        "unique_names": len(groups),
        "groups": [
            {
                "name": g["display_name"],
                "count": len(g["appointments"]),
                "total_minutes": g["total_minutes"],
                "total_duration": format_duration(g["total_minutes"]),
                "appointments": g["appointments"],
            }
            for g in groups.values()
        ],
    }


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------


def render_bar_chart(groups: list[dict]) -> str:
    if not groups:
        return ""
    max_min = max(g["total_minutes"] for g in groups) or 1
    rows = []
    for g in groups:
        pct = round(g["total_minutes"] / max_min * 100, 1)
        label = g["name"][:22] + "…" if len(g["name"]) > 22 else g["name"]
        rows.append(
            f'<div class="bar-row">'
            f'<span class="bar-label" title="{g["name"]}">{label}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>'
            f'<span class="bar-value">{g["total_duration"]}</span>'
            f"</div>"
        )
    return "\n".join(rows)


def render_appointments_table(appointments: list[dict]) -> str:
    rows = "".join(
        f"<tr>"
        f"<td>{format_dt(a['start'])}</td>"
        f"<td>{format_dt(a['end'])}</td>"
        f"<td>{a['duration']}</td>"
        f"<td class='tz'>{a.get('timezone', '')}</td>"
        f"</tr>"
        for a in appointments
    )
    return (
        "<table class='appt-table'>"
        "<thead><tr>"
        "<th>Start</th><th>End</th><th>Duration</th><th>Timezone</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def render_file_section(file_result: dict) -> str:
    name_groups_html = ""
    for g in file_result["groups"]:
        table = render_appointments_table(g["appointments"])

        invoice_payload = {
            "groupName": g["name"],
            "totalMinutes": g["total_minutes"],
            "totalDuration": g["total_duration"],
            "sourceFile": file_result["filename"],
            "appointments": [
                {
                    "subject": a["subject"],
                    "start": format_dt(a["start"]),
                    "end": format_dt(a["end"]),
                    "duration": a["duration"],
                }
                for a in g["appointments"]
            ],
        }
        payload_b64 = base64.b64encode(
            json.dumps(invoice_payload, ensure_ascii=False).encode()
        ).decode()

        # Escape name for use in HTML attributes
        safe_name = g["name"].replace("'", "&#39;").replace('"', "&quot;")

        name_groups_html += (
            f'<div class="name-group" data-name="{safe_name}">'
            f'<div class="name-header">'
            f'<span class="name-chevron-wrap">'
            f'<span class="name-title">{g["name"]}</span>'
            f'<span class="name-chevron">▾</span>'
            f"</span>"
            f'<span class="name-meta">'
            f'<span class="badge badge-blue">{g["count"]} appt{"s" if g["count"] != 1 else ""}</span>'
            f'<span class="badge badge-green">{g["total_duration"]}</span>'
            f'<button class="btn-invoice" onclick="openInvoiceModal(\'{payload_b64}\')" '
            f'title="Generate invoice for {safe_name}">🧾 Invoice</button>'
            f"</span>"
            f"</div>"
            f'<div class="name-body">{table}</div>'
            f"</div>"
        )

    chart = render_bar_chart(file_result["groups"])
    safe_filename = file_result["filename"].replace("'", "&#39;")

    return (
        f'<div class="file-section" data-filename="{safe_filename}">'
        f'<div class="file-header">'
        f'<span class="file-title"><span class="icon">📄</span>{file_result["filename"]}</span>'
        f'<span class="file-meta">'
        f'<span class="badge badge-blue">{file_result["total_appointments"]} appointments</span>'
        f'<span class="badge badge-amber">{file_result["unique_names"]} names</span>'
        f'<span class="badge badge-green">{file_result["total_duration"]}</span>'
        f'<span class="chevron">▾</span>'
        f"</span>"
        f"</div>"
        f'<div class="file-body">'
        f"{chart}"
        f"{name_groups_html}"
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Full HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Appointments Report</title>
<style>
/* ── Reset & tokens ───────────────────────────────────────────────── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f0efe9;
  --surface:#ffffff;
  --surface2:#f5f4f0;
  --surface3:#eeede8;
  --border:rgba(0,0,0,0.08);
  --text:#181816;
  --text2:#75746e;
  --text3:#a8a79f;
  --accent:#2563eb;
  --accent-bg:#eff6ff;
  --accent-text:#1d4ed8;
  --accent-border:#bfdbfe;
  --green-bg:#f0fdf4;
  --green-text:#16a34a;
  --green-border:#bbf7d0;
  --amber-bg:#fffbeb;
  --amber-text:#d97706;
  --amber-border:#fde68a;
  --red-bg:#fef2f2;
  --red-text:#dc2626;
  --radius:12px;
  --radius-sm:7px;
  --radius-xs:4px;
  --shadow:0 1px 3px rgba(0,0,0,0.07),0 1px 2px rgba(0,0,0,0.04);
  --shadow-md:0 4px 12px rgba(0,0,0,0.08),0 1px 3px rgba(0,0,0,0.05);
}}
@media(prefers-color-scheme:dark){{
  :root{{
    --bg:#111110;--surface:#1c1c1a;--surface2:#232320;--surface3:#2a2a27;
    --border:rgba(255,255,255,0.07);--text:#e8e8e4;--text2:#87867e;--text3:#555450;
    --accent:#60a5fa;--accent-bg:#172554;--accent-text:#93c5fd;--accent-border:#1e3a5f;
    --green-bg:#052e16;--green-text:#4ade80;--green-border:#14532d;
    --amber-bg:#1c1400;--amber-text:#fbbf24;--amber-border:#451a03;
    --red-bg:#2d0a0a;--red-text:#f87171;
  }}
}}

/* ── Base ─────────────────────────────────────────────────────────── */
body{{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Inter",sans-serif;
  background:var(--bg);color:var(--text);font-size:14px;line-height:1.6;
  padding:2rem 1rem;
}}
.container{{max-width:980px;margin:0 auto}}

/* ── Page header ──────────────────────────────────────────────────── */
.page-header{{margin-bottom:1.75rem;display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap}}
.page-header-left h1{{font-size:20px;font-weight:700;letter-spacing:-0.3px;margin-bottom:2px}}
.page-header-left p{{color:var(--text2);font-size:12px}}

/* ── Search & filter bar ─────────────────────────────────────────── */
.toolbar{{
  display:flex;align-items:center;gap:8px;margin-bottom:1.5rem;
  background:var(--surface);border:0.5px solid var(--border);
  border-radius:var(--radius);padding:8px 12px;
  box-shadow:var(--shadow);flex-wrap:wrap;
}}
.search-wrap{{position:relative;flex:1;min-width:180px}}
.search-icon{{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--text3);font-size:13px;pointer-events:none}}
.search-input{{
  width:100%;padding:6px 10px 6px 28px;border-radius:var(--radius-sm);
  border:0.5px solid var(--border);background:var(--surface2);color:var(--text);
  font-size:13px;font-family:inherit;
}}
.search-input:focus{{outline:none;border-color:var(--accent);background:var(--surface)}}
.search-input::placeholder{{color:var(--text3)}}
.filter-sep{{width:1px;height:20px;background:var(--border);flex-shrink:0}}
.filter-label{{font-size:12px;color:var(--text2);white-space:nowrap}}
.filter-select{{
  padding:5px 8px;border-radius:var(--radius-sm);border:0.5px solid var(--border);
  background:var(--surface2);color:var(--text);font-size:12px;font-family:inherit;
  cursor:pointer;
}}
.filter-select:focus{{outline:none;border-color:var(--accent)}}
.btn-clear-search{{
  display:none;font-size:11px;padding:4px 9px;border-radius:var(--radius-xs);
  border:0.5px solid var(--border);background:var(--surface2);color:var(--text2);
  cursor:pointer;white-space:nowrap;
}}
.btn-clear-search:hover{{background:var(--surface3)}}
.btn-clear-search.visible{{display:inline-flex;align-items:center;gap:4px}}
.no-results{{
  text-align:center;padding:3rem 1rem;color:var(--text2);
  background:var(--surface);border:0.5px solid var(--border);
  border-radius:var(--radius);display:none;
}}
.no-results.visible{{display:block}}

/* ── Summary metrics ──────────────────────────────────────────────── */
.summary-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:1.5rem}}
.metric{{
  background:var(--surface);border:0.5px solid var(--border);
  border-radius:var(--radius);padding:14px 16px;
  box-shadow:var(--shadow);
}}
.metric-label{{font-size:11px;color:var(--text2);margin-bottom:4px;font-weight:500;text-transform:uppercase;letter-spacing:0.04em}}
.metric-value{{font-size:24px;font-weight:700;letter-spacing:-0.5px}}
.metric-sub{{font-size:11px;color:var(--text3);margin-top:2px}}

/* ── File section ─────────────────────────────────────────────────── */
.file-section{{
  background:var(--surface);border:0.5px solid var(--border);
  border-radius:var(--radius);margin-bottom:1.25rem;
  overflow:hidden;box-shadow:var(--shadow);
  transition:box-shadow 0.15s;
}}
.file-section:hover{{box-shadow:var(--shadow-md)}}
.file-header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:13px 18px;border-bottom:0.5px solid var(--border);
  cursor:pointer;user-select:none;gap:12px;
}}
.file-header:hover{{background:var(--surface2)}}
.file-title{{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px}}
.file-meta{{display:flex;align-items:center;gap:7px;flex-shrink:0;flex-wrap:wrap}}
.badge{{font-size:11px;padding:2px 8px;border-radius:20px;font-weight:500;white-space:nowrap;border:0.5px solid transparent}}
.badge-blue{{background:var(--accent-bg);color:var(--accent-text);border-color:var(--accent-border)}}
.badge-green{{background:var(--green-bg);color:var(--green-text);border-color:var(--green-border)}}
.badge-amber{{background:var(--amber-bg);color:var(--amber-text);border-color:var(--amber-border)}}
.chevron{{font-size:11px;color:var(--text2);transition:transform 0.2s;flex-shrink:0}}
.file-body{{padding:0 16px 14px}}

/* ── Bar chart ────────────────────────────────────────────────────── */
.bar-row{{display:flex;align-items:center;gap:10px;margin-top:12px;margin-bottom:1px}}
.bar-label{{font-size:12px;color:var(--text2);width:130px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0}}
.bar-track{{flex:1;background:var(--surface3);border-radius:4px;height:7px;overflow:hidden}}
.bar-fill{{height:100%;background:var(--accent);border-radius:4px;transition:width 0.6s cubic-bezier(.16,1,.3,1)}}
.bar-value{{font-size:12px;font-weight:600;color:var(--text2);width:52px;text-align:right;flex-shrink:0}}

/* ── Name group ───────────────────────────────────────────────────── */
.name-group{{border:0.5px solid var(--border);border-radius:var(--radius-sm);margin-top:10px;overflow:hidden}}
.name-header{{display:flex;align-items:center;justify-content:space-between;padding:9px 13px;background:var(--surface2);gap:8px}}
.name-title{{font-weight:500;font-size:13px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.name-meta{{display:flex;align-items:center;gap:6px;flex-shrink:0}}
.name-chevron-wrap{{cursor:pointer;display:flex;align-items:center;gap:6px;flex:1;min-width:0}}
.name-chevron{{font-size:11px;color:var(--text2);transition:transform 0.2s;flex-shrink:0}}
.name-body{{display:none}}

/* ── Appointments table ───────────────────────────────────────────── */
.appt-table{{width:100%;border-collapse:collapse;font-size:12px}}
.appt-table th{{text-align:left;padding:7px 13px;color:var(--text2);font-weight:500;border-bottom:0.5px solid var(--border);background:var(--surface);font-size:11px;text-transform:uppercase;letter-spacing:0.04em}}
.appt-table td{{padding:7px 13px;border-bottom:0.5px solid var(--border);color:var(--text)}}
.appt-table tr:last-child td{{border-bottom:none}}
.appt-table tr:hover td{{background:var(--surface2)}}
.tz{{color:var(--text3)!important;font-size:11px}}

/* ── Invoice button ───────────────────────────────────────────────── */
.btn-invoice{{
  display:inline-flex;align-items:center;gap:4px;
  font-size:11px;font-weight:500;padding:4px 9px;
  border-radius:var(--radius-xs);cursor:pointer;
  background:var(--surface);border:0.5px solid var(--border);
  color:var(--text2);white-space:nowrap;
  transition:all 0.15s;font-family:inherit;
}}
.btn-invoice:hover{{background:var(--accent-bg);color:var(--accent-text);border-color:var(--accent-border)}}

/* ── Modal overlay ────────────────────────────────────────────────── */
.modal-overlay{{
  display:none;position:fixed;inset:0;
  background:rgba(0,0,0,0.45);backdrop-filter:blur(3px);z-index:1000;
  align-items:center;justify-content:center;padding:1rem;
}}
.modal-overlay.open{{display:flex}}
.modal{{
  background:var(--surface);border-radius:var(--radius);
  border:0.5px solid var(--border);box-shadow:var(--shadow-md);
  width:100%;max-width:700px;max-height:92vh;
  display:flex;flex-direction:column;overflow:hidden;
  animation:modal-in 0.18s ease;
}}
@keyframes modal-in{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}
.modal-head{{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 18px;border-bottom:0.5px solid var(--border);flex-shrink:0;
}}
.modal-head h2{{font-size:15px;font-weight:600}}
.modal-close{{background:none;border:none;cursor:pointer;font-size:18px;color:var(--text2);line-height:1;padding:3px 7px;border-radius:var(--radius-xs)}}
.modal-close:hover{{background:var(--surface2)}}
.modal-form{{padding:18px;overflow-y:auto;flex:1}}
.form-section-title{{
  font-size:11px;font-weight:600;color:var(--text);text-transform:uppercase;
  letter-spacing:0.06em;padding:10px 0 6px;border-top:0.5px solid var(--border);
  margin-top:6px;
}}
.form-section-title:first-child{{border-top:none;padding-top:0}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:2px}}
.form-group{{display:flex;flex-direction:column;gap:4px}}
.form-group.full{{grid-column:1/-1}}
.form-group label{{font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:0.04em}}
.form-group input,.form-group textarea{{
  font-size:13px;padding:7px 10px;border-radius:var(--radius-sm);
  border:0.5px solid var(--border);background:var(--surface2);color:var(--text);
  font-family:inherit;width:100%;transition:border-color 0.15s;
}}
.form-group input:focus,.form-group textarea:focus{{outline:none;border-color:var(--accent);background:var(--surface)}}
.form-group textarea{{resize:vertical;min-height:64px}}
.modal-foot{{
  display:flex;justify-content:flex-end;gap:8px;
  padding:13px 18px;border-top:0.5px solid var(--border);flex-shrink:0;
}}
.btn{{font-size:13px;font-weight:500;padding:7px 16px;border-radius:var(--radius-sm);cursor:pointer;border:0.5px solid var(--border);font-family:inherit;transition:all 0.15s}}
.btn-secondary{{background:var(--surface2);color:var(--text)}}
.btn-secondary:hover{{background:var(--surface3)}}
.btn-primary{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.btn-primary:hover{{opacity:0.88}}

/* ── Saved-data notice ────────────────────────────────────────────── */
.saved-notice{{
  font-size:11px;color:var(--green-text);display:none;align-items:center;gap:4px;
}}
.saved-notice.visible{{display:flex}}

/* ── Footer ───────────────────────────────────────────────────────── */
.report-footer{{font-size:11px;color:var(--text3);text-align:center;margin-top:2.5rem;padding-top:1rem;border-top:0.5px solid var(--border)}}

/* ── Responsive ───────────────────────────────────────────────────── */
@media(max-width:600px){{
  .file-meta{{display:none}}
  .form-grid{{grid-template-columns:1fr}}
  .form-group.full{{grid-column:1}}
  .toolbar{{gap:6px}}
  .filter-sep,.filter-label{{display:none}}
}}
</style>
</head>
<body>
<div class="container">

<div class="page-header">
  <div class="page-header-left">
    <h1>📅 Appointments Report</h1>
    <p>Generated on {generated_on} &mdash; {num_files} file(s) processed</p>
  </div>
</div>

<div class="summary-grid">
  <div class="metric">
    <div class="metric-label">Files</div>
    <div class="metric-value">{num_files}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Appointments</div>
    <div class="metric-value">{total_appts}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Unique clients</div>
    <div class="metric-value">{total_names}</div>
  </div>
  <div class="metric">
    <div class="metric-label">Total time</div>
    <div class="metric-value">{total_time}</div>
  </div>
</div>

<div class="toolbar">
  <div class="search-wrap">
    <span class="search-icon">🔍</span>
    <input class="search-input" id="searchInput" type="search"
      placeholder="Search by client name…" autocomplete="off" spellcheck="false">
  </div>
  <div class="filter-sep"></div>
  <span class="filter-label">Sort:</span>
  <select class="filter-select" id="sortSelect">
    <option value="time-desc">Most time</option>
    <option value="time-asc">Least time</option>
    <option value="count-desc">Most appointments</option>
    <option value="name-asc">Name A→Z</option>
  </select>
  <button class="btn-clear-search" id="clearSearch">✕ Clear</button>
</div>

<div id="noResults" class="no-results">
  <p>No clients match "<span id="noResultsQuery"></span>".</p>
</div>

{file_sections}

<p class="report-footer">{footer_label}</p>
</div>

<!-- ── Invoice modal ─────────────────────────────────────────────── -->
<div class="modal-overlay" id="invoiceModal">
  <div class="modal">
    <div class="modal-head">
      <h2>🧾 Generate Invoice</h2>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-form">

      <div class="form-section-title">Invoice details</div>
      <div class="form-grid">
        <div class="form-group">
          <label>Invoice number</label>
          <input type="text" id="inv_number" placeholder="INV-2026-001">
        </div>
        <div class="form-group"></div>
        <div class="form-group">
          <label>Issue date</label>
          <input type="date" id="inv_date">
        </div>
        <div class="form-group">
          <label>Due date</label>
          <input type="date" id="inv_due">
        </div>
      </div>

      <div class="form-section-title">From (your details)</div>
      <div class="form-grid">
        <div class="form-group full">
          <label>Company / Name</label>
          <input type="text" id="from_name" placeholder="Your Company S.L.">
        </div>
        <div class="form-group">
          <label>Tax ID / NIF</label>
          <input type="text" id="from_tax" placeholder="B-12345678">
        </div>
        <div class="form-group">
          <label>Email</label>
          <input type="email" id="from_email" placeholder="billing@company.com">
        </div>
        <div class="form-group full">
          <label>Address</label>
          <input type="text" id="from_address" placeholder="Calle Mayor 1, 28001 Madrid">
        </div>
      </div>

      <div class="form-section-title">Bill to (client)</div>
      <div class="form-grid">
        <div class="form-group full">
          <label>Client name</label>
          <input type="text" id="to_name" placeholder="Client Name">
        </div>
        <div class="form-group">
          <label>Tax ID / NIF</label>
          <input type="text" id="to_tax" placeholder="">
        </div>
        <div class="form-group">
          <label>Email</label>
          <input type="email" id="to_email" placeholder="client@email.com">
        </div>
        <div class="form-group full">
          <label>Address</label>
          <input type="text" id="to_address" placeholder="Client address">
        </div>
      </div>

      <div class="form-section-title">Rate &amp; taxes</div>
      <div class="form-grid">
        <div class="form-group">
          <label>Hourly rate (€)</label>
          <input type="number" id="inv_rate" placeholder="100" min="0" step="0.01">
        </div>
        <div class="form-group">
          <label>VAT / IVA (%)</label>
          <input type="number" id="inv_vat" placeholder="21" min="0" max="100" value="21">
        </div>
        <div class="form-group full">
          <label>Notes</label>
          <textarea id="inv_notes" placeholder="Payment terms, bank details…"></textarea>
        </div>
      </div>

    </div>
    <div class="modal-foot">
      <span class="saved-notice" id="savedNotice">✓ Details saved</span>
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="generateInvoice()">Generate &amp; print</button>
    </div>
  </div>
</div>

<script>
// ── Constants ──────────────────────────────────────────────────────────────
const STORAGE_KEY = 'appt_report_provider_v1';
const PROVIDER_FIELDS = ['from_name','from_tax','from_email','from_address'];

// ── Provider data persistence ──────────────────────────────────────────────
function saveProvider() {{
  const data = {{}};
  PROVIDER_FIELDS.forEach(id => {{ data[id] = document.getElementById(id).value; }});
  try {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); }} catch(e) {{}}
  document.getElementById('savedNotice').classList.add('visible');
  setTimeout(() => document.getElementById('savedNotice').classList.remove('visible'), 1800);
}}

function loadProvider() {{
  try {{
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    PROVIDER_FIELDS.forEach(id => {{
      if (data[id] !== undefined && document.getElementById(id)) {{
        document.getElementById(id).value = data[id];
      }}
    }});
  }} catch(e) {{}}
}}

// Auto-save on blur for provider fields
PROVIDER_FIELDS.forEach(id => {{
  const el = document.getElementById(id);
  if (el) el.addEventListener('blur', saveProvider);
}});

// ── Collapse / expand ──────────────────────────────────────────────────────
document.querySelectorAll('.file-header').forEach(h => {{
  h.addEventListener('click', () => {{
    const body = h.nextElementSibling;
    const chevron = h.querySelector('.chevron');
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    chevron.style.transform = open ? 'rotate(-90deg)' : '';
  }});
}});
document.querySelectorAll('.name-chevron-wrap').forEach(wrap => {{
  wrap.addEventListener('click', () => {{
    const group = wrap.closest('.name-group');
    const body = group.querySelector('.name-body');
    const chevron = wrap.querySelector('.name-chevron');
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    chevron.style.transform = open ? 'rotate(-90deg)' : '';
  }});
}});

// ── Search & sort ──────────────────────────────────────────────────────────
const searchInput  = document.getElementById('searchInput');
const sortSelect   = document.getElementById('sortSelect');
const clearBtn     = document.getElementById('clearSearch');
const noResults    = document.getElementById('noResults');
const noResultsQ   = document.getElementById('noResultsQuery');

function applyFilters() {{
  const q = searchInput.value.trim().toLowerCase();
  const sort = sortSelect.value;

  clearBtn.classList.toggle('visible', q.length > 0);

  let visibleCount = 0;

  document.querySelectorAll('.file-section').forEach(section => {{
    const groups = Array.from(section.querySelectorAll('.name-group'));

    // Filter groups by search query
    let anyVisible = false;
    groups.forEach(g => {{
      const name = (g.dataset.name || '').toLowerCase();
      const match = !q || name.includes(q);
      g.style.display = match ? '' : 'none';
      if (match) anyVisible = true;
    }});

    section.style.display = anyVisible ? '' : 'none';
    if (anyVisible) visibleCount++;

    // Sort visible groups
    if (anyVisible && sort !== 'time-desc') {{
      const parent = groups[0]?.parentNode;
      if (!parent) return;
      const visible = groups.filter(g => g.style.display !== 'none');
      visible.sort((a, b) => {{
        const aMin  = parseFloat(a.querySelector('.badge-green')?.dataset.min || 0);
        const bMin  = parseFloat(b.querySelector('.badge-green')?.dataset.min || 0);
        const aName = (a.dataset.name || '').toLowerCase();
        const bName = (b.dataset.name || '').toLowerCase();
        const aCount = parseInt(a.querySelector('.badge-blue')?.dataset.count || 0);
        const bCount = parseInt(b.querySelector('.badge-blue')?.dataset.count || 0);
        if (sort === 'time-asc')    return aMin - bMin;
        if (sort === 'count-desc')  return bCount - aCount;
        if (sort === 'name-asc')    return aName.localeCompare(bName);
        return 0;
      }});
      // Append chart rows first, then sorted groups
      const chartRows = Array.from(parent.children).filter(el => el.classList.contains('bar-row'));
      chartRows.forEach(el => parent.appendChild(el));
      visible.forEach(el => parent.appendChild(el));
    }}
  }});

  noResults.classList.toggle('visible', visibleCount === 0 && q.length > 0);
  if (q) {{ noResultsQ.textContent = q; }}
}}

searchInput.addEventListener('input', applyFilters);
sortSelect.addEventListener('change', applyFilters);
clearBtn.addEventListener('click', () => {{
  searchInput.value = '';
  applyFilters();
  searchInput.focus();
}});

// Attach data attributes for sort (minutes & count) to badges
document.querySelectorAll('.name-group').forEach(g => {{
  const greenBadge = g.querySelector('.badge-green');
  const blueBadge  = g.querySelector('.badge-blue');
  // minutes: stored in the invoice payload base64 — parse lazily via data attr
  const invoiceBtn = g.querySelector('.btn-invoice');
  if (invoiceBtn && greenBadge) {{
    try {{
      const payload = JSON.parse(atob(invoiceBtn.getAttribute('onclick').match(/'([^']+)'/)[1]));
      greenBadge.dataset.min = payload.totalMinutes;
    }} catch(e) {{}}
  }}
  if (blueBadge) {{
    const m = blueBadge.textContent.match(/\\d+/);
    if (m) blueBadge.dataset.count = m[0];
  }}
}});

// ── Invoice modal ──────────────────────────────────────────────────────────
let _invoiceData = null;

function openInvoiceModal(dataB64) {{
  _invoiceData = JSON.parse(atob(dataB64));
  loadProvider();
  document.getElementById('to_name').value = _invoiceData.groupName;
  document.getElementById('to_tax').value  = '';
  document.getElementById('to_email').value = '';
  document.getElementById('to_address').value = '';

  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  document.getElementById('inv_number').value =
    'INV-' + now.getFullYear() + '-' + pad(now.getMonth()+1) + pad(now.getDate());
  document.getElementById('inv_date').value = now.toISOString().slice(0,10);
  const due = new Date(now); due.setDate(due.getDate()+30);
  document.getElementById('inv_due').value = due.toISOString().slice(0,10);
  document.getElementById('invoiceModal').classList.add('open');
}}

function closeModal() {{
  document.getElementById('invoiceModal').classList.remove('open');
}}

document.getElementById('invoiceModal').addEventListener('click', e => {{
  if (e.target === e.currentTarget) closeModal();
}});

document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') closeModal();
  if (e.key === 'f' && (e.ctrlKey || e.metaKey) && !e.shiftKey) {{
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  }}
}});

function generateInvoice() {{
  const d = _invoiceData;
  const rate    = parseFloat(document.getElementById('inv_rate').value)  || 0;
  const vatPct  = parseFloat(document.getElementById('inv_vat').value)   || 0;
  const hours   = d.totalMinutes / 60;
  const subtotal = hours * rate;
  const vatAmt  = subtotal * vatPct / 100;
  const total   = subtotal + vatAmt;
  const fmt     = n => n.toLocaleString('es-ES',{{minimumFractionDigits:2,maximumFractionDigits:2}});
  const fmtDate = s => s ? new Date(s).toLocaleDateString('es-ES') : '—';

  const rows = d.appointments.map(a =>
    `<tr><td>${{a.subject}}</td><td>${{a.start}}</td><td>${{a.end}}</td>
     <td style="text-align:right">${{a.duration}}</td></tr>`
  ).join('');

  const notes = document.getElementById('inv_notes').value;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Invoice ${{document.getElementById('inv_number').value}}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;color:#18181a;padding:48px;max-width:760px;margin:0 auto}}
.inv-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:40px}}
.inv-title{{font-size:30px;font-weight:700;letter-spacing:-0.5px}}
.inv-number{{font-size:13px;color:#75746e;margin-top:4px}}
.inv-dates{{text-align:right;font-size:12px;color:#75746e;line-height:1.8}}
.inv-dates strong{{color:#18181a}}
.parties{{display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-bottom:36px;padding-bottom:28px;border-bottom:1px solid #e5e5e3}}
.party-label{{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:#75746e;margin-bottom:6px}}
.party-name{{font-size:14px;font-weight:600;margin-bottom:2px}}
.party-detail{{font-size:12px;color:#75746e;line-height:1.6}}
table{{width:100%;border-collapse:collapse;margin-bottom:24px;font-size:12px}}
thead th{{text-align:left;padding:8px 12px;background:#f5f5f3;font-weight:500;color:#75746e;border-bottom:1px solid #e5e5e3}}
thead th:last-child{{text-align:right}}
tbody td{{padding:9px 12px;border-bottom:1px solid #f0f0ee;color:#18181a;vertical-align:top}}
tbody tr:last-child td{{border-bottom:none}}
.totals{{margin-left:auto;width:260px;margin-top:8px}}
.total-row{{display:flex;justify-content:space-between;padding:5px 0;font-size:13px;color:#75746e}}
.total-row.grand{{font-size:16px;font-weight:700;color:#18181a;border-top:2px solid #18181a;margin-top:8px;padding-top:10px}}
.notes{{margin-top:36px;padding-top:20px;border-top:1px solid #e5e5e3;font-size:12px;color:#75746e;line-height:1.7;white-space:pre-wrap}}
.notes strong{{color:#18181a;display:block;margin-bottom:4px;font-size:11px;text-transform:uppercase;letter-spacing:0.06em}}
@media print{{body{{padding:32px}}@page{{margin:0.8cm}}}}
</style>
</head>
<body>
<div class="inv-header">
  <div>
    <div class="inv-title">Invoice</div>
    <div class="inv-number">#${{document.getElementById('inv_number').value}}</div>
  </div>
  <div class="inv-dates">
    <div>Issue date: <strong>${{fmtDate(document.getElementById('inv_date').value)}}</strong></div>
    <div>Due date:   <strong>${{fmtDate(document.getElementById('inv_due').value)}}</strong></div>
  </div>
</div>
<div class="parties">
  <div>
    <div class="party-label">From</div>
    <div class="party-name">${{document.getElementById('from_name').value||'—'}}</div>
    <div class="party-detail">
      ${{document.getElementById('from_tax').value?'NIF: '+document.getElementById('from_tax').value+'<br>':''}}
      ${{document.getElementById('from_email').value||''}}
      ${{document.getElementById('from_address').value?'<br>'+document.getElementById('from_address').value:''}}
    </div>
  </div>
  <div>
    <div class="party-label">Bill to</div>
    <div class="party-name">${{document.getElementById('to_name').value||'—'}}</div>
    <div class="party-detail">
      ${{document.getElementById('to_tax').value?'NIF: '+document.getElementById('to_tax').value+'<br>':''}}
      ${{document.getElementById('to_email').value||''}}
      ${{document.getElementById('to_address').value?'<br>'+document.getElementById('to_address').value:''}}
    </div>
  </div>
</div>
<table>
  <thead><tr><th>Description</th><th>Start</th><th>End</th><th style="text-align:right">Duration</th></tr></thead>
  <tbody>${{rows}}</tbody>
</table>
<div class="totals">
  <div class="total-row"><span>Hours worked</span><span>${{hours.toFixed(2)}} h</span></div>
  <div class="total-row"><span>Rate</span><span>${{fmt(rate)}} €/h</span></div>
  <div class="total-row"><span>Subtotal</span><span>${{fmt(subtotal)}} €</span></div>
  <div class="total-row"><span>VAT (${{vatPct}}%)</span><span>${{fmt(vatAmt)}} €</span></div>
  <div class="total-row grand"><span>Total</span><span>${{fmt(total)}} €</span></div>
</div>
${{notes?'<div class="notes"><strong>Notes</strong>'+notes+'</div>':''}}
</body></html>`;

  const win = window.open('','_blank');
  if (!win) {{ alert('Please allow pop-ups to generate the invoice.'); return; }}
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 450);
  closeModal();
}}
</script>
</body>
</html>
"""


def generate_html(results: list[dict], footer_label: str = "calendar-pipeline") -> str:
    """Render the full HTML report from a list of file-result dicts."""
    total_appts = sum(r["total_appointments"] for r in results)
    total_names = sum(r["unique_names"] for r in results)
    total_min = sum(r["total_minutes"] for r in results)
    file_sections = "\n".join(render_file_section(r) for r in results)

    return _HTML_TEMPLATE.format(
        generated_on=datetime.now().strftime("%d/%m/%Y %H:%M"),
        num_files=len(results),
        total_appts=total_appts,
        total_names=total_names,
        total_time=format_duration(total_min),
        file_sections=file_sections,
        footer_label=footer_label,
    )
