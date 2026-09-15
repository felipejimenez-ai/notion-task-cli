## Why

Notion's task management is unmatched, but the Notion MCP is slow and the raw API is verbose and UUID-dependent. Every CRUD operation requires knowing page IDs, nested JSON property formats, and handling rate limiting manually. We need a thin CLI wrapper that talks directly to the Notion API with natural names instead of UUIDs.

## What Changes

- **New**: `tools/notion_task_manager.py` — single-file CRUD tool for Notion tasks (create, read, update, delete, sync)
- **New**: Natural name resolution — type project/task names instead of UUIDs
- **New**: Batch operations — create/update/delete multiple tasks from JSON files
- **New**: Multiple output formats (json, table, csv) for read operations
- **Modified**: `.env.example` — added `NOTION_DATABASE_ID` variable
- **Modified**: `tools/README.md` — documented new tool, archived old tools
- **Archived**: `tools/api_fetch_tasks.py`, `tools/mcp_fetch_tasks.py` → `tools/.archive/`

## Capabilities

### New Capabilities
- `notion-task-crud`: Full CRUD operations for Notion tasks via CLI with natural name resolution, batch support, and rate-limited API calls

### Modified Capabilities
<!-- None — this is a new tool, not changing existing spec-level behavior -->

## Impact

- **Code**: Single new file `tools/notion_task_manager.py` (~800 lines), no external dependencies beyond `requests`
- **API**: Talks to Notion REST API v1 (POST/PATCH/GET/DELETE endpoints)
- **Dependencies**: None added — `requests` already in `requirements.txt`
- **Data**: No schema changes to Notion database, reads/writes existing properties
- **Existing tools**: `refresh_tasks.py`, `organize_notion_tasks.py`, `validate_task_report.py` unchanged
