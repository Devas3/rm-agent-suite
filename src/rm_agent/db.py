"""Shared Neon Postgres connection helper."""

from __future__ import annotations

import psycopg2
from psycopg2.extensions import connection as PGConnection

from rm_agent.settings import NeonSettings


def get_connection() -> PGConnection:
    settings = NeonSettings()  # type: ignore[call-arg]
    return psycopg2.connect(settings.neon_database_url, connect_timeout=10)
