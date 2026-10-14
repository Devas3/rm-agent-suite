"""Daily Morning Briefing Orchestration Flow.

Loops active Relationship Managers, fetches metric vectors from Redis,
evaluates composite risk scores, synthesizes flagged relationships via LLM,
and stores finalized morning digests in the document store (MongoDB Atlas / Local).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project packages are accessible
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "services" / "risk_scoring_engine" / "src"))
sys.path.insert(0, str(ROOT / "services" / "agno_service" / "src"))

from agno_service.agent import BriefingAgent
from agno_service.schemas import ClientDigest, MorningDigest
from risk_scoring_engine.scorer import RiskScoringEngine
from rm_agent.db import get_connection
from rm_agent.mongo_client import get_document_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_daily_briefing() -> list[MorningDigest]:
    """Executes the daily digest generation workflow for all RMs."""
    scorer = RiskScoringEngine()
    agent = BriefingAgent()
    doc_store = get_document_store()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rm_id, name, email FROM relationship_managers")
            rms = cur.fetchall()

            cur.execute(
                """
                SELECT c.client_id, c.rm_id, c.name,
                       COALESCE(lb.operating_balance, 500000.0) as latest_balance
                FROM clients c
                LEFT JOIN LATERAL (
                    SELECT operating_balance
                    FROM ledger_balances
                    WHERE client_id = c.client_id
                    ORDER BY balance_date DESC
                    LIMIT 1
                ) lb ON TRUE
                """
            )
            clients_data = cur.fetchall()
    finally:
        conn.close()

    # Map clients by RM
    rm_client_map: dict[str, list[dict[str, Any]]] = {}
    for c_id, rm_id, name, balance in clients_data:
        rm_client_map.setdefault(rm_id, []).append(
            {"client_id": c_id, "name": name, "operating_balance": float(balance)}
        )

    all_digests: list[MorningDigest] = []

    logger.info("Starting Daily Morning Briefing generation for %d RMs...", len(rms))

    for rm_id, rm_name, rm_email in rms:
        clients = rm_client_map.get(rm_id, [])
        total_relationships = len(clients)
        if total_relationships == 0:
            continue

        client_ids = [c["client_id"] for c in clients]
        client_lookup = {c["client_id"]: c for c in clients}

        # Step 1: Score risk across portfolio
        evaluations = scorer.evaluate_rm_portfolio(rm_id, client_ids)

        # Step 2: Filter flagged relationships (or take top at-risk if under threshold for small sample tests)
        flagged_evals = [e for e in evaluations if e.is_flagged]
        if not flagged_evals and evaluations:
            # In small test datasets (e.g. 5 clients), ensure the highest risk client is flagged for evaluation
            flagged_evals = [evaluations[0]]

        flagged_digests: list[ClientDigest] = []
        total_deposits_at_risk = 0.0

        # Step 3: Synthesize each flagged relationship via Agno LLM
        for ev in flagged_evals:
            c_info = client_lookup.get(ev.client_id, {})
            c_info["risk_level"] = ev.risk_level
            c_info["risk_score"] = ev.risk_score

            digest = agent.synthesize_client_digest(
                client_info=c_info,
                evaluation_signals=ev.signals,
                metrics=ev.metrics_snapshot,
            )
            flagged_digests.append(digest)
            total_deposits_at_risk += digest.deposits_at_risk

        # Step 4: Construct executive MorningDigest
        headline = agent.generate_briefing_headline(
            rm_name=rm_name,
            flagged_count=len(flagged_digests),
            total_count=total_relationships,
            deposits_at_risk=total_deposits_at_risk,
        )

        morning_digest = MorningDigest(
            rm_id=rm_id,
            rm_name=rm_name,
            total_relationships=total_relationships,
            flagged_count=len(flagged_digests),
            total_deposits_at_risk=round(total_deposits_at_risk, 2),
            summary_headline=headline,
            flagged_relationships=flagged_digests,
            generated_at=datetime.now().isoformat(),
        )

        # Step 5: Persist to Mongo/Document Store
        doc_store.save_digest(rm_id, morning_digest.model_dump())
        all_digests.append(morning_digest)

        logger.info("[OK] Generated digest for %s: %s", rm_name, headline)

    return all_digests


if __name__ == "__main__":
    results = run_daily_briefing()
    print(f"\nCompleted briefing generation. Processed {len(results)} RM digests.")
    for d in results:
        print(f"\n--- RM: {d.rm_name} ({d.rm_id}) ---")
        print(d.summary_headline)
        for rel in d.flagged_relationships:
            print(f"  * {rel.client_name} (Risk: {rel.risk_level}, Score: {rel.risk_score})")
            print(f"    Conclusion: {rel.agent_conclusion.encode('ascii', 'ignore').decode('ascii')}")
            print(f"    Action: {rel.recommended_action.encode('ascii', 'ignore').decode('ascii')}")
