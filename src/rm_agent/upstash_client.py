

from __future__ import annotations

import json
from typing import Any

import httpx

from rm_agent.settings import UpstashSettings


class UpstashRedisError(RuntimeError):
    pass


def _client_and_url() -> tuple[httpx.Client, str]:
    settings = UpstashSettings()  # type: ignore[call-arg]
    headers = {"Authorization": f"Bearer {settings.upstash_redis_rest_token}"}
    return httpx.Client(headers=headers, timeout=10.0), settings.upstash_redis_rest_url


def _run_command(*command: Any) -> Any:
    client, url = _client_and_url()
    with client:
        response = client.post(url, json=list(command))
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise UpstashRedisError(body["error"])
        return body.get("result")


def set_json(key: str, value: dict[str, Any]) -> None:
    _run_command("SET", key, json.dumps(value))


def get_json(key: str) -> dict[str, Any] | None:
    raw = _run_command("GET", key)
    if raw is None:
        return None
    return json.loads(raw)


def delete(key: str) -> None:
    _run_command("DEL", key)


def ping() -> bool:
    return _run_command("PING") == "PONG"
