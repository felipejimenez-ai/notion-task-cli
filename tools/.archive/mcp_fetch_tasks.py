"""Normalize MCP task page exports into tasks-source JSON.

This tool does not call MCP directly. Instead, it transforms saved MCP page payloads
into the normalized source schema consumed by organize_notion_tasks.py.

Supported input formats:
1) JSON array of page payload objects returned by Notion MCP fetch tool.
2) JSON array of objects with a top-level "properties" object.
3) JSON array of already-normalized task objects.

Usage:
    python tools/mcp_fetch_tasks.py --input .tmp/reports/mcp-pages.json --output .tmp/reports/tasks-source.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROPERTIES_BLOCK_PATTERN = re.compile(r"<properties>\s*(\{.*?\})\s*</properties>", re.DOTALL)


@dataclass(frozen=True)
class NormalizedTask:
    name: str
    url: str
    status: str
    description: str | None
    project: str | None
    impact: str | None
    category: str | None
    deadline: str | None
    today: str
    sequence: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize MCP task payloads")
    parser.add_argument("--input", required=True, help="Path to MCP payload JSON file")
    parser.add_argument(
        "--output",
        default=".tmp/reports/tasks-source.json",
        help="Path to write normalized tasks JSON",
    )
    parser.add_argument(
        "--exclude-status",
        action="append",
        default=["done"],
        help="Status to exclude (repeatable). Default: done",
    )
    parser.add_argument(
        "--cache-file",
        default=".tmp/reports/tasks-cache.json",
        help="Path to cache metadata used for delta mode",
    )
    parser.add_argument(
        "--delta",
        action="store_true",
        help="Enable cache-aware delta mode. If normalized source is unchanged, skip rewriting output.",
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Ignore cache and force writing normalized output.",
    )
    return parser.parse_args()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _get_property_value(properties: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in properties:
            return properties.get(name)
    lowered = {key.lower(): key for key in properties.keys()}
    for name in names:
        actual_key = lowered.get(name.lower())
        if actual_key is not None:
            return properties.get(actual_key)
    return None


def _normalize_due(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _normalize_status(value: Any) -> str:
    status = str(value or "to-do").strip().lower()
    return status or "to-do"


def _normalize_today(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "__YES__" if text in {"__yes__", "yes", "true", "1"} else "__NO__"


def _normalize_sequence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_properties_from_text(text: str) -> dict[str, Any] | None:
    match = PROPERTIES_BLOCK_PATTERN.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_notion_api_value(prop_obj: Any) -> Any:
    """Extract value from Notion API property object format."""
    if not isinstance(prop_obj, dict):
        return None
    
    prop_type = prop_obj.get("type")
    if prop_type == "title":
        title_list = prop_obj.get("title", [])
        return "".join([t.get("plain_text", "") for t in title_list])
    elif prop_type == "rich_text":
        text_list = prop_obj.get("rich_text", [])
        return "".join([t.get("plain_text", "") for t in text_list])
    elif prop_type == "select":
        select_obj = prop_obj.get("select", {})
        return select_obj.get("name") if select_obj else None
    elif prop_type == "status":
        status_obj = prop_obj.get("status", {})
        return status_obj.get("name") if status_obj else None
    elif prop_type == "checkbox":
        return prop_obj.get("checkbox")
    elif prop_type == "date":
        date_obj = prop_obj.get("date", {})
        return date_obj.get("start") if date_obj else None
    elif prop_type == "number":
        return prop_obj.get("number")
    elif prop_type == "relation":
        relation_list = prop_obj.get("relation", [])
        if not isinstance(relation_list, list):
            return None
        related_ids = [str(item.get("id", "")).strip() for item in relation_list if isinstance(item, dict) and item.get("id")]
        return ", ".join(related_ids) if related_ids else None
    else:
        # For unknown types, return None
        return None


def _extract_properties(item: dict[str, Any]) -> dict[str, Any] | None:
    # Already normalized or direct properties-like shape.
    if "name" in item and ("status" in item or "today" in item):
        return item

    properties = item.get("properties")
    if not isinstance(properties, dict):
        text = item.get("text")
        if isinstance(text, str):
            return _extract_properties_from_text(text)
        return None
    
    # Check if this is Notion API format (properties contain type/value objects)
    # vs flat format (properties contain direct values)
    has_type_fields = any(isinstance(v, dict) and "type" in v for v in properties.values())
    
    if has_type_fields:
        # Notion API format: convert each property using _extract_notion_api_value
        return {
            key: _extract_notion_api_value(value)
            for key, value in properties.items()
        }
    else:
        # Already flat format
        return properties


def _normalize_task(properties: dict[str, Any]) -> NormalizedTask | None:
    name = _clean_text(properties.get("name"))
    if not name:
        return None

    return NormalizedTask(
        name=name,
        url=str(properties.get("url", "")).strip(),
        status=_normalize_status(properties.get("status")),
        description=_clean_text(_get_property_value(properties, "description", "Description")) or None,
        project=_clean_text(properties.get("project")) or None,
        impact=_clean_text(properties.get("impact")).lower() or None,
        category=_clean_text(properties.get("category")) or None,
        deadline=_normalize_due(
            properties.get("deadline") or properties.get("due") or properties.get("date:deadline:start") or properties.get("date:deadine:start")
        ),
        today=_normalize_today(properties.get("today")),
        sequence=_normalize_sequence(properties.get("sequence")),
    )


def normalize_payload(payload: Any, excluded_statuses: set[str]) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Input JSON must be an array")

    normalized: list[NormalizedTask] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Item at index {index} is not an object")

        properties = _extract_properties(item)
        if not properties:
            continue

        task = _normalize_task(properties)
        if not task:
            continue

        if task.status in excluded_statuses:
            continue

        normalized.append(task)

    # Deterministic de-dup by URL if present, else by name.
    seen: set[str] = set()
    unique: list[NormalizedTask] = []
    for task in sorted(normalized, key=lambda x: (x.name.lower(), x.url)):
        key = task.url or task.name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(task)

    return [
        {
            "name": task.name,
            "url": task.url,
            "status": task.status,
            **({"description": task.description} if task.description else {}),
            **({"project": task.project} if task.project else {}),
            **({"impact": task.impact} if task.impact else {}),
            **({"category": task.category} if task.category else {}),
            **({"deadline": task.deadline} if task.deadline else {}),
            "today": task.today,
            **({"sequence": task.sequence} if task.sequence is not None else {}),
        }
        for task in unique
    ]


def _task_key(task: dict[str, Any]) -> str:
    return str(task.get("url") or task.get("name") or "").strip().lower()


def _task_fingerprint(task: dict[str, Any]) -> str:
    serialized = json.dumps(task, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _source_fingerprint(tasks: list[dict[str, Any]]) -> str:
    keyed = sorted(((_task_key(task), _task_fingerprint(task)) for task in tasks), key=lambda x: x[0])
    serialized = json.dumps(keyed, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"cache file is not valid JSON: {path} ({exc.msg})") from exc
    except OSError as exc:
        raise ValueError(f"cache file cannot be read: {path} ({exc})") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"cache file must contain a JSON object: {path}")
    return payload


def _write_cache(path: Path, source_fp: str, tasks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    task_map = {}
    for task in tasks:
        key = _task_key(task)
        if not key:
            continue
        task_map[key] = _task_fingerprint(task)

    cache = {
        "version": 1,
        "source_fingerprint": source_fp,
        "task_fingerprints": task_map,
        "tasks": len(tasks),
    }
    path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"ok": False, "error": f"input file not found: {input_path}"}))
        return 1

    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        excluded = {status.strip().lower() for status in args.exclude_status if status.strip()}
        tasks = normalize_payload(payload, excluded)
        source_fp = _source_fingerprint(tasks)

        cache_path = Path(args.cache_file)
        cache_payload = _load_cache(cache_path)
        previous_source_fp = str(cache_payload.get("source_fingerprint", ""))

        changed = True
        if args.delta and not args.force_full and previous_source_fp == source_fp:
            changed = False

        output_path = Path(args.output)
        if changed or not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")

        if args.delta or args.force_full:
            _write_cache(cache_path, source_fp, tasks)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "input": str(input_path),
                "output": str(args.output),
                "excluded_status": sorted(excluded),
                "tasks": len(tasks),
                "changed": changed,
                "delta": args.delta,
                "force_full": args.force_full,
                "cache_file": str(cache_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
