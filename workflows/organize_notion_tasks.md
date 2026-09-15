# Workflow: Organize Notion Tasks into Markdown

## Objective
Fetch current tasks from Notion (Direct API by default), normalize the data, and generate a deterministic execution-first report at `.tmp/reports/tasks-organized.md`.

## Inputs
- Notion database ID: `e929797b8cff82f8aba601bbc7b2172a`
- Report output path: `.tmp/reports/tasks-organized.md`
- Source snapshot path: `.tmp/reports/tasks-source.json`
- Priority labels and task metadata already stored in Notion

## Tools Used
- `tools/notion_task_manager.py export-source` — fetch + normalize (replaces archived api_fetch/mcp_fetch scripts)
- `python tools/organize_notion_tasks.py --source-file .tmp/reports/tasks-source.json --output .tmp/reports/tasks-organized.md --validate`
- `python tools/validate_task_report.py --current .tmp/reports/tasks-organized.md`
- `python tools/refresh_tasks.py` — Unified orchestrator for all modes

## Steps

### Recommended: Direct-API Mode (Fastest, ~1.7s)
```bash
python tools/refresh_tasks.py --mode direct-api --delta
```
**Best for:** Daily refreshes, live updates, general use  
**Speed:** 1.7 seconds (vs 12-45s for MCP)  
**Requirements:** NOTION_API_KEY or NOTION_TOKEN in .env

### Alternative: Local Cached Mode (~0.25s)
```bash
python tools/refresh_tasks.py --mode mcp-local --delta
```
**Best for:** Offline work, when API is unavailable  
**Speed:** 0.25 seconds (uses cached data)  
**Requirements:** Previous `tasks-source.json` must exist

### Full Workflow (Manual Steps, Legacy MCP Path)
1. Use Notion MCP to read the live tasks database and data source metadata.
2. Collect task page payloads from the tasks data source and save raw JSON at `.tmp/reports/mcp-pages.json`.
3. Normalize raw payloads into `.tmp/reports/tasks-source.json` by running:
   - `python tools/notion_task_manager.py export-source --input .tmp/reports/mcp-pages.json --output .tmp/reports/tasks-source.json`
4. Ensure normalized source contains keys:
   - `name`
   - `url`
   - `status`
   - `impact`
   - `category`
   - `deadline` (or `date:deadline:start`)
   - `today`
   - `sequence`
   - `description` (optional, recommended)
   - `project` (optional, relation link)
5. Run:
   - `python tools/organize_notion_tasks.py --source-file .tmp/reports/tasks-source.json --output .tmp/reports/tasks-organized.md --validate`
6. Group tasks into:
   - `Today`
   - `Next`
   - `Later`
   - `Blocked`
7. Preserve useful task metadata such as priority, category, deadline, project, description, and subtasks.
8. Validate final report structure:
   - `python tools/validate_task_report.py --current .tmp/reports/tasks-organized.md`
9. When tasks change in Notion, rerun the workflow to refresh the report.

## Refresh Command Matrix

| Use Case | Command | Speed | Requirements |
|----------|---------|-------|--------------|
| **Daily refresh (online)** | `refresh_tasks.py --mode direct-api --delta` | 1.7s | NOTION_API_KEY |
| **Daily refresh (offline)** | `refresh_tasks.py --mode mcp-local --delta` | 0.25s | Cached source.json |
| **Weekly full reset** | `refresh_tasks.py --mode direct-api --force-full` | 1.7s | NOTION_API_KEY |
| **Strict validation** | `refresh_tasks.py --mode direct-api --delta --strict` | 1.7s | NOTION_API_KEY |
| **Emergency fallback** | `refresh_tasks.py --mode mcp-live --mcp-input .tmp/reports/mcp-pages.json` | 12-45s | MCP available |
| **Batch description updates** | `refresh_tasks.py --mode batch-descriptions --batch-input .tmp/reports/description-updates.csv` | depends on rows | NOTION_API_KEY |

## Key Implementation Details

### Delta Caching
- By default, `--delta` flag enables fingerprint-based change detection
- If source hasn't changed, report generation is skipped but report validation still runs
- Use `--force-full` to ignore cache and rebuild from scratch

### Fallback Behavior
- Direct-API mode can fall back to MCP on token/API failures when explicitly enabled
- Enable with `--allow-fallback-mcp` flag
- Only one fallback hop is allowed per run (prevents loops)

### Status Filtering
- By default, `status: done` tasks are **excluded** from the report
- Shows only `status: to-do` and unspecified tasks
- Use `--exclude-status <status>` to customize filter (repeatable)

## Output
A markdown report at `.tmp/reports/tasks-organized.md` containing:
- execution buckets
- a short recommended order
- summary counts
- the latest live task snapshot from Notion
- stable ordering across runs when input data is unchanged
- description text when available
- project link when available

## Failure Handling
- If the database cannot be read through Direct API, verify `NOTION_API_KEY`/`NOTION_TOKEN` and integration access.
- If fallback was requested and fails, pass a valid `--mcp-input` or run `--mode mcp-local` explicitly.
- If source normalization fails, verify `.tmp/reports/mcp-pages.json` is a JSON array of MCP page payloads.
- If cache metadata is corrupt, fix or delete `.tmp/reports/tasks-cache.json` and rerun.
- If validation fails, fix duplicate URLs or status mismatches in source JSON and rerun.
- If the output file is missing, rerun the workflow so the report is regenerated.
- If the task list looks stale, refresh from Notion again instead of editing the markdown by hand.

## Notes
- This workflow is the WAT-native path.
- It does not depend on `NOTION_TOKEN` for the refresh step.
- `NOTION_TOKEN`-based refresh is deprecated in this repository.
- Deterministic sort order is: priority, due date, sequence, title.
- Unspecified-priority tasks default to `Later`.
- Delta mode writes cache metadata to `.tmp/reports/tasks-cache.json`.
- Use `--force-full` when you want to bypass cache and rebuild all artifacts.
