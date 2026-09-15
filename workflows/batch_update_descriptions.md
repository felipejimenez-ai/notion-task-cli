# Workflow: Batch Update Notion Descriptions

## Objective
Apply description updates to many Notion pages safely and deterministically.

## Inputs
- Update file (`.csv` or `.json`) with page locator and new description text
- Notion token in `.env` (`NOTION_API_KEY` preferred)
- Target rich text property name (default: `description`)

## Tools Used
- `python tools/batch_update_descriptions.py`

## Input Contract

Accepted page locator fields (at least one required):
- `page_id`
- `page_url`
- `url`
- `id`

Accepted description fields (at least one required):
- `description`
- `new_description`
- `content`

Description max length is 2000 characters for this MVP.

### CSV Example
```csv
page_url,description
https://www.notion.so/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,Updated execution notes for this task.
```

### JSON Example
```json
[
  {
    "page_id": "01234567-89ab-cdef-0123-456789abcdef",
    "description": "Updated execution notes for this task."
  }
]
```

## Steps

1. Run preflight dry-run (default mode):
```bash
python tools/batch_update_descriptions.py --input .tmp/reports/description-updates.csv
```

2. Review audit output:
- `.tmp/reports/mutation-audit.jsonl`

3. Apply live updates only after dry-run looks correct:
```bash
python tools/batch_update_descriptions.py --input .tmp/reports/description-updates.csv --apply
```

4. For staged rollouts, cap rows:
```bash
python tools/batch_update_descriptions.py --input .tmp/reports/description-updates.csv --apply --max-rows 20
```

5. Continue through row errors when needed:
```bash
python tools/batch_update_descriptions.py --input .tmp/reports/description-updates.csv --apply --continue-on-error
```

## Output
- JSON summary to stdout with run stats
- Append-only JSONL audit at `.tmp/reports/mutation-audit.jsonl`

Each audit line includes:
- timestamp
- run_id
- row
- page_id
- status (`validation_error`, `dry_run_ready`, `skipped`, `updated`, `failed`)
- new_description
- optional old_description and error metadata

## Failure Handling
- Invalid input rows are logged as `validation_error` and excluded from updates.
- Missing property names or wrong property type are logged per row.
- HTTP rate limits and transient failures use retry/backoff.
- By default, the tool stops on first live write failure; pass `--continue-on-error` to process the full batch.

## Notes
- Dry-run is default for safety.
- This MVP updates one rich text property only.
- Use a sandbox database first before production pages.
