# notion-task-cli

Fast Notion task management from the command line.

## Job to Be Done

> As someone who works with AI daily, I want to manage my Notion tasks through natural language so I can stay in my AI-powered workflow instead of context-switching to Notion's UI.

All my knowledge lives in Obsidian — AI-powered, local-first, fast. But tasks live in Notion. The gap between these two tools is where productivity dies: opening a browser, navigating databases, clicking through properties. The Notion MCP is painfully slow (high latency per call), and the raw API is verbose and UUID-dependent for every operation.

So I built a thin CLI wrapper that talks directly to the Notion API. No MCP middleware, no unnecessary abstraction — just `requests` → Notion REST API. A layer that lets AI agents (and humans) CRUD Notion tasks without leaving the terminal. One fewer context switch. One fewer tab. One fewer interruption to deep work.

**The bigger picture:** this is a full-stack AI engineering problem — API design, CLI ergonomics, error handling, rate limiting, batch operations, and the glue code that connects an LLM's reasoning to a real productivity tool. The kind of work that matters when you're building AI systems that actually ship.

## The Template

The Notion database behind this CLI is a task management system built to run a real business — marketing, sales, operations, finance, and personal life — all in one place. [Use the template](https://1afjp.notion.site/tasks-template-f719797b8cff827097c281345e601518?source=copy_link) to get started.

**Columns:**

| Column | Values | Purpose |
|--------|--------|---------|
| Name | — | Task title |
| Status | To Do, Doing, Quality Check, Blocked, Done | Pipeline stage |
| Category | DW \| deep work, MTNG \| meeting - class, Task \| administrative - planning, PRSNL \| personal, GAP | Work type |
| Impact | P1, P2, P3 | Priority level |
| Sequence | — | Custom ordering within a project |
| Deadline | — | Due date |
| Project | — | Relation to project, groups tasks by initiative |
| Assignee | — | Who's responsible |
| Description | — | Task details |

**Views:**

- **Today** — checkbox to flag what you're working on today
- **Calendar** — visual deadline tracking across all tasks
- **Done** — completed and archived tasks
- **Kanban per project** — drag tasks across pipeline columns (To Do → Doing → Quality Check → Blocked → Done) inside each project folder

This template is what the CLI wraps. Every flag you pass maps to a real column in the database.

## Why Not Just Use the Raw API?

The raw Notion API works, but it's painful for day-to-day task management:

1. **UUIDs everywhere** — Every operation needs page IDs, database IDs, relation IDs. You're copy-pasting UUIDs from Notion URLs instead of typing human names.
2. **Verbose property format** — Each property type has a different JSON shape. A single `create` call requires 5-10 lines of nested JSON per task. A simple "create task under project" shouldn't need that.
3. **No name resolution** — If you want to link a task to a project, you need the project's UUID first. That means an extra API query before every create/update.
4. **Rate limiting is manual** — Notion caps at 3 requests/second. The raw API returns 429s with no built-in retry. You handle backoff yourself or get throttled.
5. **No batch support** — Creating 50 tasks means 50 API calls with manual delays between each.
6. **Error handling is on you** — A 400 error tells you "invalid property format." A 429 tells you to wait. A 403 tells you permissions are wrong. The CLI translates these into readable messages.

## What You Get

- **CRUD operations** — create, read, update, delete tasks from the terminal
- **Natural names** — reference projects and tasks by name, not UUIDs
- **Batch support** — create/update/delete hundreds of tasks from a JSON file
- **Low latency** — direct API calls, ~200ms per operation
- **Cloneable** — fork it, edit the config, set your `.env`, done

## Quick Start

```bash
git clone https://github.com/felipejimenez-ai/notion-task-cli.git
cd notion-task-cli
pip install -r requirements.txt
cp .env.example .env  # add your Notion API key and database ID
```

## Usage

### Create

```bash
# Simple — just name and project
python tools/notion_task_manager.py create "Deploy landing page" --project "client-acme"

# With all options
python tools/notion_task_manager.py create "Deploy landing page" \
  --project "client-acme" \
  --priority p1 \
  --deadline 2026-09-19 \
  --status today \
  --category "Deep Work" \
  --description "Ship the new landing page"

# Without project (goes to "No project")
python tools/notion_task_manager.py create "Fix typo on homepage"

# Batch create
python tools/notion_task_manager.py create --batch tasks.json
```

### Read

```bash
# All tasks
python tools/notion_task_manager.py read

# Filter by project
python tools/notion_task_manager.py read --project "client-acme"

# Filter by status
python tools/notion_task_manager.py read --status today

# Filter by priority
python tools/notion_task_manager.py read --priority p1

# Single task by ID
python tools/notion_task_manager.py read --task-id "page-uuid"

# Output formats
python tools/notion_task_manager.py read --format table
python tools/notion_task_manager.py read --format csv
```

### Update

```bash
# Update by name
python tools/notion_task_manager.py update "Deploy landing page" --set-status done

# Update multiple fields
python tools/notion_task_manager.py update "Deploy landing page" \
  --set-priority p1 \
  --set-deadline 2026-09-20 \
  --set-description "Updated description"

# Batch update
python tools/notion_task_manager.py update --batch updates.json
```

### Delete

```bash
# Archive (soft delete — sets status to "done")
python tools/notion_task_manager.py delete "Deploy landing page"

# Permanent delete (moves to trash)
python tools/notion_task_manager.py delete "Deploy landing page" --permanent

# Batch delete
python tools/notion_task_manager.py delete --batch delete-list.json
```

### Sync

```bash
# Refresh local report after changes
python tools/notion_task_manager.py sync
```

## How It Works

```
CLI args → resolve names → Notion API → done
```

The script takes natural names (task name, project name), resolves them to Notion UUIDs via API queries, then performs the operation. No local database, no caching layer — just the API.

## Configuration

### .env

```
NOTION_API_KEY=your-integration-token
NOTION_DATABASE_ID=your-task-database-id
```

### Property Mapping

Edit the `PROPERTIES` dict at the top of `notion_task_manager.py` to match your database columns. The default config works with the included schema.

### Default Columns

| CLI param | Notion key | Type |
|-----------|-----------|------|
| `--name` | Name | title |
| `--today` | today | checkbox |
| `--status` | Status | status |
| `--category` | Category | select |
| `--impact` / `--priority` | Impact | select |
| `--sequence` | Sequence | number |
| `--deadline` | Deadline | date |
| `--project` | Project | relation |

## Requirements

- Python 3.10+
- requests
- A Notion integration token with database access

## The Stack

This tool is part of a larger WAT (Workflows, Agents, Tools) architecture for connecting AI agents to Notion. The CLI is the simple path — for AI integration, use the underlying `NotionClient` class directly.
