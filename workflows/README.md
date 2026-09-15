# Workflows

Workflows are SOPs that define:

- objective
- required inputs
- exact tool sequence
- expected outputs
- failure handling

Use one Markdown file per repeatable process, for example:

- `scrape_website.md`
- `update_sheet.md`
- `build_report.md`

## Current Workflows

- `organize_notion_tasks.md`: Notion task refresh, normalization, report generation, and validation.
- `batch_update_descriptions.md`: safe batch updates for Notion description rich_text fields (dry-run first).
- `task_operating_rules.md`: simple daily and weekly rules for task intake and maintenance.

## Workflow Template

```md
# <Workflow Name>

## Objective
<What this workflow achieves>

## Inputs
- <Input A>
- <Input B>

## Tools Used
- `tools/<script_name>.py`

## Steps
1. <Step 1>
2. <Step 2>
3. <Step 3>

## Output
<Where the deliverable lives>

## Failure Handling
- <Known issue + mitigation>
```
