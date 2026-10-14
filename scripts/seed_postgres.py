
from __future__ import annotations

import random
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from faker import Faker
from psycopg2.extras import execute_batch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rm_agent.db import get_connection  # noqa: E402

NUM_RMS = 2
NUM_CLIENTS = 5
HISTORY_DAYS = 7
RNG_SEED = 42

fake = Faker()
Faker.seed(RNG_SEED)
random.seed(RNG_SEED)
np.random.seed(RNG_SEED)

RISK_ARCHETYPES = ["healthy", "declining", "dormant", "maturity_risk"]
RISK_WEIGHTS = [0.55, 0.20, 0.15, 0.10]  # most relationships are fine


def gen_balance_series(archetype: str, base_balance: float) -> list[float]:
    """90 days of daily operating balance, oldest first."""
    if archetype == "declining":
        total_decline = random.uniform(0.35, 0.48)
        trend = np.linspace(0, -total_decline, HISTORY_DAYS)
    elif archetype == "maturity_risk":
        total_decline = random.uniform(0.10, 0.20)
        trend = np.linspace(0, -total_decline, HISTORY_DAYS)
    else:
        trend = np.linspace(0, random.uniform(-0.03, 0.05), HISTORY_DAYS)
    noise = np.random.normal(0, 0.02, HISTORY_DAYS)
    series = base_balance * (1 + trend + noise)
    return [round(float(max(v, 0)), 2) for v in series]


def gen_wire_ach_series(archetype: str, base_wire: float, base_ach: float) -> list[tuple[float, float]]:
    if archetype == "declining":
        wire_decline = random.uniform(0.40, 0.55)
        wire_trend = np.linspace(0, -wire_decline, HISTORY_DAYS)
    else:
        wire_trend = np.linspace(0, random.uniform(-0.05, 0.10), HISTORY_DAYS)
    ach_trend = np.linspace(0, random.uniform(-0.05, 0.10), HISTORY_DAYS)
    wire_noise = np.random.normal(0, 0.10, HISTORY_DAYS)
    ach_noise = np.random.normal(0, 0.03, HISTORY_DAYS)
    wire = base_wire * (1 + wire_trend + wire_noise)
    ach = base_ach * (1 + ach_trend + ach_noise)
    return [(round(float(max(w, 0)), 2), round(float(max(a, 0)), 2)) for w, a in zip(wire, ach, strict=True)]


def gen_last_interaction_gap(archetype: str) -> int:
    if archetype == "dormant":
        return random.randint(60, 110)
    return random.randint(1, 45)


def gen_maturity_days(archetype: str) -> int | None:
    if archetype == "maturity_risk":
        return random.randint(5, 20)
    if random.random() < 0.3:
        return random.randint(30, 300)
    return None


def main() -> None:
    conn = get_connection()
    today = date.today()
    print("Connecting to Neon PostgreSQL...")

    try:
        with conn:
            with conn.cursor() as cur:
                print(f"Generating {NUM_RMS} Relationship Managers...")
                rm_ids = []
                rm_records = []
                for i in range(NUM_RMS):
                    rm_id = f"RM-{i + 1:03d}"  # RM-001, RM-002, ...
                    rm_ids.append(rm_id)
                    rm_records.append((rm_id, fake.name(), fake.company_email()))

                execute_batch(
                    cur,
                    "INSERT INTO relationship_managers (rm_id, name, email) VALUES (%s, %s, %s) ON CONFLICT (rm_id) DO NOTHING",
                    rm_records,
                    page_size=100,
                )
                print(f"[OK] Inserted {len(rm_records)} RMs.")

                print(f"Generating {NUM_CLIENTS} client relationships & 90-day time series...")
                client_records = []
                balance_records = []
                wire_records = []
                crm_records = []
                cd_records = []

                for idx in range(1, NUM_CLIENTS + 1):
                    client_id = f"client_{uuid.uuid4().hex[:8]}"
                    rm_id = random.choice(rm_ids)
                    archetype = random.choices(RISK_ARCHETYPES, weights=RISK_WEIGHTS, k=1)[0]
                    profitability = round(random.uniform(20, 98), 2)
                    start_date = today - timedelta(days=random.randint(180, 4000))

                    client_records.append((client_id, rm_id, fake.company(), start_date, profitability))

                    base_balance = random.uniform(200_000, 6_000_000)
                    balances = gen_balance_series(archetype, base_balance)
                    base_wire = random.uniform(20_000, 500_000)
                    base_ach = random.uniform(10_000, 150_000)
                    wire_ach = gen_wire_ach_series(archetype, base_wire, base_ach)

                    for offset, (balance, (wire, ach)) in enumerate(zip(balances, wire_ach, strict=True)):
                        day = today - timedelta(days=HISTORY_DAYS - offset)
                        balance_records.append((client_id, day, balance))
                        wire_records.append((client_id, day, wire, ach))

                    gap_days = gen_last_interaction_gap(archetype)
                    last_interaction_date = today - timedelta(days=gap_days)
                    num_interactions = random.randint(2, 6)
                    for i in range(num_interactions):
                        interaction_date = last_interaction_date - timedelta(days=random.randint(0, 30) * i)
                        crm_records.append(
                            (
                                client_id,
                                rm_id,
                                interaction_date,
                                random.choice(["call", "email", "meeting"]),
                                fake.sentence(),
                            )
                        )

                    maturity_days = gen_maturity_days(archetype)
                    if maturity_days is not None:
                        cd_records.append(
                            (
                                client_id,
                                "CD",
                                round(random.uniform(100_000, 2_000_000), 2),
                                today + timedelta(days=maturity_days),
                                "active",
                            )
                        )

                    if idx % 50 == 0 or idx == NUM_CLIENTS:
                        print(f"  Processed {idx}/{NUM_CLIENTS} clients in memory...")

                print(f"Writing {len(client_records)} clients to Neon...")
                execute_batch(
                    cur,
                    "INSERT INTO clients (client_id, rm_id, name, relationship_start_date, profitability_score) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (client_id) DO NOTHING",
                    client_records,
                    page_size=500,
                )
                print("[OK] Clients inserted.")

                print(f"Writing {len(balance_records)} daily balance records (batch)...")
                execute_batch(
                    cur,
                    "INSERT INTO ledger_balances (client_id, balance_date, operating_balance) VALUES (%s, %s, %s) ON CONFLICT (client_id, balance_date) DO NOTHING",
                    balance_records,
                    page_size=1000,
                )
                print("[OK] Ledger balances inserted.")

                print(f"Writing {len(wire_records)} wire/ACH records (batch)...")
                execute_batch(
                    cur,
                    "INSERT INTO wire_payment_logs (client_id, log_date, wire_volume, ach_volume) VALUES (%s, %s, %s, %s) ON CONFLICT (client_id, log_date) DO NOTHING",
                    wire_records,
                    page_size=1000,
                )
                print("[OK] Wire/ACH logs inserted.")

                print(f"Writing {len(crm_records)} CRM interactions (batch)...")
                execute_batch(
                    cur,
                    "INSERT INTO crm_interactions (client_id, rm_id, interaction_date, interaction_type, notes) VALUES (%s, %s, %s, %s, %s)",
                    crm_records,
                    page_size=500,
                )
                print("[OK] CRM interactions inserted.")

                print(f"Writing {len(cd_records)} CD/loan accounts (batch)...")
                execute_batch(
                    cur,
                    "INSERT INTO cd_loan_accounts (client_id, account_type, principal_amount, maturity_date, status) VALUES (%s, %s, %s, %s, %s)",
                    cd_records,
                    page_size=500,
                )
                print("[OK] CD/loan accounts inserted.")

        print(f"\nSUCCESS: Seeded {NUM_RMS} RMs, {NUM_CLIENTS} clients, and {len(balance_records)} history rows into Neon.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
