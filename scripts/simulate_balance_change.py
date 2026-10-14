"""Insert or update one ledger balance to exercise the prototype pipeline."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rm_agent.db import get_connection  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--balance-date", required=True, type=date.fromisoformat)
    parser.add_argument("--balance", required=True, type=float)
    args = parser.parse_args()

    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ledger_balances
                    (client_id, balance_date, operating_balance)
                VALUES (%s, %s, %s)
                ON CONFLICT (client_id, balance_date)
                DO UPDATE SET operating_balance = EXCLUDED.operating_balance
                """,
                (args.client_id, args.balance_date, args.balance),
            )
    finally:
        conn.close()

    print("Ledger balance change committed to PostgreSQL.")


if __name__ == "__main__":
    main()