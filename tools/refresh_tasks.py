"""Run deterministic task refresh pipeline in one command.

Pipeline:
1) Optional normalize: mcp_fetch_tasks.py (when MCP payload is provided)
2) Generate report: organize_notion_tasks.py --validate
3) Regression validation: validate_task_report.py (with optional --strict)
4) Optional mutation mode: batch_update_descriptions.py

Usage:
    python tools/refresh_tasks.py --mode mcp-local --source .tmp/reports/tasks-source.json --delta
    python tools/refresh_tasks.py --mode mcp-live --mcp-input .tmp/reports/mcp-pages.json --delta --strict
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-command task refresh pipeline")
    parser.add_argument(
        "--mode",
        choices=["mcp-live", "mcp-local", "direct-api", "batch-descriptions"],
        default="direct-api",
        help="Mode to run. Use batch-descriptions for bulk rich_text updates.",
    )
    parser.add_argument(
        "--mcp-input",
        help="Optional MCP pages payload JSON. When provided, source JSON is regenerated first.",
    )
    parser.add_argument(
        "--source",
        default=".tmp/reports/tasks-source.json",
        help="Normalized source tasks JSON path",
    )
    parser.add_argument(
        "--output",
        default=".tmp/reports/tasks-organized.md",
        help="Output report markdown path",
    )
    parser.add_argument(
        "--previous",
        default=".tmp/reports/tasks-organized.prev.md",
        help="Previous report backup path used for regression checks",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable strict regression failure when tasks are dropped",
    )
    parser.add_argument(
        "--delta",
        action="store_true",
        help="Enable cache-aware delta mode in normalization.",
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Force full regeneration even when delta cache reports no changes.",
    )
    parser.add_argument(
        "--cache-file",
        default=".tmp/reports/tasks-cache.json",
        help="Cache metadata file used for delta mode.",
    )
    parser.add_argument(
        "--allow-fallback-local",
        action="store_true",
        help="Allow fallback from mcp-live to mcp-local when live input is unavailable.",
    )
    parser.add_argument(
        "--allow-fallback-mcp",
        action="store_true",
        help="Allow fallback from direct-api to mcp-live.",
    )
    parser.add_argument(
        "--exclude-status",
        action="append",
        default=["done"],
        help="Status to exclude while normalizing MCP input (repeatable)",
    )
    parser.add_argument(
        "--database-id",
        default="e929797b8cff82f8aba601bbc7b2172a",
        help="Notion database ID for direct-api mode",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size for API pagination in direct-api mode (default 100, max 100)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum pages to fetch in direct-api mode (default all)",
    )
    parser.add_argument(
        "--batch-input",
        help="Input CSV/JSON for batch-descriptions mode",
    )
    parser.add_argument(
        "--description-property-name",
        default="description",
        help="Target rich_text property name for batch-descriptions mode",
    )
    parser.add_argument(
        "--batch-apply",
        action="store_true",
        help="Apply live writes in batch-descriptions mode (default is dry-run)",
    )
    parser.add_argument(
        "--batch-audit-file",
        default=".tmp/reports/mutation-audit.jsonl",
        help="Audit JSONL path for batch-descriptions mode",
    )
    parser.add_argument(
        "--batch-max-retries",
        type=int,
        default=3,
        help="Retry count for batch-descriptions mode",
    )
    parser.add_argument(
        "--batch-rate-limit-rps",
        type=float,
        default=3.0,
        help="Rate limit (requests/sec) for batch-descriptions mode",
    )
    parser.add_argument(
        "--batch-continue-on-error",
        action="store_true",
        help="Continue processing rows after row-level failures in batch-descriptions mode",
    )
    parser.add_argument(
        "--batch-max-rows",
        type=int,
        default=None,
        help="Optional row cap for batch-descriptions mode",
    )
    return parser.parse_args()


def run_cmd(command: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "command": command,
        "elapsed_seconds": round(elapsed, 4),
    }


def parse_json_stdout(stdout: str) -> dict[str, Any] | None:
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def usage_error(message: str, hint: str | None = None, mode: str | None = None) -> int:
    result: dict[str, Any] = {"ok": False, "error": message, "exit_code": 2}
    if hint:
        result["hint"] = hint
    if mode:
        result["mode"] = mode
    print(json.dumps(result))
    return 2


def main() -> int:
    started_total = time.perf_counter()
    args = parse_args()

    if args.mode == "batch-descriptions":
        if not args.batch_input:
            return usage_error(
                "mode batch-descriptions requires --batch-input",
                hint="Pass --batch-input .tmp/reports/description-updates.csv (or .json).",
                mode=args.mode,
            )

        mutation_cmd = [
            sys.executable,
            "tools/batch_update_descriptions.py",
            "--input",
            args.batch_input,
            "--property-name",
            args.description_property_name,
            "--audit-file",
            args.batch_audit_file,
            "--max-retries",
            str(args.batch_max_retries),
            "--rate-limit-rps",
            str(args.batch_rate_limit_rps),
        ]
        if args.batch_apply:
            mutation_cmd.append("--apply")
        if args.batch_continue_on_error:
            mutation_cmd.append("--continue-on-error")
        if args.batch_max_rows is not None:
            mutation_cmd.extend(["--max-rows", str(args.batch_max_rows)])

        mutation_result = run_cmd(mutation_cmd)
        total_elapsed = time.perf_counter() - started_total
        summary = {
            "ok": mutation_result["returncode"] == 0,
            "mode_requested": args.mode,
            "mode_used": "batch-descriptions",
            "results": {
                "mutate": parse_json_stdout(mutation_result["stdout"]),
            },
            "steps": [
                {
                    "step": "mutate",
                    "elapsed_seconds": mutation_result["elapsed_seconds"],
                    "returncode": mutation_result["returncode"],
                    "stderr": mutation_result["stderr"],
                }
            ],
            "timings": {
                "steps": [{"step": "mutate", "elapsed_seconds": mutation_result["elapsed_seconds"]}],
                "total_seconds": round(total_elapsed, 4),
            },
        }
        if mutation_result["returncode"] != 0 and summary["results"]["mutate"] is None:
            summary["error"] = "batch-descriptions mode failed"
        print(json.dumps(summary))
        return 0 if mutation_result["returncode"] == 0 else 1

    source_path = Path(args.source)
    output_path = Path(args.output)
    previous_path = Path(args.previous)
    cache_path = Path(args.cache_file)

    steps: list[dict[str, Any]] = []
    fallback_used: str | None = None
    mode_used = args.mode

    # Backup current report for regression checking.
    if output_path.exists():
        previous_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_path, previous_path)

    # Contract validation and mode fallback resolution.
    if args.mode == "direct-api":
        api_pages_path = Path(".tmp/reports/api-pages.json")
        # Attempt direct API fetch
        fetch_cmd = [
            sys.executable,
            "tools/api_fetch_tasks.py",
            "--database-id",
            args.database_id,
            "--output",
            str(api_pages_path),
            "--page-size",
            str(args.page_size),
        ]
        if args.max_pages is not None:
            fetch_cmd.extend(["--max-pages", str(args.max_pages)])
        
        fetch_result = run_cmd(fetch_cmd)
        steps.append({"step": "fetch", **fetch_result})
        
        if fetch_result["returncode"] != 0:
            # API fetch failed; check if fallback is allowed
            if not args.allow_fallback_mcp:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "mode": "direct-api",
                            "fallback_used": fallback_used,
                            "error": "direct-api fetch failed and fallback is disabled",
                            "hint": "Retry with --allow-fallback-mcp or fix NOTION_API_KEY/NOTION_TOKEN.",
                            "steps": steps,
                        }
                    )
                )
                return 1
            # Fallback to mcp-live; pop the failed fetch step
            print(
                "warning: direct-api fetch failed; falling back to mcp-live",
                file=sys.stderr,
            )
            mode_used = "mcp-live"
            fallback_used = "direct-api->mcp-live"
            steps.pop()
        else:
            # API fetch succeeded; use it as MCP input for normalization
            args.mcp_input = str(api_pages_path)

    if mode_used == "mcp-live" and not args.mcp_input:
        if args.allow_fallback_local and source_path.exists() and fallback_used is None:
            mode_used = "mcp-local"
            fallback_used = "mcp-live->mcp-local"
        elif args.allow_fallback_local and source_path.exists() and fallback_used is not None:
            return usage_error(
                "refusing multi-hop fallback chain after direct-api failure",
                hint="Provide --mcp-input for mcp-live or run --mode mcp-local explicitly.",
                mode=args.mode,
            )
        else:
            return usage_error(
                "mode mcp-live requires --mcp-input in the current implementation",
                hint="Pass --mcp-input .tmp/reports/mcp-pages.json or use --mode mcp-local with --source.",
                mode=args.mode,
            )

    if mode_used == "mcp-local" and not args.mcp_input and not source_path.exists():
        return usage_error(
            "mode mcp-local requires either --mcp-input or an existing --source file",
            hint="Provide --source .tmp/reports/tasks-source.json or --mcp-input .tmp/reports/mcp-pages.json.",
            mode=args.mode,
        )

    # Optional source normalization from fetched payload.
    if args.mcp_input and mode_used in {"mcp-live", "mcp-local", "direct-api"}:
        normalize_cmd = [
            sys.executable,
            "tools/mcp_fetch_tasks.py",
            "--input",
            args.mcp_input,
            "--output",
            str(source_path),
            "--cache-file",
            str(cache_path),
        ]
        if args.delta:
            normalize_cmd.append("--delta")
        if args.force_full:
            normalize_cmd.append("--force-full")
        for status in args.exclude_status:
            normalize_cmd.extend(["--exclude-status", status])

        normalize_result = run_cmd(normalize_cmd)
        steps.append({"step": "normalize", **normalize_result})
        if normalize_result["returncode"] != 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": mode_used,
                        "fallback_used": fallback_used,
                        "steps": steps,
                    }
                )
            )
            return 1

    if not source_path.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": mode_used,
                    "fallback_used": fallback_used,
                    "error": f"source file not found: {source_path}",
                    "hint": "Provide --mcp-input or create source JSON first.",
                }
            )
        )
        return 1

    # Short-circuit no-change runs in delta mode when report already exists.
    normalize_step = next((step for step in steps if step["step"] == "normalize"), None)
    normalize_payload = parse_json_stdout(normalize_step["stdout"]) if normalize_step else None
    if (
        args.delta
        and not args.force_full
        and normalize_payload is not None
        and normalize_payload.get("changed") is False
        and output_path.exists()
        and not args.strict
    ):
        validate_cmd = [
            sys.executable,
            "tools/validate_task_report.py",
            "--current",
            str(output_path),
        ]
        validate_result = run_cmd(validate_cmd)
        steps.append({"step": "validate", **validate_result})
        if validate_result["returncode"] != 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": mode_used,
                        "fallback_used": fallback_used,
                        "error": "delta short-circuit validation failed",
                        "steps": steps,
                    }
                )
            )
            return 1

        total_elapsed = time.perf_counter() - started_total
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode_requested": args.mode,
                    "mode_used": mode_used,
                    "fallback_used": fallback_used,
                    "source": str(source_path),
                    "output": str(output_path),
                    "previous": str(previous_path) if previous_path.exists() else None,
                    "strict": args.strict,
                    "delta": args.delta,
                    "force_full": args.force_full,
                    "cache_file": str(cache_path),
                    "short_circuit": "no_source_changes",
                    "results": {
                        "normalize": normalize_payload,
                        "generate": None,
                        "validate": parse_json_stdout(validate_result["stdout"]),
                    },
                    "timings": {
                        "steps": [{"step": step["step"], "elapsed_seconds": step["elapsed_seconds"]} for step in steps],
                        "total_seconds": round(total_elapsed, 4),
                    },
                }
            )
        )
        return 0

    # Generate report with integrity checks.
    generate_cmd = [
        sys.executable,
        "tools/organize_notion_tasks.py",
        "--source-file",
        str(source_path),
        "--output",
        str(output_path),
        "--validate",
    ]
    generate_result = run_cmd(generate_cmd)
    steps.append({"step": "generate", **generate_result})
    if generate_result["returncode"] != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": mode_used,
                    "fallback_used": fallback_used,
                    "steps": steps,
                }
            )
        )
        return 1

    # Validate current report and optional regression against backup.
    validate_cmd = [
        sys.executable,
        "tools/validate_task_report.py",
        "--current",
        str(output_path),
    ]
    if previous_path.exists():
        validate_cmd.extend(["--previous", str(previous_path)])
    if args.strict:
        validate_cmd.append("--strict")

    validate_result = run_cmd(validate_cmd)
    steps.append({"step": "validate", **validate_result})
    if validate_result["returncode"] != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": mode_used,
                    "fallback_used": fallback_used,
                    "steps": steps,
                }
            )
        )
        return 1

    total_elapsed = time.perf_counter() - started_total
    generate_step = next((step for step in steps if step["step"] == "generate"), None)
    validate_step = next((step for step in steps if step["step"] == "validate"), None)

    summary = {
        "ok": True,
        "mode_requested": args.mode,
        "mode_used": mode_used,
        "fallback_used": fallback_used,
        "source": str(source_path),
        "output": str(output_path),
        "previous": str(previous_path) if previous_path.exists() else None,
        "strict": args.strict,
        "delta": args.delta,
        "force_full": args.force_full,
        "cache_file": str(cache_path),
        "results": {
            "normalize": parse_json_stdout(normalize_step["stdout"]) if normalize_step else None,
            "generate": parse_json_stdout(generate_step["stdout"]) if generate_step else None,
            "validate": parse_json_stdout(validate_step["stdout"]) if validate_step else None,
        },
        "timings": {
            "steps": [{"step": step["step"], "elapsed_seconds": step["elapsed_seconds"]} for step in steps],
            "total_seconds": round(total_elapsed, 4),
        },
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
