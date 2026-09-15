"""Fetch Notion tasks directly from Notion API (non-MCP path).

This tool queries the Notion API database endpoint directly, returning raw page
payloads suitable for normalization by mcp_fetch_tasks.py.

Usage:
    python tools/api_fetch_tasks.py --database-id e929797b8cff82f8aba601bbc7b2172a --output .tmp/reports/api-pages.json
    python tools/api_fetch_tasks.py --database-id e929797b8cff82f8aba601bbc7b2172a --page-size 50 --max-pages 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available, continue with system env vars
    pass


def get_notion_token() -> str:
    """Get Notion API token from environment, preferring NOTION_API_KEY."""
    token = os.getenv("NOTION_API_KEY")
    if token:
        return token
    token = os.getenv("NOTION_TOKEN")
    if token:
        return token
    raise ValueError(
        "No Notion API token found. Set NOTION_API_KEY (preferred) or NOTION_TOKEN environment variable."
    )


class APIFetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        pages_fetched: int = 0,
        partial_pages: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.pages_fetched = pages_fetched
        self.partial_pages = partial_pages or []


def fetch_pages_from_api(
    database_id: str,
    token: str,
    page_size: int = 100,
    max_pages: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch pages from Notion API database query endpoint."""
    if requests is None:
        raise APIFetchError(
            "requests library required. Install with: pip install requests",
            code="missing_dependency",
            retryable=False,
        )

    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    all_pages: list[dict[str, Any]] = []
    cursor: str | None = None
    page_count = 0
    retries = 3

    while True:
        payload = {"page_size": page_size}
        if cursor:
            payload["start_cursor"] = cursor

        response = None
        for attempt in range(retries):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=30)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt + 1 == retries:
                    raise APIFetchError(
                        f"network error while fetching Notion pages: {exc}",
                        code="network_error",
                        retryable=True,
                        pages_fetched=len(all_pages),
                        partial_pages=list(all_pages),
                    ) from exc
                time.sleep(2 ** attempt)
                continue

            if response.status_code == 429:
                if attempt + 1 == retries:
                    raise APIFetchError(
                        "notion api rate limit exceeded",
                        code="rate_limited",
                        retryable=True,
                        pages_fetched=len(all_pages),
                        partial_pages=list(all_pages),
                    )
                retry_after = response.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                time.sleep(wait_seconds)
                continue

            if response.status_code >= 500:
                if attempt + 1 == retries:
                    raise APIFetchError(
                        f"notion api server error: {response.status_code}",
                        code="server_error",
                        retryable=True,
                        pages_fetched=len(all_pages),
                        partial_pages=list(all_pages),
                    )
                time.sleep(2 ** attempt)
                continue

            break

        if response is None:
            raise APIFetchError(
                "notion api request did not return a response",
                code="request_failed",
                retryable=False,
                pages_fetched=len(all_pages),
                partial_pages=list(all_pages),
            )

        if response.status_code in {401, 403}:
            raise APIFetchError(
                "notion authentication failed. verify NOTION_API_KEY/NOTION_TOKEN and database access",
                code="auth_error",
                retryable=False,
                pages_fetched=len(all_pages),
                partial_pages=list(all_pages),
            )
        if response.status_code >= 400:
            raise APIFetchError(
                f"notion api request failed with status {response.status_code}",
                code="request_error",
                retryable=False,
                pages_fetched=len(all_pages),
                partial_pages=list(all_pages),
            )

        data = response.json()
        pages = data.get("results", [])

        # Extract only properties needed: id, url, and properties object
        for page in pages:
            # Build Notion URL from database and page IDs
            page_id = page.get("id", "").replace("-", "")
            if not page_id:
                continue
            page_url = f"https://www.notion.so/{page_id}"

            # Emit page in shape expected by mcp_fetch_tasks.py
            all_pages.append({
                "id": page.get("id"),
                "url": page_url,
                "properties": page.get("properties", {}),
            })

        page_count += 1
        if max_pages and page_count >= max_pages:
            break

        cursor = data.get("next_cursor")
        if not cursor:
            break

    return all_pages, False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Notion tasks via direct API")
    parser.add_argument(
        "--database-id",
        required=True,
        help="Notion database ID to query",
    )
    parser.add_argument(
        "--output",
        default=".tmp/reports/api-pages.json",
        help="Path to write raw pages JSON",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Page size for API pagination (default 100, max 100)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages to fetch (default all)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write partial output if pagination fails after at least one page.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Validate page size
    if args.page_size < 1 or args.page_size > 100:
        print(
            json.dumps({
                "ok": False,
                "error": f"page-size must be between 1 and 100, got {args.page_size}",
            })
        )
        return 2

    try:
        token = get_notion_token()
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    partial = False
    try:
        pages, partial = fetch_pages_from_api(
            database_id=args.database_id,
            token=token,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
    except APIFetchError as exc:
        if args.allow_partial and exc.pages_fetched > 0:
            partial = True
            pages = exc.partial_pages
            print(
                json.dumps(
                    {
                        "ok": False,
                        "warning": str(exc),
                        "code": exc.code,
                        "retryable": exc.retryable,
                        "partial": True,
                        "pages_fetched": exc.pages_fetched,
                    }
                ),
                file=sys.stderr,
            )
        else:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"API fetch failed: {exc}",
                        "code": exc.code,
                        "retryable": exc.retryable,
                        "pages_fetched": exc.pages_fetched,
                    }
                )
            )
            return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"API fetch failed: {exc}"}))
        return 1

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=True, indent=2)
    except Exception as exc:
        print(
            json.dumps({
                "ok": False,
                "error": f"Failed to write output: {exc}",
            })
        )
        return 1

    print(
        json.dumps({
            "ok": True,
            "database_id": args.database_id,
            "output": str(output_path),
            "pages_fetched": len(pages),
            "page_size": args.page_size,
            "max_pages": args.max_pages,
            "partial": partial,
        })
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
