import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv is not installed. Run: pip install -r requirements.txt")
    sys.exit(2)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    env_file = repo_root / ".env"

    if env_file.exists():
        load_dotenv(env_file)
        env_status = "present"
    else:
        env_status = "missing"

    notion_api_key = os.getenv("NOTION_API_KEY")
    notion_token = os.getenv("NOTION_TOKEN")
    missing = []
    if not notion_api_key and not notion_token:
        missing.append("NOTION_API_KEY|NOTION_TOKEN")

    print("WAT Health Check")
    print(f"- repo: {repo_root}")
    print(f"- .env: {env_status}")
    print(f"- required_keys_missing: {len(missing)}")

    if missing:
        print("- missing_keys:")
        for key in missing:
            print(f"  - {key}")
        return 1

    print("- status: ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
