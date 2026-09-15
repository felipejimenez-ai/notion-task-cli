"""Notion Task Manager — CRUD operations for Notion tasks via CLI.

Talks directly to the Notion REST API. No MCP, no middleware.
Supports natural names (task name, project name) instead of UUIDs.

Usage:
    python tools/notion_task_manager.py create "Task name" --project "Project name"
    python tools/notion_task_manager.py read --status today
    python tools/notion_task_manager.py update "Task name" --set-status done
    python tools/notion_task_manager.py delete "Task name"
    python tools/notion_task_manager.py sync
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
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


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — edit PROPERTIES to match your Notion database columns
# ═══════════════════════════════════════════════════════════════════════════════

PROPERTIES = {
    "name":        {"notion_key": "name",        "type": "title"},
    "today":       {"notion_key": "today",       "type": "checkbox"},
    "status":      {"notion_key": "status",      "type": "status"},
    "category":    {"notion_key": "category",    "type": "select"},
    "impact":      {"notion_key": "impact",      "type": "select"},
    "sequence":    {"notion_key": "sequence",    "type": "number"},
    "deadline":    {"notion_key": "deadline",    "type": "date"},
    "project":     {"notion_key": "project",     "type": "relation"},
    "description": {"notion_key": "description", "type": "rich_text"},
}

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_RATE_LIMIT_RPS = 3.0
DEFAULT_MAX_RETRIES = 3


# ═══════════════════════════════════════════════════════════════════════════════
# NOTION CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class NotionError(RuntimeError):
    def __init__(self, message: str, code: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class NotionClient:
    def __init__(self, token: str, database_id: str, rate_limit_rps: float = DEFAULT_RATE_LIMIT_RPS):
        if requests is None:
            raise NotionError("requests library required. pip install requests", "missing_dependency")
        self.token = token
        self.database_id = database_id
        self.rate_limit_interval = 1.0 / rate_limit_rps
        self._last_request_time = 0.0
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _rate_limit(self) -> None:
        elapsed = time.perf_counter() - self._last_request_time
        sleep_for = self.rate_limit_interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        max_retries = DEFAULT_MAX_RETRIES
        for attempt in range(max_retries):
            self._rate_limit()
            try:
                response = requests.request(method, url, headers=self.headers, json=payload, timeout=30)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt + 1 >= max_retries:
                    raise NotionError(f"network error: {exc}", "network_error") from exc
                time.sleep(2 ** attempt)
                continue

            self._last_request_time = time.perf_counter()

            if response.status_code == 429:
                if attempt + 1 >= max_retries:
                    raise NotionError("rate limit exceeded after retries", "rate_limited", 429)
                retry_after = response.headers.get("Retry-After", "1")
                try:
                    wait = max(float(retry_after), 1.0)
                except ValueError:
                    wait = 1.0
                time.sleep(wait)
                continue

            if response.status_code >= 500 and attempt + 1 < max_retries:
                time.sleep(2 ** attempt)
                continue

            break

        if response.status_code == 401:
            raise NotionError("authentication failed. check NOTION_API_KEY", "auth_error", 401)
        if response.status_code == 403:
            raise NotionError("forbidden. check integration permissions", "forbidden", 403)
        if response.status_code == 404:
            raise NotionError("not found", "not_found", 404)
        if response.status_code >= 400:
            try:
                err = response.json()
                msg = err.get("message", response.text[:300])
            except Exception:
                msg = response.text[:300]
            raise NotionError(f"API error {response.status_code}: {msg}", "api_error", response.status_code)

        if response.status_code == 204:
            return {}
        return response.json()

    # ── Name resolution ──────────────────────────────────────────────────────

    def find_page_by_name(self, name: str) -> str | None:
        """Search the database for a page with matching title. Returns page_id or None."""
        payload = {
            "filter": {
                "property": "Name",
                "title": {"equals": name}
            },
            "page_size": 1,
        }
        url = f"{NOTION_API_BASE}/databases/{self.database_id}/query"
        data = self._request("POST", url, payload)
        results = data.get("results", [])
        if not results:
            return None
        return results[0].get("id")

    def find_pages_by_name(self, name: str) -> list[dict[str, Any]]:
        """Search for all pages matching a title. Returns list of {id, properties}."""
        payload = {
            "filter": {
                "property": "Name",
                "title": {"equals": name}
            },
            "page_size": 10,
        }
        url = f"{NOTION_API_BASE}/databases/{self.database_id}/query"
        data = self._request("POST", url, payload)
        return data.get("results", [])

    def resolve_task_id(self, name_or_id: str) -> str:
        """Resolve a task name or UUID to a page_id. Raises if not found or ambiguous."""
        if _is_uuid(name_or_id):
            return name_or_id
        matches = self.find_pages_by_name(name_or_id)
        if len(matches) == 0:
            raise NotionError(f"task '{name_or_id}' not found", "task_not_found")
        if len(matches) > 1:
            ids = [m.get("id", "?")[:8] for m in matches]
            raise NotionError(
                f"ambiguous: {len(matches)} tasks named '{name_or_id}' (IDs: {', '.join(ids)}...)",
                "ambiguous_task"
            )
        return matches[0]["id"]

    def resolve_project_id(self, name: str) -> str:
        """Resolve a project name to a page_id. Raises if not found or ambiguous."""
        if _is_uuid(name):
            return name
        matches = self.find_pages_by_name(name)
        if len(matches) == 0:
            raise NotionError(f"project '{name}' not found", "project_not_found")
        if len(matches) > 1:
            ids = [m.get("id", "?")[:8] for m in matches]
            raise NotionError(
                f"ambiguous: {len(matches)} projects named '{name}' (IDs: {', '.join(ids)}...)",
                "ambiguous_project"
            )
        return matches[0]["id"]

    # ── CRUD operations ──────────────────────────────────────────────────────

    def create_page(self, properties: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": properties,
        }
        return self._request("POST", f"{NOTION_API_BASE}/pages", payload)

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"{NOTION_API_BASE}/pages/{page_id}")

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        payload = {"properties": properties}
        return self._request("PATCH", f"{NOTION_API_BASE}/pages/{page_id}", payload)

    def archive_page(self, page_id: str) -> dict[str, Any]:
        return self.update_page(page_id, {"archived": True})

    def query_database(self, filter_obj: dict[str, Any] | None = None, sorts: list[dict] | None = None) -> list[dict[str, Any]]:
        url = f"{NOTION_API_BASE}/databases/{self.database_id}/query"
        payload: dict[str, Any] = {}
        if filter_obj:
            payload["filter"] = filter_obj
        if sorts:
            payload["sorts"] = sorts
        all_results = []
        cursor = None
        while True:
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request("POST", url, payload)
            all_results.extend(data.get("results", []))
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return all_results


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _is_uuid(value: str) -> bool:
    """Check if a string looks like a UUID."""
    clean = value.strip().replace("-", "")
    return len(clean) == 32 and all(c in "0123456789abcdef" for c in clean.lower())


def get_notion_token() -> str:
    token = os.getenv("NOTION_API_KEY")
    if token:
        return token
    token = os.getenv("NOTION_TOKEN")
    if token:
        return token
    raise NotionError("no Notion token found. set NOTION_API_KEY or NOTION_TOKEN in .env", "missing_token")


def get_database_id() -> str:
    db_id = os.getenv("NOTION_DATABASE_ID")
    if db_id:
        return db_id
    raise NotionError("no database ID found. set NOTION_DATABASE_ID in .env", "missing_database_id")


# ═══════════════════════════════════════════════════════════════════════════════
# PROPERTY BUILDERS — CLI value → Notion API format
# ═══════════════════════════════════════════════════════════════════════════════

def _build_title(value: str) -> dict[str, Any]:
    return {"title": [{"text": {"content": value}}]}


def _build_rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": value}}]}


def _build_select(value: str) -> dict[str, Any]:
    return {"select": {"name": value}}


def _build_status(value: str) -> dict[str, Any]:
    return {"status": {"name": value}}


def _build_date(value: str) -> dict[str, Any]:
    return {"date": {"start": value}}


def _build_number(value: str) -> dict[str, Any]:
    try:
        num = float(value)
    except ValueError:
        raise NotionError(f"invalid number: {value}", "invalid_number")
    return {"number": num}


def _build_checkbox(value: str) -> dict[str, Any]:
    return {"checkbox": value.lower() in ("true", "yes", "1", "on")}


def _build_relation(value: str, client: NotionClient) -> dict[str, Any]:
    """Build relation property. Resolves project name to UUID if needed."""
    page_id = client.resolve_project_id(value)
    return {"relation": [{"id": page_id}]}


def build_property(prop_config: dict[str, Any], value: str, client: NotionClient | None = None) -> dict[str, Any]:
    """Build a Notion API property object from a CLI value."""
    prop_type = prop_config["type"]
    builders = {
        "title": lambda v: _build_title(v),
        "rich_text": lambda v: _build_rich_text(v),
        "select": lambda v: _build_select(v),
        "status": lambda v: _build_status(v),
        "date": lambda v: _build_date(v),
        "number": lambda v: _build_number(v),
        "checkbox": lambda v: _build_checkbox(v),
    }
    if prop_type == "relation":
        if client is None:
            raise NotionError("relation requires NotionClient for name resolution", "internal_error")
        return _build_relation(value, client)
    builder = builders.get(prop_type)
    if not builder:
        raise NotionError(f"unsupported property type: {prop_type}", "unsupported_type")
    return builder(value)


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTORS — Notion API format → readable values
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_title(prop_obj: dict[str, Any]) -> str:
    title_arr = prop_obj.get("title", [])
    return "".join(t.get("plain_text", "") for t in title_arr)


def _extract_rich_text(prop_obj: dict[str, Any]) -> str:
    text_arr = prop_obj.get("rich_text", [])
    return "".join(t.get("plain_text", "") for t in text_arr)


def _extract_select(prop_obj: dict[str, Any]) -> str | None:
    sel = prop_obj.get("select")
    return sel.get("name") if sel else None


def _extract_status(prop_obj: dict[str, Any]) -> str | None:
    s = prop_obj.get("status")
    return s.get("name") if s else None


def _extract_date(prop_obj: dict[str, Any]) -> str | None:
    d = prop_obj.get("date")
    return d.get("start") if d else None


def _extract_number(prop_obj: dict[str, Any]) -> float | None:
    return prop_obj.get("number")


def _extract_checkbox(prop_obj: dict[str, Any]) -> bool:
    return prop_obj.get("checkbox", False)


def _extract_relation(prop_obj: dict[str, Any]) -> list[str]:
    rel = prop_obj.get("relation", [])
    return [r.get("id", "") for r in rel if r.get("id")]


EXTRACTORS = {
    "title": _extract_title,
    "rich_text": _extract_rich_text,
    "select": _extract_select,
    "status": _extract_status,
    "date": _extract_date,
    "number": _extract_number,
    "checkbox": _extract_checkbox,
    "relation": _extract_relation,
}


def extract_property_value(prop_obj: dict[str, Any], prop_type: str) -> Any:
    extractor = EXTRACTORS.get(prop_type)
    if not extractor:
        return None
    return extractor(prop_obj)


# ═══════════════════════════════════════════════════════════════════════════════
# CRUD FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def create_task(client: NotionClient, name: str, project: str | None = None, **fields: str) -> dict[str, Any]:
    """Create a new task. Returns {task_id, url, message}."""
    properties = {PROPERTIES["name"]["notion_key"]: _build_title(name)}

    if project:
        properties[PROPERTIES["project"]["notion_key"]] = _build_relation(project, client)

    for cli_param, value in fields.items():
        if cli_param in PROPERTIES and value is not None:
            prop_config = PROPERTIES[cli_param]
            properties[prop_config["notion_key"]] = build_property(prop_config, value, client)

    result = client.create_page(properties)
    page_id = result.get("id", "")
    page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
    return {"task_id": page_id, "url": page_url, "message": "Task created successfully"}


def read_tasks(
    client: NotionClient,
    project: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read tasks with optional filters. Returns list of task dicts."""
    if task_id:
        page = client.get_page(task_id)
        return [_page_to_task(page)]

    filters = []
    if project:
        project_id = client.resolve_project_id(project)
        filters.append({
            "property": PROPERTIES["project"]["notion_key"],
            "relation": {"contains": project_id}
        })
    if status:
        filters.append({
            "property": PROPERTIES["status"]["notion_key"],
            "status": {"equals": status}
        })
    if priority:
        filters.append({
            "property": PROPERTIES["impact"]["notion_key"],
            "select": {"equals": priority}
        })

    filter_obj = None
    if len(filters) == 1:
        filter_obj = filters[0]
    elif len(filters) > 1:
        filter_obj = {"and": filters}

    pages = client.query_database(filter_obj)
    return [_page_to_task(p) for p in pages]


def _page_to_task(page: dict[str, Any]) -> dict[str, Any]:
    """Convert a Notion page object to a flat task dict."""
    props = page.get("properties", {})
    task: dict[str, Any] = {
        "id": page.get("id", ""),
        "url": f"https://www.notion.so/{page.get('id', '').replace('-', '')}",
    }
    for cli_param, prop_config in PROPERTIES.items():
        notion_key = prop_config["notion_key"]
        prop_obj = props.get(notion_key, {})
        if isinstance(prop_obj, dict):
            value = extract_property_value(prop_obj, prop_config["type"])
            if value is not None:
                task[cli_param] = value
    return task


def update_task(client: NotionClient, name_or_id: str, **set_fields: str) -> dict[str, Any]:
    """Update a task's fields. Returns {task_id, message}."""
    page_id = client.resolve_task_id(name_or_id)
    properties = {}
    for cli_param, value in set_fields.items():
        if cli_param in PROPERTIES and value is not None:
            prop_config = PROPERTIES[cli_param]
            properties[prop_config["notion_key"]] = build_property(prop_config, value, client)
    if not properties:
        raise NotionError("no fields to update", "no_fields")
    client.update_page(page_id, properties)
    return {"task_id": page_id, "message": "Task updated successfully"}


def delete_task(client: NotionClient, name_or_id: str, permanent: bool = False) -> dict[str, Any]:
    """Delete a task. Archive (soft) or permanent (trash)."""
    page_id = client.resolve_task_id(name_or_id)
    if permanent:
        client.archive_page(page_id)
        return {"task_id": page_id, "message": "Task permanently deleted (moved to trash)"}
    else:
        # Archive = soft delete (sets archived: true)
        client.archive_page(page_id)
        return {"task_id": page_id, "message": "Task archived (soft deleted)"}


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def batch_create(client: NotionClient, json_path: str) -> dict[str, Any]:
    """Batch create tasks from a JSON file."""
    path = Path(json_path)
    if not path.exists():
        raise NotionError(f"file not found: {json_path}", "file_not_found")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise NotionError("JSON must be an array of objects", "invalid_format")

    succeeded = []
    failed = []
    for i, row in enumerate(rows):
        try:
            name = row.get("name", "")
            if not name:
                failed.append({"row": i + 1, "error": "missing 'name' field"})
                continue
            project = row.pop("project", None)
            result = create_task(client, name, project=project, **{k: str(v) for k, v in row.items() if v is not None})
            succeeded.append({"row": i + 1, "task_id": result["task_id"]})
        except NotionError as exc:
            failed.append({"row": i + 1, "error": str(exc)})
        except Exception as exc:
            failed.append({"row": i + 1, "error": str(exc)})

    return {
        "ok": len(failed) == 0,
        "operation": "batch_create",
        "total": len(rows),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "details": {"succeeded": succeeded, "failed": failed},
    }


def batch_update(client: NotionClient, json_path: str) -> dict[str, Any]:
    """Batch update tasks from a JSON file."""
    path = Path(json_path)
    if not path.exists():
        raise NotionError(f"file not found: {json_path}", "file_not_found")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise NotionError("JSON must be an array of objects", "invalid_format")

    succeeded = []
    failed = []
    for i, row in enumerate(rows):
        try:
            task_id = row.get("task_id") or row.get("id")
            if not task_id:
                failed.append({"row": i + 1, "error": "missing 'task_id' field"})
                continue
            set_fields = {k.replace("set_", ""): str(v) for k, v in row.items() if k.startswith("set_") and v is not None}
            result = update_task(client, task_id, **set_fields)
            succeeded.append({"row": i + 1, "task_id": result["task_id"]})
        except NotionError as exc:
            failed.append({"row": i + 1, "error": str(exc)})
        except Exception as exc:
            failed.append({"row": i + 1, "error": str(exc)})

    return {
        "ok": len(failed) == 0,
        "operation": "batch_update",
        "total": len(rows),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "details": {"succeeded": succeeded, "failed": failed},
    }


def batch_delete(client: NotionClient, json_path: str) -> dict[str, Any]:
    """Batch delete tasks from a JSON file."""
    path = Path(json_path)
    if not path.exists():
        raise NotionError(f"file not found: {json_path}", "file_not_found")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise NotionError("JSON must be an array of objects", "invalid_format")

    succeeded = []
    failed = []
    for i, row in enumerate(rows):
        try:
            task_id = row.get("task_id") or row.get("id")
            if not task_id:
                failed.append({"row": i + 1, "error": "missing 'task_id' field"})
                continue
            permanent = row.get("permanent", False)
            result = delete_task(client, task_id, permanent=permanent)
            succeeded.append({"row": i + 1, "task_id": result["task_id"]})
        except NotionError as exc:
            failed.append({"row": i + 1, "error": str(exc)})
        except Exception as exc:
            failed.append({"row": i + 1, "error": str(exc)})

    return {
        "ok": len(failed) == 0,
        "operation": "batch_delete",
        "total": len(rows),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "details": {"succeeded": succeeded, "failed": failed},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SYNC HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

def sync_tasks() -> dict[str, Any]:
    """Refresh local report by calling refresh_tasks.py."""
    import subprocess
    cmd = [sys.executable, "tools/refresh_tasks.py", "--mode", "direct-api", "--delta"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise NotionError(f"sync failed: {result.stderr[:500]}", "sync_failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": True, "message": "sync completed", "stdout": result.stdout}


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def format_output(tasks: list[dict[str, Any]], fmt: str = "json") -> str:
    """Format task list for output."""
    if fmt == "json":
        return json.dumps(tasks, indent=2, ensure_ascii=False)
    elif fmt == "table":
        if not tasks:
            return "No tasks found."
        headers = ["Name", "Status", "Priority", "Deadline", "Project"]
        rows = []
        for t in tasks:
            rows.append([
                t.get("name", "-"),
                t.get("status", "-"),
                t.get("impact", "-"),
                t.get("deadline", "-"),
                (t.get("project") or ["-"])[0] if isinstance(t.get("project"), list) else (t.get("project") or "-"),
            ])
        col_widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
        header_line = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
        sep_line = "-+-".join("-" * w for w in col_widths)
        lines = [header_line, sep_line]
        for row in rows:
            lines.append(" | ".join(cell.ljust(w) for cell, w in zip(row, col_widths)))
        return "\n".join(lines)
    elif fmt == "csv":
        if not tasks:
            return ""
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "status", "impact", "deadline", "project", "category", "today", "description"])
        writer.writeheader()
        for t in tasks:
            row = {k: t.get(k, "") for k in ["name", "status", "impact", "deadline", "project", "category", "today", "description"]}
            writer.writerow(row)
        return output.getvalue()
    else:
        return json.dumps(tasks, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Notion Task Manager — CRUD operations via CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s create "My task" --project "Project X"
  %(prog)s read --status today
  %(prog)s update "My task" --set-status done
  %(prog)s delete "My task"
  %(prog)s sync
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # CREATE
    create_p = sub.add_parser("create", help="Create a new task")
    create_p.add_argument("name", help="Task name")
    create_p.add_argument("--project", help="Project name or UUID (optional)")
    create_p.add_argument("--priority", dest="impact", help="Priority: p1, p2, p3")
    create_p.add_argument("--deadline", help="Deadline: YYYY-MM-DD")
    create_p.add_argument("--status", default=None, help="Status: today, next, later, blocked")
    create_p.add_argument("--category", help="Category name")
    create_p.add_argument("--description", help="Task description")
    create_p.add_argument("--today", help="Set today flag: true/false")
    create_p.add_argument("--batch", help="Batch create from JSON file")

    # READ
    read_p = sub.add_parser("read", help="Read/query tasks")
    read_p.add_argument("--project", help="Filter by project name")
    read_p.add_argument("--status", help="Filter by status")
    read_p.add_argument("--priority", help="Filter by priority")
    read_p.add_argument("--task-id", help="Get single task by ID")
    read_p.add_argument("--format", choices=["json", "table", "csv"], default="json", help="Output format")

    # UPDATE
    update_p = sub.add_parser("update", help="Update a task")
    update_p.add_argument("name_or_id", help="Task name or UUID")
    update_p.add_argument("--set-status", help="Set status")
    update_p.add_argument("--set-priority", dest="set_impact", help="Set priority")
    update_p.add_argument("--set-deadline", help="Set deadline")
    update_p.add_argument("--set-description", help="Set description")
    update_p.add_argument("--set-category", help="Set category")
    update_p.add_argument("--set-today", help="Set today flag")
    update_p.add_argument("--set-project", help="Set project")
    update_p.add_argument("--batch", help="Batch update from JSON file")

    # DELETE
    delete_p = sub.add_parser("delete", help="Delete a task")
    delete_p.add_argument("name_or_id", nargs="?", help="Task name or UUID")
    delete_p.add_argument("--permanent", action="store_true", help="Permanent delete (move to trash)")
    delete_p.add_argument("--batch", help="Batch delete from JSON file")

    # SYNC
    sub.add_parser("sync", help="Refresh local report")

    return parser.parse_args()


def _print(text: str) -> None:
    """Print with UTF-8 encoding, falling back to safe encoding on Windows."""
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")


def _ok(data: dict[str, Any]) -> int:
    _print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0 if data.get("ok", False) else 1


def _err(message: str, code: str = "error") -> int:
    _print(json.dumps({"ok": False, "error": message, "code": code}))
    return 1


def main() -> int:
    args = parse_args()

    try:
        token = get_notion_token()
        database_id = get_database_id()
    except NotionError as exc:
        return _err(str(exc), exc.code)

    client = NotionClient(token, database_id)

    try:
        if args.command == "create":
            if args.batch:
                result = batch_create(client, args.batch)
                return _ok(result)
            result = create_task(
                client,
                name=args.name,
                project=args.project,
                impact=args.impact,
                deadline=args.deadline,
                status=args.status,
                category=args.category,
                description=args.description,
                today=args.today,
            )
            return _ok({"ok": True, "operation": "create", **result})

        elif args.command == "read":
            tasks = read_tasks(
                client,
                project=args.project,
                status=args.status,
                priority=args.priority,
                task_id=args.task_id,
            )
            output = format_output(tasks, args.format)
            _print(output)
            return 0

        elif args.command == "update":
            if args.batch:
                result = batch_update(client, args.batch)
                return _ok(result)
            set_fields = {}
            if args.set_status:
                set_fields["status"] = args.set_status
            if args.set_impact:
                set_fields["impact"] = args.set_impact
            if args.set_deadline:
                set_fields["deadline"] = args.set_deadline
            if args.set_description:
                set_fields["description"] = args.set_description
            if args.set_category:
                set_fields["category"] = args.set_category
            if args.set_today:
                set_fields["today"] = args.set_today
            if args.set_project:
                set_fields["project"] = args.set_project
            result = update_task(client, args.name_or_id, **set_fields)
            return _ok({"ok": True, "operation": "update", **result})

        elif args.command == "delete":
            if args.batch:
                result = batch_delete(client, args.batch)
                return _ok(result)
            if not args.name_or_id:
                return _err("task name or ID is required", "missing_argument")
            result = delete_task(client, args.name_or_id, permanent=args.permanent)
            return _ok({"ok": True, "operation": "delete", **result})

        elif args.command == "sync":
            result = sync_tasks()
            return _ok(result)

    except NotionError as exc:
        return _err(str(exc), exc.code)
    except Exception as exc:
        return _err(f"unexpected error: {exc}", "unexpected")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
