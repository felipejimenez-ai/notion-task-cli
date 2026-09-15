## ADDED Requirements

### Requirement: Create task
The system SHALL allow creating a new Notion task with a name and optional fields (project, priority, deadline, status, category, description, today). The system SHALL accept natural names for project and resolve them to Notion UUIDs via API query.

#### Scenario: Create task with project name
- **WHEN** user runs `create "Task name" --project "Project X"`
- **THEN** system queries Notion for project named "Project X", creates task with relation to that project, returns `{task_id, url, message}`

#### Scenario: Create task without project
- **WHEN** user runs `create "Task name"` (no --project flag)
- **THEN** system creates task with empty project relation, returns `{task_id, url, message}`

#### Scenario: Create task with all fields
- **WHEN** user runs `create "Task" --project "P" --priority p1 --deadline 2026-09-19 --status doing --category "Deep Work" --description "notes"`
- **THEN** system creates task with all properties set

#### Scenario: Project name not found
- **WHEN** user runs `create "Task" --project "Nonexistent"`
- **THEN** system returns error with message "project 'Nonexistent' not found"

#### Scenario: Ambiguous project name
- **WHEN** user runs `create "Task" --project "X"` and 2+ projects named "X" exist
- **THEN** system returns error with message "ambiguous: N projects named 'X'"

### Requirement: Read tasks
The system SHALL allow querying tasks with optional filters (project, status, priority) and output in json, table, or csv format.

#### Scenario: Read all tasks
- **WHEN** user runs `read`
- **THEN** system returns all tasks as JSON array

#### Scenario: Filter by project
- **WHEN** user runs `read --project "Project X"`
- **THEN** system resolves project name to UUID, queries tasks where project relation contains that UUID

#### Scenario: Filter by status
- **WHEN** user runs `read --status "to-do"`
- **THEN** system returns tasks with status equal to "to-do"

#### Scenario: Read single task by ID
- **WHEN** user runs `read --task-id "page-uuid"`
- **THEN** system returns that single task's full properties

#### Scenario: Output format table
- **WHEN** user runs `read --format table`
- **THEN** system prints aligned table with columns: Name, Status, Priority, Deadline, Project

### Requirement: Update task
The system SHALL allow updating one or more fields on an existing task, identified by name or UUID.

#### Scenario: Update by name
- **WHEN** user runs `update "Task name" --set-status doing`
- **THEN** system resolves task name to UUID, patches status property, returns `{task_id, message}`

#### Scenario: Update by UUID
- **WHEN** user runs `update "page-uuid" --set-priority p1`
- **THEN** system patches priority property directly

#### Scenario: Update multiple fields
- **WHEN** user runs `update "Task" --set-status done --set-deadline 2026-09-20`
- **THEN** system patches both status and deadline in one API call

#### Scenario: Task not found
- **WHEN** user runs `update "Nonexistent" --set-status done`
- **THEN** system returns error "task 'Nonexistent' not found"

### Requirement: Delete task
The system SHALL allow deleting a task by name or UUID, with archive (soft) or permanent delete options.

#### Scenario: Archive task
- **WHEN** user runs `delete "Task name"`
- **THEN** system archives the page (archived: true), returns `{task_id, message: "Task archived"}`

#### Scenario: Permanent delete
- **WHEN** user runs `delete "Task name" --permanent`
- **THEN** system archives the page, returns `{task_id, message: "Task permanently deleted"}`

### Requirement: Batch operations
The system SHALL support batch create, update, and delete from JSON files.

#### Scenario: Batch create
- **WHEN** user runs `create --batch tasks.json`
- **THEN** system reads JSON array, creates each task, reports succeeded/failed counts

#### Scenario: Batch partial failure
- **WHEN** batch create has 3 tasks and 1 fails (missing name)
- **THEN** system creates 2 tasks, returns `{ok: false, succeeded: 2, failed: 1, details: {...}}`

### Requirement: Sync local report
The system SHALL support refreshing the local markdown report via `sync` command.

#### Scenario: Sync
- **WHEN** user runs `sync`
- **THEN** system calls `refresh_tasks.py --mode direct-api --delta` and returns its output

### Requirement: Rate limiting
The system SHALL rate-limit API requests to 3 per second and retry on 429/5xx errors with exponential backoff.

#### Scenario: Rate limit hit
- **WHEN** Notion API returns 429
- **THEN** system waits for Retry-After duration, retries up to 3 times

#### Scenario: Server error
- **WHEN** Notion API returns 500
- **THEN** system retries up to 3 times with exponential backoff (1s, 2s, 4s)

### Requirement: Windows compatibility
The system SHALL output UTF-8 encoded text even on Windows consoles that don't support it.

#### Scenario: Unicode characters in output
- **WHEN** task data contains characters like → or é
- **THEN** system writes UTF-8 to stdout.buffer, falls back to replacement characters if needed
