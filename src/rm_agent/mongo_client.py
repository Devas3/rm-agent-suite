

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient
from pymongo.errors import PyMongoError

DIGEST_TTL_HOURS = 24  # Briefings are valid for 24 hours

from rm_agent.settings import AtlasSettings

logger = logging.getLogger(__name__)


class DocumentStore:
    def __init__(self) -> None:
        self.settings = AtlasSettings()
        self._mongo_client: MongoClient[dict[str, Any]] | None = None
        self._local_store_path = Path("./data/mongo_store.json")
        self._init_backend()

    def _init_backend(self) -> None:
        uri = (self.settings.atlas_uri or "").strip()
        if uri:
            try:
                client: MongoClient[dict[str, Any]] = MongoClient(
                    uri, serverSelectionTimeoutMS=3000
                )
                client.admin.command("ping")
                self._mongo_client = client
                logger.info("Connected to MongoDB Atlas: %s", self.settings.atlas_db)
                return
            except PyMongoError as e:
                logger.warning(
                    "Could not connect to MongoDB Atlas (%s). Falling back to local storage.", e
                )

        self._local_store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._local_store_path.exists():
            self._local_store_path.write_text("{}", encoding="utf-8")
        logger.info("Using local document storage at %s", self._local_store_path)

    def _read_local(self) -> dict[str, Any]:
        if not self._local_store_path.exists():
            return {}
        try:
            return json.loads(self._local_store_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_local(self, data: dict[str, Any]) -> None:
        self._local_store_path.parent.mkdir(parents=True, exist_ok=True)
        self._local_store_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def save_digest(self, rm_id: str, digest_data: dict[str, Any]) -> str:
        """Saves or updates the latest morning digest for an RM."""
        record = {
            **digest_data,
            "rm_id": rm_id,
            "updated_at": datetime.now().isoformat(),
        }

        if self._mongo_client is not None:
            db = self._mongo_client[self.settings.atlas_db]
            coll = db["morning_digests"]
            coll.update_one({"rm_id": rm_id}, {"$set": record}, upsert=True)
            return rm_id

        # Local storage fallback
        data = self._read_local()
        digests = data.setdefault("morning_digests", {})
        digests[rm_id] = record
        self._write_local(data)
        return rm_id

    def get_latest_digest(self, rm_id: str) -> dict[str, Any] | None:
        """Retrieves the latest morning digest for an RM.

        Returns None if no digest exists or if it is older than DIGEST_TTL_HOURS.
        """
        doc: dict[str, Any] | None = None

        if self._mongo_client is not None:
            db = self._mongo_client[self.settings.atlas_db]
            doc = db["morning_digests"].find_one({"rm_id": rm_id}, {"_id": 0})
        else:
            data = self._read_local()
            doc = data.get("morning_digests", {}).get(rm_id)

        if doc is None:
            return None

        # TTL check — reject briefings older than DIGEST_TTL_HOURS
        generated_at_str = doc.get("generated_at") or doc.get("updated_at")
        if generated_at_str:
            try:
                generated_at = datetime.fromisoformat(str(generated_at_str))
                # Make timezone-aware if naive
                if generated_at.tzinfo is None:
                    generated_at = generated_at.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - generated_at
                if age > timedelta(hours=DIGEST_TTL_HOURS):
                    logger.info(
                        "Digest for %s is %.1f hours old (TTL=%dh) — treating as expired.",
                        rm_id, age.total_seconds() / 3600, DIGEST_TTL_HOURS,
                    )
                    return None
            except (ValueError, TypeError):
                pass  # If we can't parse the date, serve it anyway

        return doc

    def list_all_digests(self) -> list[dict[str, Any]]:
        """List all digests currently stored."""
        if self._mongo_client is not None:
            db = self._mongo_client[self.settings.atlas_db]
            return list(db["morning_digests"].find({}, {"_id": 0}))

        data = self._read_local()
        return list(data.get("morning_digests", {}).values())


_global_store: DocumentStore | None = None


def get_document_store() -> DocumentStore:
    global _global_store
    if _global_store is None:
        _global_store = DocumentStore()
    return _global_store
