"""Migrate the local auth registry to managed PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.auth import migrate_sqlite_to_postgres, user_count  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("D4D_DATABASE_URL"), help="Managed PostgreSQL connection string; prefer an environment variable.")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or D4D_DATABASE_URL is required")
    source_count = user_count(ROOT)
    migrated = migrate_sqlite_to_postgres(ROOT, args.database_url)
    print(f"Migrated {migrated} users and their scopes to PostgreSQL (source users: {source_count}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
