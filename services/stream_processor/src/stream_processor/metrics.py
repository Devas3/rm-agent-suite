
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from rm_agent.db import get_connection  # noqa: E402

DEPOSIT_WINDOW_SHORT_DAYS = 60
DEPOSIT_WINDOW_LONG_DAYS = 90


def _pct_change(latest: float, earlier: float) -> float | None:
    if earlier == 0:
        return None
    return round((latest - earlier) / earlier * 100, 2)


def compute_metrics(client_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rm_id, profitability_score FROM clients WHERE client_id = %s",
                (client_id,),
            )
            client_row = cur.fetchone()
            if client_row is None:
                return None
            rm_id, profitability_score = client_row

            cur.execute(
                """
                SELECT balance_date, operating_balance
                FROM ledger_balances
                WHERE client_id = %s
                ORDER BY balance_date ASC
                """,
                (client_id,),
            )
            balance_rows = cur.fetchall()

            cur.execute(
                """
                SELECT log_date, wire_volume, ach_volume
                FROM wire_payment_logs
                WHERE client_id = %s
                ORDER BY log_date ASC
                """,
                (client_id,),
            )
            wire_rows = cur.fetchall()

            cur.execute(
                """
                SELECT MAX(interaction_date) FROM crm_interactions WHERE client_id = %s
                """,
                (client_id,),
            )
            last_interaction = cur.fetchone()[0]

            cur.execute(
                """
                SELECT MIN(maturity_date)
                FROM cd_loan_accounts
                WHERE client_id = %s AND status = 'active' AND maturity_date IS NOT NULL
                """,
                (client_id,),
            )
            nearest_maturity = cur.fetchone()[0]
    finally:
        conn.close()

    today = date.today()

    balances = [float(b) for _, b in balance_rows]
    deposit_60d_change = None
    deposit_90d_change = None
    deposit_zscore = None
    if balances:
        latest_balance = balances[-1]
        if len(balances) >= DEPOSIT_WINDOW_SHORT_DAYS:
            deposit_60d_change = _pct_change(latest_balance, balances[-DEPOSIT_WINDOW_SHORT_DAYS])
        if len(balances) >= 2:
            deposit_90d_change = _pct_change(latest_balance, balances[0])
        if len(balances) >= 5:
            mean = float(np.mean(balances))
            std = float(np.std(balances))
            deposit_zscore = round((latest_balance - mean) / std, 3) if std > 0 else 0.0

    wire_volumes = [float(w) for _, w, _ in wire_rows]
    ach_volumes = [float(a) for _, _, a in wire_rows]
    wire_60d_change = None
    ach_60d_change = None
    if len(wire_volumes) >= DEPOSIT_WINDOW_SHORT_DAYS:
        wire_60d_change = _pct_change(wire_volumes[-1], wire_volumes[-DEPOSIT_WINDOW_SHORT_DAYS])
        ach_60d_change = _pct_change(ach_volumes[-1], ach_volumes[-DEPOSIT_WINDOW_SHORT_DAYS])

    days_since_last_interaction = (today - last_interaction).days if last_interaction else None
    cd_maturity_days = (nearest_maturity - today).days if nearest_maturity else None

    return {
        "rm_id": rm_id,
        "client_id": client_id,
        "profitability_score": float(profitability_score),
        "deposit_balance_60d_pct_change": deposit_60d_change,
        "deposit_balance_90d_pct_change": deposit_90d_change,
        "deposit_zscore": deposit_zscore,
        "wire_volume_60d_pct_change": wire_60d_change,
        "ach_volume_60d_pct_change": ach_60d_change,
        "days_since_last_interaction": days_since_last_interaction,
        "cd_maturity_days": cd_maturity_days,
        "computed_at": datetime.now().isoformat(),
    }
