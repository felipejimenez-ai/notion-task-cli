## 1. Setup

- [x] 1.1 Commit current state to remote git repo (pre-CRUD snapshot)
- [x] 1.2 Archive old tools (api_fetch_tasks.py, mcp_fetch_tasks.py) to tools/.archive/
- [x] 1.3 Create OpenSpec change proposal

## 2. Core Implementation

- [x] 2.1 Build NotionClient class with rate limiting and retry logic
- [x] 2.2 Implement PROPERTIES config dict with all 9 columns
- [x] 2.3 Implement property builders (title, select, status, date, number, checkbox, relation, rich_text)
- [x] 2.4 Implement extractors for reading Notion API responses
- [x] 2.5 Implement create_task with project name resolution
- [x] 2.6 Implement read_tasks with filters (project, status, priority, task-id)
- [x] 2.7 Implement update_task with name/UUID resolution
- [x] 2.8 Implement delete_task with archive/permanent options
- [x] 2.9 Implement batch handlers (batch_create, batch_update, batch_delete)
- [x] 2.10 Implement sync handler (calls refresh_tasks.py)
- [x] 2.11 Build CLI with argparse subcommands
- [x] 2.12 Fix Windows UTF-8 encoding for stdout

## 3. Verification

- [x] 3.1 Test `read --format table` loads all tasks
- [x] 3.2 Test `read --status "to-do"` filters correctly
- [x] 3.3 Test CLI help output for all subcommands
- [ ] 3.4 Test create task with project name resolution
- [ ] 3.5 Test update task by name
- [ ] 3.6 Test delete task (archive)
- [ ] 3.7 Test batch create from JSON file

## 4. Documentation

- [x] 4.1 Write README.md with narrative, usage examples, and config docs
- [x] 4.2 Update .env.example with NOTION_DATABASE_ID
- [x] 4.3 Update tools/README.md with new tool documentation
- [x] 4.4 Create OpenSpec design.md and specs
