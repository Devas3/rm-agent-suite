"""Flushes Kafka producer on CDC poller completion."""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from psycopg2.extras import RealDictCursor  # noqa: E402

from rm_agent import queue_broker as qb  # noqa: E402
from rm_agent.db import get_connection  # noqa: E402
from rm_agent.observability import configure_tracing, inject_trace_headers  # noqa: E402
from rm_agent.settings import CDCSettings  # noqa: E402

tracer = configure_tracing("rm-agent-cdc-poller")

TABLE_TOPIC_MAP = {
    "ledger_balances": qb.TOPIC_LEDGER_BALANCE_UPDATES,
    "wire_payment_logs": qb.TOPIC_PAYMENT_WIRE_LOGS,
    "crm_interactions": qb.TOPIC_CRM_RM_INTERACTIONS,
    "cd_loan_accounts": qb.TOPIC_CD_LOAN_UPDATES,
}


def _json_safe(row: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            safe[key] = float(value)
        elif isinstance(value, (date, datetime)):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe


def _load_checkpoints(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_checkpoints(path: Path, checkpoints: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoints, indent=2))


def poll_once(checkpoint_path: Path) -> int:
    """Run one poll cycle across all source tables. Returns event count published."""
    qb.ensure_topics_exist()
    checkpoints = _load_checkpoints(checkpoint_path)
    conn = get_connection()
    published = 0

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for table, topic in TABLE_TOPIC_MAP.items():
                last_seen = checkpoints.get(table, "1970-01-01T00:00:00+00:00")
                cur.execute(
                    f"SELECT * FROM {table} WHERE updated_at > %s ORDER BY updated_at ASC",  # noqa: S608
                    (last_seen,),
                )
                rows = cur.fetchall()
                for row in rows:
                    row_dict = _json_safe(dict(row))
                    event_id = f"{table}:{row_dict.get('id', row_dict.get('client_id', 'unknown'))}:{row_dict['updated_at']}"
                    with tracer.start_as_current_span("cdc.publish") as span:
                        span.set_attribute("event.id", event_id)
                        span.set_attribute("source.table", table)
                        span.set_attribute("source.updated_at", row_dict["updated_at"])
                        if row_dict.get("client_id"):
                            span.set_attribute("client.id", str(row_dict["client_id"]))
                        qb.produce(
                            topic,
                            {"event_id": event_id, "table": table, "row": row_dict},
                            headers=inject_trace_headers(),
                        )
                    published += 1
                    checkpoints[table] = row_dict["updated_at"]
    finally:
        conn.close()
        qb.flush_producer()

    _save_checkpoints(checkpoint_path, checkpoints)
    return published


def main() -> None:
    settings = CDCSettings()  # type: ignore[call-arg]
    checkpoint_path = Path(settings.cdc_checkpoint_path)
    run_once = "--once" in sys.argv

    if run_once:
        count = poll_once(checkpoint_path)
        print(f"Published {count} change event(s) to Kafka.")
        return

    print(f"Polling Neon every {settings.cdc_poll_interval_seconds}s and publishing to Kafka. Ctrl+C to stop.")
    while True:
        count = poll_once(checkpoint_path)
        if count:
            print(f"Published {count} change event(s) to Kafka.")
        time.sleep(settings.cdc_poll_interval_seconds)


if __name__ == "__main__":
    main()
