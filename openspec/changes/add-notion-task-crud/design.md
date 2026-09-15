## Context

The existing Notion integration uses a read-only pipeline (`api_fetch_tasks.py` → `mcp_fetch_tasks.py` → `organize_notion_tasks.py` → `validate_task_report.py`). Users need CRUD operations that work with natural names (project/task names) instead of UUIDs, with batch support and low latency.

The Notion API already supports all CRUD operations. The tool is a thin CLI wrapper — no server, no database, no middleware.

## Goals / Non-Goals

**Goals:**
- CRUD operations (create, read, update, delete) for Notion tasks via CLI
- Natural name resolution — user types project/task names, script resolves to UUIDs
- Batch operations from JSON files
- Multiple output formats (json, table, csv)
- Rate limiting (3 req/s) with retry on 429/5xx
- Cloneable — config at top of file, `.env` for credentials

**Non-Goals:**
- FastAPI server (not needed for CLI use case)
- Local database or caching layer
- Real-time sync or webhooks
- Multi-database support (one database at a time)
- Authentication flow (relies on existing Notion integration tokens)

## Decisions

**1. Single file, not a package**
Rationale: Simpler to clone, distribute, and understand. One `notion_task_manager.py` with ~800 lines is manageable. A package would add `__init__.py`, `setup.py`, etc. for no real benefit at this scale.

**2. Direct `requests` calls, no SDK**
Rationale: Notion has an official Python SDK (`notion-client`), but it adds a dependency and wraps the same HTTP calls. Using `requests` directly keeps dependencies minimal (already in `requirements.txt`) and makes the API calls explicit and debuggable.

**3. Name resolution via database query filter**
Rationale: `POST /databases/{id}/query` with `filter: {property: "Name", title: {equals: "..."}}` returns exact matches. This is the simplest approach — one API call per resolution. Alternatives considered: caching names (complexity not worth it for CLI usage), local name→UUID mapping file (stale data risk).

**4. No property auto-discovery**
Rationale: Auto-discovering database properties via `GET /databases/{id}` would add flexibility but also latency and complexity. The PROPERTIES dict at the top of the file is explicit, documented, and fast. Users clone, edit one dict, done.

**5. Windows UTF-8 encoding handled via `sys.stdout.buffer`**
Rationale: Notion data contains Unicode characters (→, é, etc.). Windows console defaults to cp1252 which can't print these. Writing directly to `sys.stdout.buffer` with UTF-8 encoding bypasses the console's codec. Fallback to `errors="replace"` for safety.

## Risks / Trade-offs

- **Name collisions** → Script errors on ambiguous matches (multiple tasks/projects with same name). Mitigation: clear error messages with suggestion of alternatives.
- **Rate limits** → 3 req/s cap per Notion API. Batch of 100 tasks takes ~33s. Mitigation: built-in rate limiter with sleep between requests.
- **No offline mode** → Every operation requires API call. Mitigation: not a CLI concern; the existing `refresh_tasks.py` handles offline via cached data.
- **PROPERTIES dict maintenance** → If Notion schema changes, user must update the dict. Mitigation: clear error messages when property not found.
