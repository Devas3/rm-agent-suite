"""Apply infra/neon/schema.sql to the configured Neon database.

Usage:
    uv run python scripts/apply_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rm_agent.db import get_connection  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "infra" / "neon" / "schema.sql"


def main() -> None:
    schema_sql = SCHEMA_PATH.read_text()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
        print(f"Applied schema from {SCHEMA_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
