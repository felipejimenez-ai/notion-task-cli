"""Validate task report structure and detect regressions.

Checks:
- section counts match summary table
- summary total is consistent
- snapshot has no duplicate task names
- snapshot excludes done tasks
- optional previous/current comparison for unexpected drops

Usage:
    python tools/validate_task_report.py --current .tmp/reports/tasks-organized.md
    python tools/validate_task_report.py --previous .tmp/reports/tasks-organized.prev.md --current .tmp/reports/tasks-organized.md --strict
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SECTION_PATTERN = re.compile(r"^##\s+(Today|Next|Later|Blocked)\s*$", re.MULTILINE)
SUMMARY_ROW_PATTERN = re.compile(r"^\|\s*(Today|Next|Later|Blocked|\*\*Total\*\*)\s*\|\s*\*\*(\d+)\*\*\s*\|\s*$|^\|\s*(Today|Next|Later|Blocked)\s*\|\s*(\d+)\s*\|\s*$", re.MULTILINE)
SNAPSHOT_ROW_PATTERN = re.compile(r"^\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate organized task report")
    parser.add_argument("--current", required=True, help="Current report path")
    parser.add_argument("--previous", help="Previous report path for regression checks")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when tasks are dropped between previous and current",
    )
    return parser.parse_args()


def _read(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return file_path.read_text(encoding="utf-8")


def _extract_section_block(content: str, section: str) -> str:
    marker = f"## {section}\n"
    start = content.find(marker)
    if start < 0:
        return ""
    rest = content[start + len(marker):]
    next_header = rest.find("\n## ")
    return rest if next_header < 0 else rest[:next_header]


def _count_tasks_in_section(content: str, section: str) -> int:
    block = _extract_section_block(content, section)
    return len(re.findall(r"^- \[ \] \*\*.*?\*\*\s*$", block, flags=re.MULTILINE))


def _extract_summary_counts(content: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in SUMMARY_ROW_PATTERN.finditer(content):
        key = match.group(1) or match.group(3)
        value = match.group(2) or match.group(4)
        if key and value:
            counts[key.replace("**", "")] = int(value)
    return counts


def _extract_snapshot_rows(content: str) -> list[dict[str, str]]:
    markers = ["## Latest Live Snapshot", "## Latest Live Snapshot (MCP)"]
    start = -1
    for marker in markers:
        start = content.find(marker)
        if start >= 0:
            break
    if start < 0:
        return []

    block = content[start:]
    rows: list[dict[str, str]] = []
    for match in SNAPSHOT_ROW_PATTERN.finditer(block):
        task, status, priority, due = [group.strip() for group in match.groups()]
        if task in {"Task", "------", "-"}:
            continue
        if task.startswith("---"):
            continue
        rows.append({"task": task, "status": status, "priority": priority, "due": due})
    return rows


def validate_structure(content: str) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    details: dict[str, Any] = {}

    section_counts = {
        "Today": _count_tasks_in_section(content, "Today"),
        "Next": _count_tasks_in_section(content, "Next"),
        "Later": _count_tasks_in_section(content, "Later"),
        "Blocked": _count_tasks_in_section(content, "Blocked"),
    }
    summary_counts = _extract_summary_counts(content)

    for section, count in section_counts.items():
        summary_value = summary_counts.get(section)
        if summary_value is None:
            errors.append(f"missing summary row for section: {section}")
            continue
        if summary_value != count:
            errors.append(
                f"section/summary mismatch for {section}: section={count}, summary={summary_value}"
            )

    total_from_sections = sum(section_counts.values())
    total_in_summary = summary_counts.get("Total")
    if total_in_summary is None:
        errors.append("missing summary total row")
    elif total_in_summary != total_from_sections:
        errors.append(
            f"summary total mismatch: section_total={total_from_sections}, summary_total={total_in_summary}"
        )

    snapshot_rows = _extract_snapshot_rows(content)
    task_names = [row["task"] for row in snapshot_rows]
    duplicates = sorted({name for name in task_names if task_names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate tasks in snapshot: {len(duplicates)}")

    done_rows = [row for row in snapshot_rows if row["status"].strip().lower() == "done"]
    if done_rows:
        errors.append("snapshot contains done tasks")

    details["section_counts"] = section_counts
    details["summary_counts"] = summary_counts
    details["snapshot_rows"] = len(snapshot_rows)
    details["duplicate_snapshot_tasks"] = len(duplicates)
    return errors, details


def compare_reports(previous: str, current: str) -> dict[str, Any]:
    prev_rows = _extract_snapshot_rows(previous)
    curr_rows = _extract_snapshot_rows(current)

    prev_tasks = {row["task"] for row in prev_rows}
    curr_tasks = {row["task"] for row in curr_rows}

    dropped = sorted(prev_tasks - curr_tasks)
    added = sorted(curr_tasks - prev_tasks)

    return {
        "previous_tasks": len(prev_tasks),
        "current_tasks": len(curr_tasks),
        "dropped": dropped,
        "added": added,
    }


def main() -> int:
    args = parse_args()

    try:
        current_content = _read(args.current)
        errors, details = validate_structure(current_content)

        regression: dict[str, Any] | None = None
        if args.previous:
            previous_content = _read(args.previous)
            regression = compare_reports(previous_content, current_content)
            if args.strict and regression["dropped"]:
                errors.append(f"strict mode: dropped tasks detected ({len(regression['dropped'])})")

        result: dict[str, Any] = {
            "ok": len(errors) == 0,
            "current": args.current,
            "details": details,
            "errors": errors,
        }
        if regression is not None:
            result["regression"] = regression

        print(json.dumps(result))
        return 0 if not errors else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
