"""
core.py
-------
Shared logic for the calendar events pipeline.
All data processing, formatting, HTML rendering, and invoice generation
lives here so that process_events.py, report_events.py, get_calendar_events.py
and main.py can import without any duplication.
"""

import base64
import csv
import json
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Try to import yaml, fall back to pyyaml if needed
try:
    import yaml
except ImportError:
    print("⚠️  PyYAML not installed. Install with: pip install pyyaml")
    yaml = None

# ---------------------------------------------------------------------------
# Configuration management
# ---------------------------------------------------------------------------

_CONFIG_FILE = Path("config.yaml")
_config_cache: dict | None = None


def load_config(force_reload: bool = False) -> dict:
    """
    Load configuration from YAML file.
    Returns empty dict if file doesn't exist or YAML is not available.
    """
    global _config_cache

    if _config_cache is not None and not force_reload:
        return _config_cache

    if yaml is None:
        return {}

    if not _CONFIG_FILE.exists():
        print(f"  ℹ️  No config file found at {_CONFIG_FILE}")
        print(f"     Create this file to customize invoices and company mapping")
        return {}

    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as fh:
            _config_cache = yaml.safe_load(fh) or {}
            print(f"  ✅ Loaded configuration from {_CONFIG_FILE}")
            return _config_cache
    except Exception as exc:
        print(f"  ⚠️  Could not load config: {exc}")
        return {}


def get_company_mapping() -> dict[str, str]:
    """Get company mapping from config."""
    config = load_config()
    return config.get("company_mapping", {})


def get_invoice_defaults() -> dict:
    """Get global invoice defaults from config."""
    config = load_config()
    return config.get("invoice_defaults", {})


def get_company_settings(company_name: str) -> dict:
    """Get specific settings for a company."""
    config = load_config()
    company_settings = config.get("company_settings", {})
    return company_settings.get(company_name, {})


def get_client_settings(client_name: str) -> dict:
    """Get specific settings for a client."""
    config = load_config()
    client_settings = config.get("client_settings", {})
    return client_settings.get(client_name, {})


def get_invoice_number() -> str:
    """
    Generate invoice number based on format in config.
    Uses sequence number stored in config file.
    """
    config = load_config()
    fmt = config.get("invoice_number_format", "INV-{year}{month:02d}{seq:04d}")
    seq = config.get("invoice_number_seq", 1)

    now = datetime.now()
    result = fmt.format(
        year=now.year,
        month=now.month,
        day=now.day,
        seq=seq,
        hour=now.hour,
        minute=now.minute,
    )

    return result


def is_auto_company_grouping_enabled() -> bool:
    """Check if auto company grouping is enabled in config."""
    config = load_config()
    return config.get("report_settings", {}).get("auto_company_grouping", True)


# ---------------------------------------------------------------------------
# Company mapping (client → company) - now from YAML
# ---------------------------------------------------------------------------

_COMPANY_MAPPING: dict[str, str] = {}
_COMPANY_MAPPING_LOADED: bool = False


def load_company_mapping(
    file_path: Path | None = None, force_reload: bool = False
) -> dict[str, str]:
    """
    Load company mapping from YAML config file.
    """
    global _COMPANY_MAPPING, _COMPANY_MAPPING_LOADED

    if _COMPANY_MAPPING_LOADED and not force_reload:
        return _COMPANY_MAPPING

    # Load from YAML config
    config = load_config(force_reload)
    mapping = config.get("company_mapping", {})

    _COMPANY_MAPPING.clear()
    _COMPANY_MAPPING.update(mapping)

    if _COMPANY_MAPPING:
        print(
            f"  ✅ Loaded company mapping: {len(_COMPANY_MAPPING)} client(s) → company"
        )
    else:
        print(f"  ℹ️  No company mapping found in config")

    _COMPANY_MAPPING_LOADED = True
    return _COMPANY_MAPPING


def save_company_mapping(
    mapping: dict[str, str], file_path: Path | None = None
) -> None:
    """
    Save company mapping to YAML config file.
    Note: This will preserve other config sections.
    """
    global _COMPANY_MAPPING, _COMPANY_MAPPING_LOADED

    if yaml is None:
        print("  ❌ Cannot save mapping: PyYAML not installed")
        return

    # Load existing config
    config = load_config(force_reload=True)

    # Update mapping
    config["company_mapping"] = mapping

    # Save back to file
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as fh:
        yaml.dump(
            config, fh, allow_unicode=True, default_flow_style=False, sort_keys=False
        )

    _COMPANY_MAPPING.clear()
    _COMPANY_MAPPING.update(mapping)
    _COMPANY_MAPPING_LOADED = True
    print(f"  ✅ Company mapping saved to: {_CONFIG_FILE}")


def get_company_for_client(client_name: str) -> str:
    """
    Return the company name for a given client, or the client name itself
    if no mapping exists.
    """
    if not _COMPANY_MAPPING_LOADED:
        load_company_mapping()

    return _COMPANY_MAPPING.get(client_name, client_name)


def set_company_mapping(mapping: dict[str, str]) -> None:
    """Set the company mapping dictionary."""
    global _COMPANY_MAPPING, _COMPANY_MAPPING_LOADED
    _COMPANY_MAPPING.clear()
    _COMPANY_MAPPING.update(mapping)
    _COMPANY_MAPPING_LOADED = True


def is_company_grouping_enabled() -> bool:
    """
    Check if company grouping should be used.
    """
    if not _COMPANY_MAPPING_LOADED:
        load_company_mapping()

    # Check both: mapping exists AND auto grouping is enabled
    auto_enabled = is_auto_company_grouping_enabled()
    return len(_COMPANY_MAPPING) > 0 and auto_enabled


# ---------------------------------------------------------------------------
# Date / time helpers
# ---------------------------------------------------------------------------


def parse_date(dt_str: str) -> datetime:
    """Parse an ISO 8601 datetime string (with or without timezone)."""
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
    """
    if not isinstance(event, dict):
        return False, f"Event {index}: not a dictionary (got {type(event).__name__})"

    start = event.get("start")
    if not isinstance(start, dict):
        return False, f"Event {index}: missing or invalid 'start' field (expected dict)"

    start_dt = start.get("dateTime")
    if not start_dt or not isinstance(start_dt, str):
        return (
            False,
            f"Event {index}: missing or invalid 'start.dateTime' (expected non-empty string)",
        )

    end = event.get("end")
    if not isinstance(end, dict):
        return False, f"Event {index}: missing or invalid 'end' field (expected dict)"

    end_dt = end.get("dateTime")
    if not end_dt or not isinstance(end_dt, str):
        return (
            False,
            f"Event {index}: missing or invalid 'end.dateTime' (expected non-empty string)",
        )

    try:
        parse_date(start_dt)
        parse_date(end_dt)
    except ValueError as exc:
        return False, f"Event {index}: invalid datetime format - {exc}"

    try:
        if parse_date(end_dt) <= parse_date(start_dt):
            return False, f"Event {index}: end time must be after start time"
    except Exception:
        pass

    return True, ""


def validate_events(events: list[dict], max_errors: int = 5) -> tuple[list[dict], int]:
    """
    Validate a list of events, filtering out invalid ones.
    Returns (valid_events, skipped_count).
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
# Core grouping logic (with company support)
# ---------------------------------------------------------------------------


def group_appointments_by_company(
    events: list[dict], normalize: bool = True, use_company_grouping: bool = None
) -> dict[str, dict]:
    """
    Group events first by company (if mapping exists), then by client name.
    """
    client_groups = _group_appointments_flat(events, normalize)

    if use_company_grouping is None:
        use_company_grouping = is_company_grouping_enabled()

    if not use_company_grouping:
        return {"__ungrouped__": _convert_to_company_format(client_groups)}

    companies: dict[str, dict] = defaultdict(
        lambda: {
            "display_name": "",
            "total_minutes": 0.0,
            "total_appointments": 0,
            "clients": {},
        }
    )

    for client_key, client_data in client_groups.items():
        company = get_company_for_client(client_data["display_name"])

        if not companies[company]["display_name"]:
            companies[company]["display_name"] = company

        companies[company]["total_minutes"] += client_data["total_minutes"]
        companies[company]["total_appointments"] += len(client_data["appointments"])
        companies[company]["clients"][client_key] = {
            "display_name": client_data["display_name"],
            "total_minutes": client_data["total_minutes"],
            "total_appointments": len(client_data["appointments"]),
            "total_duration": format_duration(client_data["total_minutes"]),
            "appointments": client_data["appointments"],
        }

    result = dict(companies)
    for company in result.values():
        company["clients"] = dict(
            sorted(
                company["clients"].items(),
                key=lambda x: x[1]["total_minutes"],
                reverse=True,
            )
        )

    return dict(
        sorted(result.items(), key=lambda x: x[1]["total_minutes"], reverse=True)
    )


def _group_appointments_flat(
    events: list[dict], normalize: bool = True
) -> dict[str, dict]:
    """
    Group appointments by client name (flat structure).
    """
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


def _convert_to_company_format(flat_groups: dict[str, dict]) -> dict:
    """
    Convert flat client groups to the same structure as company grouping
    for consistent rendering.
    """
    return {
        "display_name": "All Clients",
        "total_minutes": sum(g["total_minutes"] for g in flat_groups.values()),
        "total_appointments": sum(len(g["appointments"]) for g in flat_groups.values()),
        "clients": {
            key: {
                "display_name": data["display_name"],
                "total_minutes": data["total_minutes"],
                "total_appointments": len(data["appointments"]),
                "total_duration": format_duration(data["total_minutes"]),
                "appointments": data["appointments"],
            }
            for key, data in flat_groups.items()
        },
    }


# Legacy function for backward compatibility
def group_appointments(events: list[dict], normalize: bool = True) -> dict[str, dict]:
    """
    Legacy function - returns flat client grouping.
    Use group_appointments_by_company() for company-aware grouping.
    """
    return _group_appointments_flat(events, normalize)


# ---------------------------------------------------------------------------
# Base64 encoding helper (FIXED for UTF-8)
# ---------------------------------------------------------------------------


def encode_to_base64(data: dict) -> str:
    """
    Encode a dictionary to base64 string with proper UTF-8 encoding.
    """
    json_str = json.dumps(data, ensure_ascii=False)
    json_bytes = json_str.encode("utf-8")
    return base64.b64encode(json_bytes).decode("ascii")


def decode_from_base64(b64_str: str) -> dict:
    """
    Decode a base64 string back to dictionary with proper UTF-8 decoding.
    """
    json_bytes = base64.b64decode(b64_str)
    json_str = json_bytes.decode("utf-8")
    return json.loads(json_str)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def load_events_from_file(path: Path, validate: bool = True) -> list[dict] | None:
    """
    Load a Graph API JSON file. Accepts a plain list or a dict with 'value' key.
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

    if validate and events:
        valid_events, skipped = validate_events(events)
        if skipped:
            print(f"  ⚠️  {path.name}: {skipped} invalid event(s) skipped during load")
        return valid_events

    return events


def export_processed_json(groups: dict[str, dict], output_path: Path) -> None:
    """Serialise grouped appointments to a JSON file."""
    first_group = next(iter(groups.values())) if groups else None
    if first_group and "clients" in first_group:
        result = []
        for company_name, company_data in groups.items():
            company_result = {
                "company": company_data["display_name"],
                "total_appointments": company_data["total_appointments"],
                "total_minutes": company_data["total_minutes"],
                "total_time_formatted": format_duration(company_data["total_minutes"]),
                "clients": [],
            }
            for client_data in company_data["clients"].values():
                company_result["clients"].append(
                    {
                        "name": client_data["display_name"],
                        "total_appointments": client_data["total_appointments"],
                        "total_minutes": client_data["total_minutes"],
                        "total_time_formatted": client_data["total_duration"],
                        "appointments": client_data["appointments"],
                    }
                )
            result.append(company_result)
    else:
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
    Export all appointments to a flat CSV file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    first_group = next(iter(groups.values())) if groups else None
    is_company_grouped = first_group and "clients" in first_group

    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)

        if is_company_grouped:
            writer.writerow(
                [
                    "company",
                    "client_name",
                    "subject",
                    "start",
                    "end",
                    "duration_min",
                    "duration",
                    "timezone",
                ]
            )
            for company_name, company_data in groups.items():
                for client_data in company_data["clients"].values():
                    for appt in client_data["appointments"]:
                        writer.writerow(
                            [
                                company_data["display_name"],
                                client_data["display_name"],
                                appt["subject"],
                                appt["start"],
                                appt["end"],
                                round(appt["duration_min"], 2),
                                appt["duration"],
                                appt.get("timezone", ""),
                            ]
                        )
        else:
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


def process_directory(
    directory: Path, normalize: bool = True, use_company_grouping: bool = None
) -> list[dict]:
    """
    Scan *directory* for *.json files (excluding *_grouped.json), process each,
    and return a list of file-result dicts ready for HTML rendering.
    """
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
        print(
            f"   🏢 Company grouping ENABLED ({len(_COMPANY_MAPPING)} client mappings)"
        )
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

        total_appts = 0
        total_min = 0.0
        unique_names = 0

        for company_data in groups.values():
            total_appts += company_data["total_appointments"]
            total_min += company_data["total_minutes"]
            unique_names += len(company_data["clients"])

        results.append(
            _build_file_result(path.name, groups, total_appts, total_min, unique_names)
        )
        print(
            f"✅  {total_appts} appointments, {unique_names} names, {format_duration(total_min)}"
        )

    return results


def _build_file_result(
    filename: str,
    groups: dict[str, dict],
    total_appts: int,
    total_min: float,
    unique_names: int | None = None,
) -> dict:
    first_group = next(iter(groups.values())) if groups else None
    is_company_grouped = (
        first_group
        and "clients" in first_group
        and first_group.get("display_name") != "All Clients"
    )

    if is_company_grouped:
        groups_list = []
        for company_name, company_data in groups.items():
            company_info = {
                "name": company_data["display_name"],
                "is_company": True,
                "total_minutes": company_data["total_minutes"],
                "total_duration": format_duration(company_data["total_minutes"]),
                "total_appointments": company_data["total_appointments"],
                "clients": [],
            }

            for client_data in company_data["clients"].values():
                company_info["clients"].append(
                    {
                        "name": client_data["display_name"],
                        "count": client_data["total_appointments"],
                        "total_minutes": client_data["total_minutes"],
                        "total_duration": client_data["total_duration"],
                        "appointments": client_data["appointments"],
                    }
                )

            groups_list.append(company_info)

        return {
            "filename": filename,
            "total_appointments": total_appts,
            "total_minutes": total_min,
            "total_duration": format_duration(total_min),
            "unique_names": unique_names or total_appts,
            "is_company_grouped": True,
            "groups": groups_list,
        }
    else:
        actual_groups = groups
        if "__ungrouped__" in groups:
            actual_groups = groups["__ungrouped__"]["clients"]

        return {
            "filename": filename,
            "total_appointments": total_appts,
            "total_minutes": total_min,
            "total_duration": format_duration(total_min),
            "unique_names": unique_names or len(actual_groups),
            "is_company_grouped": False,
            "groups": (
                [
                    {
                        "name": g["display_name"],
                        "count": len(g["appointments"]),
                        "total_minutes": g["total_minutes"],
                        "total_duration": format_duration(g["total_minutes"]),
                        "appointments": g["appointments"],
                    }
                    for g in actual_groups.values()
                ]
                if isinstance(actual_groups, dict)
                else []
            ),
        }


# ---------------------------------------------------------------------------
# HTML rendering helpers (with company support and config integration)
# ---------------------------------------------------------------------------


def render_bar_chart(groups: list[dict]) -> str:
    if not groups:
        return ""

    if groups and groups[0].get("is_company"):
        max_min = max(g["total_minutes"] for g in groups) or 1
        rows = []
        for g in groups:
            pct = round(g["total_minutes"] / max_min * 100, 1)
            label = g["name"][:22] + "…" if len(g["name"]) > 22 else g["name"]
            rows.append(
                f'<div class="bar-row bar-row-company" data-minutes="{g["total_minutes"]}" data-name="{g["name"]}">'
                f'<span class="bar-label bar-label-company" title="{g["name"]}">🏢 {label}</span>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>'
                f'<span class="bar-value">{g["total_duration"]}</span>'
                f"</div>"
            )
    else:
        max_min = max(g["total_minutes"] for g in groups) or 1
        rows = []
        for g in groups:
            pct = round(g["total_minutes"] / max_min * 100, 1)
            label = g["name"][:22] + "…" if len(g["name"]) > 22 else g["name"]
            rows.append(
                f'<div class="bar-row" data-minutes="{g["total_minutes"]}" data-name="{g["name"]}">'
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
        "<tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


def get_invoice_settings_for_client(client_name: str, company_name: str = None) -> dict:
    """
    Get invoice settings for a client, merging:
    1. Global defaults
    2. Company-specific settings (if company_name provided)
    3. Client-specific settings
    """
    config = load_config()

    settings = config.get("invoice_defaults", {}).get("defaults", {}).copy()
    from_defaults = config.get("invoice_defaults", {}).get("from", {}).copy()

    if company_name:
        company_settings = config.get("company_settings", {}).get(company_name, {})
        if "defaults" in company_settings:
            settings.update(company_settings["defaults"])
        if "from" in company_settings:
            from_defaults.update(company_settings["from"])

    client_settings = config.get("client_settings", {}).get(client_name, {})
    if "defaults" in client_settings:
        settings.update(client_settings["defaults"])

    return {"from": from_defaults, "defaults": settings}


def render_file_section(file_result: dict) -> str:
    is_company_grouped = file_result.get("is_company_grouped", False)
    config = load_config()
    report_settings = config.get("report_settings", {})
    collapse_companies = report_settings.get("collapse_companies_by_default", False)
    collapse_clients = report_settings.get("collapse_clients_by_default", True)

    if is_company_grouped:
        name_groups_html = ""
        for company in file_result["groups"]:
            company_clients_html = ""
            for client in company["clients"]:
                table = render_appointments_table(client["appointments"])

                invoice_payload = {
                    "groupName": client["name"],
                    "totalMinutes": client["total_minutes"],
                    "totalDuration": client["total_duration"],
                    "sourceFile": file_result["filename"],
                    "appointments": [
                        {
                            "subject": a["subject"],
                            "start": format_dt(a["start"]),
                            "end": format_dt(a["end"]),
                            "duration": a["duration"],
                        }
                        for a in client["appointments"]
                    ],
                }
                client_settings = get_invoice_settings_for_client(
                    client["name"], company["name"]
                )
                invoice_payload["settings"] = client_settings

                payload_b64 = encode_to_base64(invoice_payload)

                safe_name = client["name"].replace("'", "&#39;").replace('"', "&quot;")
                client_collapse_style = "display:none" if collapse_clients else ""

                company_clients_html += (
                    f'<div class="name-group" data-name="{safe_name}" data-company="{company["name"]}">'
                    f'<div class="name-header">'
                    f'<span class="name-chevron-wrap">'
                    f'<span class="name-title">{client["name"]}</span>'
                    f'<span class="name-chevron">▾</span>'
                    f"</span>"
                    f'<span class="name-meta">'
                    f'<span class="badge badge-blue">{client["count"]} appt{"s" if client["count"] != 1 else ""}</span>'
                    f'<span class="badge badge-green">{client["total_duration"]}</span>'
                    f'<button class="btn-invoice" onclick="openInvoiceModal(\'{payload_b64}\')" '
                    f'title="Generate invoice for {safe_name}">🧾 Invoice</button>'
                    f"</span>"
                    f"</div>"
                    f'<div class="name-body" style="{client_collapse_style}">{table}</div>'
                    f"</div>"
                )

            company_payload = {
                "groupName": company["name"],
                "totalMinutes": company["total_minutes"],
                "totalDuration": company["total_duration"],
                "sourceFile": file_result["filename"],
                "isConsolidated": True,
                "clients": [
                    {
                        "name": c["name"],
                        "totalMinutes": c["total_minutes"],
                        "totalDuration": c["total_duration"],
                        "appointments": [
                            {
                                "subject": a["subject"],
                                "start": format_dt(a["start"]),
                                "end": format_dt(a["end"]),
                                "duration": a["duration"],
                            }
                            for a in c["appointments"]
                        ],
                    }
                    for c in company["clients"]
                ],
            }
            company_settings = get_invoice_settings_for_client(
                company["name"], company["name"]
            )
            company_payload["settings"] = company_settings

            company_payload_b64 = encode_to_base64(company_payload)

            safe_company_name = (
                company["name"].replace("'", "&#39;").replace('"', "&quot;")
            )
            company_collapse_style = "display:none" if collapse_companies else ""

            name_groups_html += (
                f'<div class="company-group" data-company="{safe_company_name}">'
                f'<div class="company-header">'
                f'<span class="company-chevron-wrap">'
                f'<span class="company-icon">🏢</span>'
                f'<span class="company-title">{company["name"]}</span>'
                f'<span class="company-chevron">▾</span>'
                f"</span>"
                f'<span class="company-meta">'
                f'<span class="badge badge-blue">{company["total_appointments"]} appointments</span>'
                f'<span class="badge badge-amber">{len(company["clients"])} clients</span>'
                f'<span class="badge badge-green">{company["total_duration"]}</span>'
                f'<button class="btn-invoice btn-company-invoice" onclick="openCompanyInvoiceModal(\'{company_payload_b64}\')" '
                f'title="Generate consolidated invoice for {safe_company_name}">🧾 Company Invoice</button>'
                f"</span>"
                f"</div>"
                f'<div class="company-body" style="{company_collapse_style}">'
                f'<div class="company-clients">{company_clients_html}</div>'
                f"</div>"
                f"</div>"
            )
    else:
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
            client_settings = get_invoice_settings_for_client(g["name"], None)
            invoice_payload["settings"] = client_settings

            payload_b64 = encode_to_base64(invoice_payload)

            safe_name = g["name"].replace("'", "&#39;").replace('"', "&quot;")
            client_collapse_style = "display:none" if collapse_clients else ""

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
                f'<div class="name-body" style="{client_collapse_style}">{table}</div>'
                f"</div>"
            )

    chart = (
        render_bar_chart(file_result["groups"])
        if report_settings.get("show_bar_chart", True)
        else ""
    )
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
# Full HTML template (with config integration)
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
.bar-row-company{{margin-top:16px;border-top:1px solid var(--border);padding-top:10px}}
.bar-label{{font-size:12px;color:var(--text2);width:130px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0}}
.bar-label-company{{font-weight:600;color:var(--accent-text)}}
.bar-track{{flex:1;background:var(--surface3);border-radius:4px;height:7px;overflow:hidden}}
.bar-fill{{height:100%;background:var(--accent);border-radius:4px;transition:width 0.6s cubic-bezier(.16,1,.3,1)}}
.bar-value{{font-size:12px;font-weight:600;color:var(--text2);width:52px;text-align:right;flex-shrink:0}}

/* ── Company group ────────────────────────────────────────────────── */
.company-group{{
  border:1px solid var(--border);
  border-radius:var(--radius);
  margin-top:16px;
  overflow:hidden;
  background:var(--surface2);
}}
.company-header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 16px;
  background:var(--surface);
  cursor:pointer;
  gap:12px;
  border-bottom:0.5px solid var(--border);
}}
.company-header:hover{{background:var(--surface2)}}
.company-chevron-wrap{{
  display:flex;align-items:center;gap:8px;
  flex:1;cursor:pointer;
}}
.company-icon{{font-size:16px}}
.company-title{{font-size:15px;font-weight:600}}
.company-chevron{{font-size:11px;color:var(--text2);transition:transform 0.2s}}
.company-meta{{display:flex;align-items:center;gap:8px;flex-shrink:0;flex-wrap:wrap}}
.company-body{{padding:12px 16px}}
.company-clients{{
  display:flex;flex-direction:column;gap:12px;
}}

/* ── Name group ───────────────────────────────────────────────────── */
.name-group{{
  border:0.5px solid var(--border);
  border-radius:var(--radius-sm);
  overflow:hidden;
}}
.name-header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:9px 13px;background:var(--surface2);gap:8px;
}}
.name-title{{font-weight:500;font-size:13px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.name-meta{{display:flex;align-items:center;gap:6px;flex-shrink:0}}
.name-chevron-wrap{{cursor:pointer;display:flex;align-items:center;gap:6px;flex:1;min-width:0}}
.name-chevron{{font-size:11px;color:var(--text2);transition:transform 0.2s;flex-shrink:0}}
.name-body{{}}

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
.btn-company-invoice{{
  background:var(--accent-bg);
  color:var(--accent-text);
  border-color:var(--accent-border);
}}
.btn-company-invoice:hover{{
  background:var(--accent);
  color:#fff;
}}

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
  .company-meta .badge-amber{{display:none}}
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
      placeholder="Search by client or company name…" autocomplete="off" spellcheck="false">
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
        <div class="form-group">
          <label>Phone</label>
          <input type="text" id="from_phone" placeholder="+34 900 123 456">
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

      <div class="form-section-title">Rate</div>
      <div class="form-grid">
        <div class="form-group">
          <label>Hourly rate (€)</label>
          <input type="number" id="inv_rate" placeholder="75.00" min="0" step="0.01">
        </div>
        <div class="form-group full">
          <label>Bank account</label>
          <input type="text" id="inv_bank" placeholder="ES00 0000 0000 0000 0000 0000">
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
const PROVIDER_FIELDS = ['from_name','from_tax','from_email','from_phone','from_address'];
const CLIENT_FIELDS = ['to_name','to_tax','to_email','to_address'];

// Helper function to decode base64 with UTF-8 support
function decodeBase64(b64Str) {{
  try {{
    const binary = atob(b64Str);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {{
      bytes[i] = binary.charCodeAt(i);
    }}
    const decoded = new TextDecoder('utf-8').decode(bytes);
    return JSON.parse(decoded);
  }} catch(e) {{
    console.error('Failed to decode base64:', e);
    return JSON.parse(atob(b64Str));
  }}
}}

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

document.querySelectorAll('.company-header').forEach(h => {{
  h.addEventListener('click', (e) => {{
    if (e.target.closest('.btn-invoice')) return;
    const body = h.nextElementSibling;
    const chevron = h.querySelector('.company-chevron');
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    chevron.style.transform = open ? 'rotate(-90deg)' : '';
  }});
}});

document.querySelectorAll('.company-chevron-wrap').forEach(wrap => {{
  wrap.addEventListener('click', () => {{
    const company = wrap.closest('.company-group');
    const body = company.querySelector('.company-body');
    const chevron = wrap.querySelector('.company-chevron');
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

  let anyVisibleTotal = 0;

  document.querySelectorAll('.file-section').forEach(section => {{
    const companies = Array.from(section.querySelectorAll('.company-group'));
    const flatGroups = Array.from(section.querySelectorAll('.name-group')).filter(g => !g.closest('.company-group'));

    let sectionVisible = false;

    companies.forEach(company => {{
      const companyName = company.dataset.company?.toLowerCase() || '';
      const clients = Array.from(company.querySelectorAll('.name-group'));
      let companyHasMatch = false;

      clients.forEach(client => {{
        const clientName = client.dataset.name?.toLowerCase() || '';
        const match = !q || companyName.includes(q) || clientName.includes(q);
        client.style.display = match ? '' : 'none';
        if (match) companyHasMatch = true;
      }});

      company.style.display = companyHasMatch ? '' : 'none';
      if (companyHasMatch) {{
        sectionVisible = true;
        anyVisibleTotal++;
      }}
    }});

    flatGroups.forEach(g => {{
      const name = (g.dataset.name || '').toLowerCase();
      const match = !q || name.includes(q);
      g.style.display = match ? '' : 'none';
      if (match) {{
        sectionVisible = true;
        anyVisibleTotal++;
      }}
    }});

    section.style.display = sectionVisible ? '' : 'none';
  }});

  noResults.classList.toggle('visible', anyVisibleTotal === 0 && q.length > 0);
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
  const invoiceBtn = g.querySelector('.btn-invoice');
  if (invoiceBtn && greenBadge) {{
    try {{
      const match = invoiceBtn.getAttribute('onclick').match(/'([^']+)'/);
      if (match) {{
        const payload = decodeBase64(match[1]);
        greenBadge.dataset.min = payload.totalMinutes;
      }}
    }} catch(e) {{}}
  }}
  if (blueBadge) {{
    const m = blueBadge.textContent.match(/\\d+/);
    if (m) blueBadge.dataset.count = m[0];
  }}
}});

// ── Invoice modal ──────────────────────────────────────────────────────────
let _invoiceData = null;
let _isConsolidated = false;

function applySettingsToForm(settings) {{
  if (settings && settings.from) {{
    if (settings.from.name) document.getElementById('from_name').value = settings.from.name;
    if (settings.from.tax_id) document.getElementById('from_tax').value = settings.from.tax_id;
    if (settings.from.email) document.getElementById('from_email').value = settings.from.email;
    if (settings.from.phone) document.getElementById('from_phone').value = settings.from.phone;
    if (settings.from.address) document.getElementById('from_address').value = settings.from.address;
    if (settings.from.bank_account) document.getElementById('inv_bank').value = settings.from.bank_account;
  }}
  if (settings && settings.defaults) {{
    if (settings.defaults.hourly_rate) document.getElementById('inv_rate').value = settings.defaults.hourly_rate;
    if (settings.defaults.payment_terms) {{
      const notes = document.getElementById('inv_notes').value;
      if (!notes.includes(settings.defaults.payment_terms)) {{
        document.getElementById('inv_notes').value = settings.defaults.payment_terms + '\\n\\n' + notes;
      }}
    }}
    if (settings.defaults.notes) {{
      const notes = document.getElementById('inv_notes').value;
      if (!notes.includes(settings.defaults.notes)) {{
        document.getElementById('inv_notes').value = settings.defaults.notes + '\\n\\n' + notes;
      }}
    }}
  }}
}}

function openInvoiceModal(dataB64) {{
  _invoiceData = decodeBase64(dataB64);
  _isConsolidated = false;
  loadProvider();

  if (_invoiceData.settings) {{
    applySettingsToForm(_invoiceData.settings);
  }}

  document.getElementById('to_name').value = _invoiceData.groupName;
  document.getElementById('to_tax').value  = '';
  document.getElementById('to_email').value = '';
  document.getElementById('to_address').value = '';

  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  document.getElementById('inv_number').value =
    'INV-' + now.getFullYear() + '-' + pad(now.getMonth()+1) + pad(now.getDate()) + '-' + pad(now.getHours()) + pad(now.getMinutes());
  document.getElementById('inv_date').value = now.toISOString().slice(0,10);
  const due = new Date(now); due.setDate(due.getDate()+30);
  document.getElementById('inv_due').value = due.toISOString().slice(0,10);
  document.getElementById('invoiceModal').classList.add('open');
}}

function openCompanyInvoiceModal(dataB64) {{
  _invoiceData = decodeBase64(dataB64);
  _isConsolidated = true;
  loadProvider();

  if (_invoiceData.settings) {{
    applySettingsToForm(_invoiceData.settings);
  }}

  document.getElementById('to_name').value = _invoiceData.groupName;
  document.getElementById('to_tax').value  = '';
  document.getElementById('to_email').value = '';
  document.getElementById('to_address').value = '';

  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  document.getElementById('inv_number').value =
    'INV-' + now.getFullYear() + '-' + pad(now.getMonth()+1) + pad(now.getDate()) + '-' + pad(now.getHours()) + pad(now.getMinutes());
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
  const rate    = parseFloat(document.getElementById('inv_rate').value) || 0;
  const hours   = d.totalMinutes / 60;
  const total   = hours * rate;
  const fmt     = n => n.toLocaleString('es-ES',{{minimumFractionDigits:2,maximumFractionDigits:2}});
  const fmtDate = s => s ? new Date(s).toLocaleDateString('es-ES') : '—';

  let rows = '';

  if (_isConsolidated && d.clients) {{
    for (const client of d.clients) {{
      rows += `<tr style="background:#f5f5f3;"><td colspan="3"><strong>🏢 ${{client.name}}</strong></td><td style="text-align:right"><strong>${{client.totalDuration}}</strong></td></tr>`;
      for (const apt of client.appointments) {{
        rows += `<tr>
          <td style="padding-left:24px">${{apt.subject}}</td>
          <td>${{apt.start}}</td>
          <td>${{apt.end}}</td>
          <td style="text-align:right">${{apt.duration}}</td>
        </tr>`;
      }}
    }}
  }} else {{
    rows = d.appointments.map(a =>
      `<tr>
        <td>${{a.subject}}</td>
        <td>${{a.start}}</td>
        <td>${{a.end}}</td>
        <td style="text-align:right">${{a.duration}}</td>
      </tr>`
    ).join('');
  }}

  const notes = document.getElementById('inv_notes').value;
  const bankAccount = document.getElementById('inv_bank').value;
  const invoiceType = _isConsolidated ? 'Consolidated Invoice' : 'Invoice';
  const fromPhone = document.getElementById('from_phone').value;
  const fromEmail = document.getElementById('from_email').value;
  const fromTax = document.getElementById('from_tax').value;
  const fromAddress = document.getElementById('from_address').value;
  const fromName = document.getElementById('from_name').value;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>${{invoiceType}} ${{document.getElementById('inv_number').value}}</title>
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
.bank-details{{margin-top:12px;padding:12px;background:#f5f5f3;border-radius:8px;font-size:11px}}
@media print{{body{{padding:32px}}@page{{margin:0.8cm}}}}
</style>
</head>
<body>
<div class="inv-header">
  <div>
    <div class="inv-title">${{invoiceType}}</div>
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
    <div class="party-name">${{fromName || '—'}}</div>
    <div class="party-detail">
      ${{fromTax ? 'NIF: ' + fromTax + '<br>' : ''}}
      ${{fromEmail || ''}}
      ${{fromPhone ? '<br>Tel: ' + fromPhone : ''}}
      ${{fromAddress ? '<br>' + fromAddress : ''}}
    </div>
  </div>
  <div>
    <div class="party-label">Bill to</div>
    <div class="party-name">${{document.getElementById('to_name').value || '—'}}</div>
    <div class="party-detail">
      ${{document.getElementById('to_tax').value ? 'NIF: ' + document.getElementById('to_tax').value + '<br>' : ''}}
      ${{document.getElementById('to_email').value || ''}}
      ${{document.getElementById('to_address').value ? '<br>' + document.getElementById('to_address').value : ''}}
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
  <div class="total-row grand"><span>Total</span><span>${{fmt(total)}} €</span></div>
</div>
${{notes ? '<div class="notes"><strong>Notes</strong>' + notes.replace(/\\n/g,'<br>') + '</div>' : ''}}
${{bankAccount ? '<div class="bank-details"><strong>Bank account:</strong> ' + bankAccount + '</div>' : ''}}
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
