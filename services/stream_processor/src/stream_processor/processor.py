

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_repo_root / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rm_agent import queue_broker as qb  # noqa: E402
from rm_agent import upstash_client  # noqa: E402
from rm_agent.observability import configure_tracing, extract_trace_context  # noqa: E402
from rm_agent.settings import CDCSettings  # noqa: E402

from stream_processor.metrics import compute_metrics  # noqa: E402

POLL_SLEEP_SECONDS = 5
tracer = configure_tracing("rm-agent-stream-processor")


def process_once() -> int:
    qb.ensure_topics_exist()
    batch = qb.consume_batch(max_messages=5000)
    if not batch:
        return 0

    touched_clients: dict[str, list[Any]] = {}
    for msg, data in batch:
        row = data.get("row", {})
        client_id = row.get("client_id")
        if client_id:
            touched_clients.setdefault(client_id, []).append(
                extract_trace_context(msg.headers())
            )

    for client_id, parent_contexts in touched_clients.items():
        parent_context = parent_contexts[0] if parent_contexts else None
        with tracer.start_as_current_span(
            "client.metrics_and_redis_update", context=parent_context
        ) as span:
            span.set_attribute("client.id", client_id)
            span.set_attribute("event.count", len(parent_contexts))
            metrics = compute_metrics(client_id)
            if metrics is None:
                span.set_attribute("processing.status", "client_not_found")
                continue
            if metrics.get("deposit_zscore") is not None:
                span.set_attribute("risk.deposit_zscore", float(metrics["deposit_zscore"]))
            key = f"metrics:{metrics['rm_id']}:{client_id}"
            with tracer.start_as_current_span("redis.write") as redis_span:
                redis_span.set_attribute("redis.key", key)
                redis_span.set_attribute("client.id", client_id)
                upstash_client.set_json(key, metrics)

    qb.commit_messages()
    return len(touched_clients)


def main() -> None:
    run_once = "--once" in sys.argv

    if run_once:
        count = process_once()
        print(f"Recomputed metrics for {count} client(s).")
        return

    settings = CDCSettings()  # type: ignore[call-arg]
    print(f"Consuming Kafka every {POLL_SLEEP_SECONDS}s. Ctrl+C to stop.")
    while True:
        count = process_once()
        if count:
            print(f"Recomputed metrics for {count} client(s).")
        time.sleep(min(POLL_SLEEP_SECONDS, settings.cdc_poll_interval_seconds))


if __name__ == "__main__":
    main()
