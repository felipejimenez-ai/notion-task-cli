# Workflow: Task Operating Rules

## Objective
Keep task management simple: capture work in Notion, keep the repo for execution, and avoid memorizing process details.

## Inputs
- Notion task database with lowercase properties
- Optional project relation database `notion-ai`
- Repo workflows and tools for execution

## Rules
- New tasks must have `description` and `deadline` before they are considered active; `project` is optional.
- Keep the visible table minimal: `name`, `status`, `category`, `impact`, `sequence`, `deadline`, `project`, `today`.
- Keep `assignee`, `blocked-by`, and `blocking` hidden in the table.
- Use `description` for short notes that automation should read.
- Use the page body only for optional long-form context.

## Daily Steps
1. Pick one `today` task.
2. Pick one `next` task.
3. Pick one small maintenance task.
4. Move or update anything blocked.
5. Do not keep a parallel task list outside Notion.

## Weekly Steps
1. Backfill missing `description` values in small batches.
2. Add `project` relations only for tasks that require project-level tracking.
3. Clean stale tasks instead of keeping them in memory.
4. Re-run the refresh flow after the batch update.

## Output
- A clean Notion task database with consistent properties.
- A refreshed markdown report at `.tmp/reports/tasks-organized.md`.

## Failure Handling
- If a task is missing `description`, add it before marking the task active.
- If a task actually belongs to a project, link it to the correct project page; otherwise keep it standalone.
- If the refresh output looks stale, rerun `python tools/refresh_tasks.py --mode direct-api --delta`.