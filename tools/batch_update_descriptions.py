"""Batch update Notion page description fields with safety-first defaults.

This tool reads a CSV/JSON input file and applies (or previews) updates to a
single rich_text property for many Notion pages.

Default behavior is dry-run. Use --apply for live writes.

Input rows must include:
- one page locator: page_id OR page_url (url)
- one description field: description OR new_description OR content

Usage examples:
    python tools/batch_update_descriptions.py --input .tmp/reports/description-updates.csv
    python tools/batch_update_descriptions.py --input .tmp/reports/description-updates.json --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
PAGE_ID_PATTERN = re.compile(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
MAX_TEXT_LENGTH = 2000


@dataclass
class UpdateRow:
    row_number: int
    page_id: str
    description: str
    source: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch update Notion description fields")
    parser.add_argument("--input", required=True, help="Path to CSV or JSON input file")
    parser.add_argument(
        "--property-name",
        default="description",
        help="Notion rich_text property name to update (default: description)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply live updates. If omitted, runs in dry-run mode.",
    )
    parser.add_argument(
        "--audit-file",
        default=".tmp/reports/mutation-audit.jsonl",
        help="JSONL audit output path",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum retries per API call for retryable errors",
    )
    parser.add_argument(
        "--rate-limit-rps",
        type=float,
        default=3.0,
        help="Maximum request rate in requests per second (default 3.0)",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing rows after non-retryable write errors",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional cap for rows processed (useful for staged rollout)",
    )
    return parser.parse_args()


def get_notion_token() -> str:
    token = os.getenv("NOTION_API_KEY")
    if token:
        return token
    token = os.getenv("NOTION_TOKEN")
    if token:
        return token
    raise ValueError("No Notion token found. Set NOTION_API_KEY (preferred) or NOTION_TOKEN.")


def resolve_property_name(properties: dict[str, Any], property_name: str) -> str | None:
    if property_name in properties:
        return property_name
    lowered = {key.lower(): key for key in properties.keys()}
    return lowered.get(property_name.lower())


def normalize_page_id(raw_value: str) -> str | None:
    match = PAGE_ID_PATTERN.search(raw_value or "")
    if not match:
        return None
    page_id = match.group(1).lower().replace("-", "")
    if len(page_id) != 32:
        return None
    return f"{page_id[0:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:32]}"


def extract_text_from_property(property_obj: Any) -> str:
    if not isinstance(property_obj, dict):
        return ""
    if property_obj.get("type") != "rich_text":
        return ""
    rich_text = property_obj.get("rich_text", [])
    if not isinstance(rich_text, list):
        return ""
    return "".join(str(part.get("plain_text", "")) for part in rich_text if isinstance(part, dict))


def load_input_rows(input_path: Path) -> list[dict[str, Any]]:
    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]
    if suffix == ".json":
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JSON input must be an array of objects")
        return [dict(row) for row in payload if isinstance(row, dict)]
    raise ValueError("Input file must be .csv or .json")


def validate_rows(raw_rows: list[dict[str, Any]], max_rows: int | None = None) -> tuple[list[UpdateRow], list[dict[str, Any]]]:
    valid_rows: list[UpdateRow] = []
    errors: list[dict[str, Any]] = []

    for index, row in enumerate(raw_rows, start=1):
        if max_rows is not None and len(valid_rows) >= max_rows:
            break

        page_value = (
            str(row.get("page_id") or "").strip()
            or str(row.get("page_url") or "").strip()
            or str(row.get("url") or "").strip()
            or str(row.get("id") or "").strip()
        )
        description = (
            str(row.get("description") or "").strip()
            or str(row.get("new_description") or "").strip()
            or str(row.get("content") or "").strip()
        )

        page_id = normalize_page_id(page_value)
        if not page_id:
            errors.append({"row": index, "code": "invalid_page", "message": "missing/invalid page_id or page_url"})
            continue
        if not description:
            errors.append({"row": index, "code": "missing_description", "message": "description value is required"})
            continue
        if len(description) > MAX_TEXT_LENGTH:
            errors.append(
                {
                    "row": index,
                    "code": "description_too_long",
                    "message": f"description exceeds {MAX_TEXT_LENGTH} characters",
                }
            )
            continue

        valid_rows.append(
            UpdateRow(
                row_number=index,
                page_id=page_id,
                description=description,
                source=row,
            )
        )

    return valid_rows, errors


def build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def request_with_retry(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    max_retries: int,
    rate_limit_interval: float,
    limiter_state: dict[str, float],
) -> requests.Response:
    if requests is None:
        raise RuntimeError("requests library required. Install dependencies from requirements.txt")

    for attempt in range(max_retries):
        now = time.perf_counter()
        elapsed = now - limiter_state.get("last_request", 0.0)
        sleep_for = rate_limit_interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)

        try:
            response = requests.request(method, url, headers=headers, json=payload, timeout=30)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt + 1 >= max_retries:
                raise RuntimeError(f"network error: {exc}") from exc
            time.sleep(2**attempt)
            continue

        limiter_state["last_request"] = time.perf_counter()

        if response.status_code == 429:
            if attempt + 1 >= max_retries:
                return response
            retry_after = response.headers.get("Retry-After", "1")
            try:
                wait_seconds = max(float(retry_after), 1.0)
            except ValueError:
                wait_seconds = 1.0
            time.sleep(wait_seconds)
            continue

        if response.status_code >= 500 and attempt + 1 < max_retries:
            time.sleep(2**attempt)
            continue

        return response

    raise RuntimeError("unexpected retry loop termination")


def append_audit_line(audit_path: Path, payload: dict[str, Any]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"ok": False, "error": f"input file not found: {input_path}"}))
        return 1

    if args.max_retries < 1:
        print(json.dumps({"ok": False, "error": "max-retries must be >= 1"}))
        return 2
    if args.rate_limit_rps <= 0:
        print(json.dumps({"ok": False, "error": "rate-limit-rps must be > 0"}))
        return 2

    try:
        token = get_notion_token()
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    try:
        raw_rows = load_input_rows(input_path)
        valid_rows, validation_errors = validate_rows(raw_rows, max_rows=args.max_rows)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"failed to load input: {exc}"}))
        return 1

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    mode = "apply" if args.apply else "dry-run"
    headers = build_headers(token)
    rate_limit_interval = 1.0 / args.rate_limit_rps
    limiter_state = {"last_request": 0.0}

    stats = {
        "rows_total": len(raw_rows),
        "rows_valid": len(valid_rows),
        "rows_invalid": len(validation_errors),
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }

    for err in validation_errors:
        append_audit_line(
            Path(args.audit_file),
            {
                "timestamp": started_at,
                "run_id": run_id,
                "mode": mode,
                "row": err["row"],
                "status": "validation_error",
                "code": err["code"],
                "message": err["message"],
            },
        )

    for row in valid_rows:
        stats["processed"] += 1
        get_url = f"{NOTION_API_BASE}/pages/{row.page_id}"
        record_base = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "mode": mode,
            "row": row.row_number,
            "page_id": row.page_id,
            "property_name": args.property_name,
            "new_description": row.description,
        }

        try:
            get_response = request_with_retry(
                method="GET",
                url=get_url,
                headers=headers,
                payload=None,
                max_retries=args.max_retries,
                rate_limit_interval=rate_limit_interval,
                limiter_state=limiter_state,
            )
        except Exception as exc:
            stats["failed"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "failed",
                    "code": "read_error",
                    "message": str(exc),
                },
            )
            if not args.continue_on_error:
                break
            continue

        if get_response.status_code >= 400:
            stats["failed"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "failed",
                    "code": "read_http_error",
                    "http_status": get_response.status_code,
                    "message": get_response.text[:500],
                },
            )
            if not args.continue_on_error:
                break
            continue

        page_obj = get_response.json()
        properties = page_obj.get("properties", {}) if isinstance(page_obj, dict) else {}
        resolved_property_name = resolve_property_name(properties, args.property_name)
        prop_obj = properties.get(resolved_property_name) if resolved_property_name else None

        if not isinstance(prop_obj, dict):
            stats["failed"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "failed",
                    "code": "missing_property",
                    "message": f"property '{args.property_name}' not found on page",
                },
            )
            if not args.continue_on_error:
                break
            continue

        prop_type = prop_obj.get("type")
        if prop_type != "rich_text":
            stats["failed"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "failed",
                    "code": "invalid_property_type",
                    "message": f"property '{args.property_name}' must be rich_text (found {prop_type})",
                },
            )
            if not args.continue_on_error:
                break
            continue

        old_description = extract_text_from_property(prop_obj)

        if old_description == row.description:
            stats["skipped"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "skipped",
                    "code": "no_change",
                    "old_description": old_description,
                },
            )
            continue

        if not args.apply:
            stats["updated"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "dry_run_ready",
                    "old_description": old_description,
                },
            )
            continue

        patch_payload = {
            "properties": {
                args.property_name: {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": row.description},
                        }
                    ]
                }
            }
        }

        try:
            patch_response = request_with_retry(
                method="PATCH",
                url=get_url,
                headers=headers,
                payload=patch_payload,
                max_retries=args.max_retries,
                rate_limit_interval=rate_limit_interval,
                limiter_state=limiter_state,
            )
        except Exception as exc:
            stats["failed"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "failed",
                    "code": "write_error",
                    "old_description": old_description,
                    "message": str(exc),
                },
            )
            if not args.continue_on_error:
                break
            continue

        if patch_response.status_code >= 400:
            stats["failed"] += 1
            append_audit_line(
                Path(args.audit_file),
                {
                    **record_base,
                    "status": "failed",
                    "code": "write_http_error",
                    "http_status": patch_response.status_code,
                    "old_description": old_description,
                    "message": patch_response.text[:500],
                },
            )
            if not args.continue_on_error:
                break
            continue

        stats["updated"] += 1
        append_audit_line(
            Path(args.audit_file),
            {
                **record_base,
                "status": "updated",
                "old_description": old_description,
            },
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    result = {
        "ok": stats["failed"] == 0,
        "mode": mode,
        "run_id": run_id,
        "input": str(input_path),
        "property_name": args.property_name,
        "audit_file": args.audit_file,
        "started_at": started_at,
        "finished_at": finished_at,
        "stats": stats,
    }
    print(json.dumps(result))
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
