"""Organize Notion Tasks into Markdown (MCP-first).

This tool supports deterministic report generation from a normalized JSON snapshot
produced through the MCP workflow. The legacy NOTION_TOKEN-based path is
deprecated and intentionally not implemented.

Usage examples:
    python tools/organize_notion_tasks.py --source-file .tmp/reports/tasks-source.json
    python tools/organize_notion_tasks.py --source-file .tmp/reports/tasks-source.json --validate
    python tools/organize_notion_tasks.py --write-placeholder
"""

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


PRIORITY_LABELS = {
    "p1": "P1",
    "p2": "P2",
    "p3": "P3",
}

CATEGORY_LABELS = {
    "DW | Deep Work": "Deep Work",
    "TASK | administrative - planning": "Administrative - Planning",
    "MTNG | meeting - class": "Meeting - Class",
}

PRIORITY_RANK = {"p1": 1, "p2": 2, "p3": 3}


@dataclass(frozen=True)
class Task:
    name: str
    url: str
    status: str
    description: str | None
    project: str | None
    impact: str | None
    category: str | None
    deadline: str | None
    today: bool
    sequence: float | None


def _clean_title(value: str) -> str:
    return " ".join(value.strip().split())


def _get_item_value(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item.get(name)
    lowered = {key.lower(): key for key in item.keys()}
    for name in names:
        actual_key = lowered.get(name.lower())
        if actual_key is not None:
            return item.get(actual_key)
    return None


def _clean_due(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _parse_today(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"__yes__", "yes", "true", "1"}


def _parse_sequence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP-first task organizer")
    parser.add_argument(
        "--database-id",
        default="e929797b8cff82f8aba601bbc7b2172a",
        help="Notion database ID reference (for messaging only)",
    )
    parser.add_argument(
        "--source-file",
        help="Path to normalized task JSON input generated from MCP fetch/search",
    )
    parser.add_argument(
        "--output",
        default=".tmp/reports/tasks-organized.md",
        help="Output markdown file path",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate task integrity checks before writing report",
    )
    parser.add_argument(
        "--write-placeholder",
        action="store_true",
        help="Write placeholder markdown with MCP refresh instructions",
    )
    return parser.parse_args()


def load_tasks(source_file: str) -> list[Task]:
    with open(source_file, "r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise ValueError("source-file must contain a JSON array")

    tasks: list[Task] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"source-file item at index {index} is not an object")

        name = _clean_title(str(item.get("name", "")))
        if not name:
            continue

        task = Task(
            name=name,
            url=str(item.get("url", "")).strip(),
            status=str(item.get("status", "")).strip().lower() or "to-do",
            description=(str(_get_item_value(item, "description", "Description") or "").strip() or None),
            project=(str(item.get("project", "")).strip() or None),
            impact=(str(item.get("impact", "")).strip().lower() or None),
            category=(str(item.get("category", "")).strip() or None),
            deadline=_clean_due(item.get("deadline") or item.get("due") or item.get("date:deadline:start") or item.get("date:deadine:start")),
            today=_parse_today(item.get("today")),
            sequence=_parse_sequence(item.get("sequence")),
        )
        tasks.append(task)

    return tasks


def priority_rank(task: Task) -> int:
    return PRIORITY_RANK.get(task.impact or "", 99)


def stable_sort(tasks: Iterable[Task]) -> list[Task]:
    return sorted(
        tasks,
        key=lambda task: (
            priority_rank(task),
            task.deadline is None,
            task.deadline or "",
            task.sequence is None,
            task.sequence if task.sequence is not None else 0,
            task.name.lower(),
        ),
    )


def bucket_for_task(task: Task) -> str:
    if task.status == "blocked":
        return "blocked"
    if task.today:
        return "today"
    if task.impact in {"p1", "p2"}:
        return "next"
    return "later"


def build_buckets(tasks: list[Task]) -> dict[str, list[Task]]:
    active = [task for task in tasks if task.status != "done"]
    buckets = {"today": [], "next": [], "later": [], "blocked": []}
    for task in active:
        buckets[bucket_for_task(task)].append(task)

    return {name: stable_sort(items) for name, items in buckets.items()}


def validate_tasks(tasks: list[Task], buckets: dict[str, list[Task]]) -> list[str]:
    errors: list[str] = []

    urls = [task.url for task in tasks if task.url]
    duplicate_urls = [url for url, count in Counter(urls).items() if count > 1]
    if duplicate_urls:
        errors.append(f"duplicate task URLs found: {len(duplicate_urls)}")

    active_count = len([task for task in tasks if task.status != "done"])
    bucket_count = sum(len(items) for items in buckets.values())
    if active_count != bucket_count:
        errors.append(
            f"active/bucket mismatch: active={active_count}, bucketed={bucket_count}"
        )

    done_in_buckets = [
        task.name
        for bucket_tasks in buckets.values()
        for task in bucket_tasks
        if task.status == "done"
    ]
    if done_in_buckets:
        errors.append("done tasks found in output buckets")

    return errors


def format_date_human(date_value: str | None) -> str | None:
    if not date_value:
        return None
    try:
        parsed = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return date_value
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def format_category(category: str | None) -> str | None:
    if not category:
        return None
    return CATEGORY_LABELS.get(category, category)


def format_priority(impact: str | None) -> str:
    return PRIORITY_LABELS.get(impact or "", "Unspecified")


def build_markdown(tasks: list[Task], buckets: dict[str, list[Task]]) -> str:
    now = datetime.now().strftime("%B %d, %Y")
    lines: list[str] = ["# My Notion Tasks", "", f"**Generated:** {now}", ""]

    section_order = [
        ("Today", "today"),
        ("Next", "next"),
        ("Later", "later"),
        ("Blocked", "blocked"),
    ]

    for title, key in section_order:
        lines.append(f"## {title}")
        lines.append("")
        items = buckets[key]
        if not items:
            lines.append("- None right now")
            lines.append("")
            continue

        for task in items:
            lines.append(f"- [ ] **{task.name}**")
            lines.append(f"  - Priority: {format_priority(task.impact)}")

            if task.description:
                lines.append(f"  - description: {task.description}")

            if task.project:
                lines.append(f"  - project: {task.project}")

            category = format_category(task.category)
            if category:
                lines.append(f"  - Category: {category}")

            deadline_human = format_date_human(task.deadline)
            if deadline_human:
                lines.append(f"  - deadline: {deadline_human}")

            if key == "today":
                lines.append("  - Status: Today")

            lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Today | {len(buckets['today'])} |")
    lines.append(f"| Next | {len(buckets['next'])} |")
    lines.append(f"| Later | {len(buckets['later'])} |")
    lines.append(f"| Blocked | {len(buckets['blocked'])} |")
    total = sum(len(values) for values in buckets.values())
    lines.append(f"| **Total** | **{total}** |")
    lines.append("")

    lines.append("### Priority Distribution")
    lines.append("")
    priority_counter = Counter(task.impact or "unspecified" for task in tasks if task.status != "done")
    lines.append("| Priority | Count |")
    lines.append("|----------|-------|")
    lines.append(f"| P1 (High) | {priority_counter.get('p1', 0)} |")
    lines.append(f"| P2 (Medium) | {priority_counter.get('p2', 0)} |")
    lines.append(f"| P3 (Low) | {priority_counter.get('p3', 0)} |")
    lines.append(f"| Unspecified | {priority_counter.get('unspecified', 0)} |")
    lines.append("")

    lines.append("## Recommended Order")
    lines.append("")
    recommended = buckets["today"] + buckets["next"] + buckets["later"] + buckets["blocked"]
    if recommended:
        for index, task in enumerate(recommended, start=1):
            lines.append(f"{index}. {task.name}")
    else:
        lines.append("1. None right now")
    lines.append("")

    lines.append("## Latest Live Snapshot")
    lines.append("")
    lines.append("| Task | Status | Priority | Deadline |")
    lines.append("|------|--------|----------|----------|")
    for task in recommended:
        lines.append(
            f"| {task.name} | {task.status or '-'} | {task.impact or '-'} | {task.deadline or '-'} |"
        )

    if not recommended:
        lines.append("| - | - | - | - |")

    lines.append("")
    return "\n".join(lines)


def write_placeholder(output_path: str, database_id: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        "# Task Report Pending MCP Refresh\n\n"
        f"Generated: {now}\n\n"
        "This repository is MCP-first for Notion task refresh.\n"
        "The local NOTION_TOKEN script flow is deprecated.\n\n"
        "## Refresh Path\n"
        "1. Use the Notion MCP workflow in workflows/organize_notion_tasks.md\n"
        f"2. Refresh database: {database_id}\n"
        "3. Save normalized task JSON as .tmp/reports/tasks-source.json\n"
        "4. Run: python tools/organize_notion_tasks.py --source-file .tmp/reports/tasks-source.json\n"
    )

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        file.write(content)


def main() -> int:
    args = parse_args()

    if args.write_placeholder:
        write_placeholder(args.output, args.database_id)
        print(json.dumps({"ok": True, "placeholder_written": True, "output_file": args.output}))
        return 0

    if args.source_file:
        tasks = load_tasks(args.source_file)
        buckets = build_buckets(tasks)

        if args.validate:
            errors = validate_tasks(tasks, buckets)
            if errors:
                print(json.dumps({"ok": False, "errors": errors}))
                return 1

        report = build_markdown(tasks, buckets)
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        with open(args.output, "w", encoding="utf-8") as file:
            file.write(report)

        active_count = sum(len(items) for items in buckets.values())
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "source-file",
                    "source_file": args.source_file,
                    "output_file": args.output,
                    "active_tasks": active_count,
                    "buckets": {
                        "today": len(buckets["today"]),
                        "next": len(buckets["next"]),
                        "later": len(buckets["later"]),
                        "blocked": len(buckets["blocked"]),
                    },
                }
            )
        )
        return 0

    message = {
        "ok": True,
        "deprecated": True,
        "mode": "mcp-first",
        "database_id": args.database_id,
        "output_file": args.output,
        "next_step": "Use Notion MCP workflow, save normalized task JSON, and rerun with --source-file",
        "note": "No source file provided. NOTION_TOKEN refresh remains deprecated.",
    }
    print(json.dumps(message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
