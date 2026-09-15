# Tools

Place deterministic scripts here.

Guidelines:
- Accept explicit CLI arguments (`argparse`).
- Read secrets from `.env` (with `python-dotenv`).
- Return clear exit codes (`0` success, non-zero failure).
- Print concise machine-readable or clearly structured output.
- Keep scripts idempotent when possible.

## Current tools

- `notion_task_manager.py`: CRUD operations for Notion tasks (create, read, update, delete, sync, export-source). Primary tool.
- `batch_update_descriptions.py`: dry-run-first batch updater for Notion rich_text description fields with audit logs.
- `health_check.py`: validates local environment and required Notion credentials.
- `organize_notion_tasks.py`: deterministic report generator from normalized source JSON.
- `refresh_tasks.py`: one-command orchestrator for fetch -> normalize -> generate -> validate.
- `validate_task_report.py`: validates report structure and optional previous/current regressions.

## Archived tools

Moved to `.archive/` — kept for reference but superseded by `notion_task_manager.py export-source`:

- `api_fetch_tasks.py`: fetches raw pages from Notion API.
- `mcp_fetch_tasks.py`: normalizes saved MCP or API payloads into `tasks-source.json` schema.
