"""Official Apache Kafka broker interface using confluent-kafka."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from rm_agent.settings import KafkaSettings

# Topic names matching the system architecture
TOPIC_LEDGER_BALANCE_UPDATES = "ledger.balance.updates"
TOPIC_PAYMENT_WIRE_LOGS = "payment.wire.logs"
TOPIC_CRM_RM_INTERACTIONS = "crm.rm.interactions"
TOPIC_CD_LOAN_UPDATES = "cd.loan.updates"

ALL_TOPICS = (
    TOPIC_LEDGER_BALANCE_UPDATES,
    TOPIC_PAYMENT_WIRE_LOGS,
    TOPIC_CRM_RM_INTERACTIONS,
    TOPIC_CD_LOAN_UPDATES,
)

_producer: Producer | None = None
_consumer: Consumer | None = None


def _get_kafka_conf() -> dict[str, str]:
    settings = KafkaSettings()  # type: ignore[call-arg]
    ca_path = Path(settings.kafka_ssl_ca_location).resolve()
    conf = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        "security.protocol": settings.kafka_security_protocol,
        "sasl.mechanism": settings.kafka_sasl_mechanism,
        "sasl.username": settings.kafka_sasl_username,
        "sasl.password": settings.kafka_sasl_password,
    }
    if ca_path.exists():
        conf["ssl.ca.location"] = str(ca_path)
    return conf


def ensure_topics_exist() -> None:
    """Check and create Kafka topics in Aiven if they do not exist."""
    conf = _get_kafka_conf()
    admin = AdminClient(conf)
    metadata = admin.list_topics(timeout=10.0)
    existing_topics = set(metadata.topics.keys())

    topics_to_create = [
        NewTopic(topic=t, num_partitions=1, replication_factor=1)
        for t in ALL_TOPICS
        if t not in existing_topics
    ]
    if topics_to_create:
        fs = admin.create_topics(topics_to_create)
        for topic, f in fs.items():
            try:
                f.result()
            except Exception:
                pass


def get_producer() -> Producer:
    global _producer
    if _producer is None:
        conf = _get_kafka_conf()
        conf["client.id"] = "rm-agent-producer"
        _producer = Producer(conf)
    return _producer


def produce(
    topic: str,
    message: dict[str, Any],
    headers: list[tuple[str, bytes]] | None = None,
) -> None:
    """Publish one JSON message onto a Kafka topic."""
    producer = get_producer()
    payload = json.dumps(message).encode("utf-8")
    producer.produce(topic=topic, value=payload, headers=headers)
    producer.poll(0)


def flush_producer() -> None:
    if _producer is not None:
        _producer.flush()


def get_consumer(topics: tuple[str, ...] = ALL_TOPICS) -> Consumer:
    global _consumer
    if _consumer is None:
        settings = KafkaSettings()  # type: ignore[call-arg]
        conf = _get_kafka_conf()
        conf.update({
            "group.id": settings.kafka_consumer_group,
            "enable.auto.commit": "false",
            "auto.offset.reset": "earliest",
        })
        _consumer = Consumer(conf)
        
        # Direct assignment of partition 0 for all topics to bypass dynamic rebalance delay
        partitions_to_assign = []
        for t in topics:
            meta = _consumer.list_topics(t, timeout=10.0)
            if t in meta.topics:
                for pid in meta.topics[t].partitions:
                    partitions_to_assign.append(TopicPartition(t, pid))
        if partitions_to_assign:
            committed = _consumer.committed(partitions_to_assign, timeout=10.0)
            assignments = [
                TopicPartition(partition.topic, partition.partition, 0 if partition.offset < 0 else partition.offset)
                for partition in committed
            ]
            _consumer.assign(assignments)
    return _consumer


def consume_batch(max_messages: int = 5000) -> list[tuple[Any, dict[str, Any]]]:
    """Consume all available messages directly across assigned partitions."""
    consumer = get_consumer()
    messages = []
    
    while len(messages) < max_messages:
        msg = consumer.poll(timeout=0.3)
        if msg is None:
            break
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            break
        try:
            data = json.loads(msg.value().decode("utf-8"))
            messages.append((msg, data))
        except Exception:
            continue

    return messages


def commit_messages() -> None:
    """Commit offsets for consumed messages."""
    if _consumer is not None:
        _consumer.commit(asynchronous=False)
