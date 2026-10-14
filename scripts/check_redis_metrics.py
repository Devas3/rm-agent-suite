"""Fetch and pretty-print one or all metrics keys from Upstash for manual testing.

Usage:
    uv run python scripts/check_redis_metrics.py                # list a sample from Neon + fetch
    uv run python scripts/check_redis_metrics.py metrics:rm_xxx:client_yyy
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rm_agent import upstash_client  # noqa: E402
from rm_agent.db import get_connection  # noqa: E402


def main() -> None:
    if len(sys.argv) > 1:
        key = sys.argv[1]
        value = upstash_client.get_json(key)
        print(json.dumps(value, indent=2) if value else f"No value found for {key}")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rm_id, client_id FROM clients LIMIT 5")
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No clients found — run scripts/seed_postgres.py first.")
        return

    for rm_id, client_id in rows:
        key = f"metrics:{rm_id}:{client_id}"
        value = upstash_client.get_json(key)
        print(f"\n{key}")
        print(json.dumps(value, indent=2) if value else "  (not yet computed)")


if __name__ == "__main__":
    main()
