import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from pymongo import MongoClient
except Exception:
    MongoClient = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationStore:
    def __init__(self):
        self.debug = os.environ.get("MONGODB_DEBUG", "").strip() in ("1", "true", "True", "yes", "YES")
        self.enabled = False
        self._client = None
        self._collection: Optional[Any] = None

        uri = os.environ.get("MONGODB_URI")
        if not uri or not MongoClient:
            return

        db_name = os.environ.get("MONGODB_DB", "twilio")
        coll_name = os.environ.get("MONGODB_COLLECTION", "conversations")

        try:
            self._client = MongoClient(uri, serverSelectionTimeoutMS=4000)
            self._client.admin.command("ping")

            db = self._client[db_name]
            self._collection = db[coll_name]
            self.enabled = True
        except Exception:
            self.enabled = False
            self._client = None
            self._collection = None
            return

        # TTL index
        try:
            self._collection.create_index("callSid", unique=True)
            self._collection.create_index("expiresAt", expireAfterSeconds=0)
            self._collection.create_index("lastActivityAt")
        except Exception:
            pass

    def ensure_call(self, call_sid: str, from_number: Optional[str] = None, to_number: Optional[str] = None):
        if not self.enabled or self._collection is None or not call_sid:
            return
        now = utcnow()
        update: Dict[str, Any] = {
            "$setOnInsert": {
                "callSid": call_sid,
                "startedAt": now,
                "turns": [],
                "status": "in_progress",
            },
            "$set": {
                "lastActivityAt": now,
            },
        }
        if from_number is not None:
            update.setdefault("$set", {})["from"] = from_number
        if to_number is not None:
            update.setdefault("$set", {})["to"] = to_number

        try:
            self._collection.update_one({"callSid": call_sid}, update, upsert=True)
        except Exception:
            pass

    def update_status(self, call_sid: str, status: str):
        if not self.enabled or self._collection is None or not call_sid:
            return
        now = utcnow()
        try:
            self._collection.update_one(
                {"callSid": call_sid},
                {
                    "$set": {
                        "status": status,
                        "lastActivityAt": now,
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    def mark_ended(self, call_sid: str, status: str = "completed", retention_hours: int = 24):
        if not self.enabled or self._collection is None or not call_sid:
            return
        now = utcnow()
        expires_at = now + timedelta(hours=retention_hours)
        try:
            self._collection.update_one(
                {"callSid": call_sid},
                {
                    "$set": {
                        "status": status,
                        "endedAt": now,
                        "expiresAt": expires_at,
                        "lastActivityAt": now,
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    def save_full_conversation(self, call_sid: str, turns: List[Dict[str, Any]], call_context: Optional[Dict[str, Any]] = None):
        if not self.enabled or self._collection is None or not call_sid:
            return
        now = utcnow()
        update_set: Dict[str, Any] = {
            "turns": turns or [],
            "lastActivityAt": now,
        }
        if call_context:
            update_set["callContext"] = call_context
        try:
            self._collection.update_one(
                {"callSid": call_sid},
                {"$set": update_set},
                upsert=True,
            )
        except Exception:
            pass

    def save_structured_response(self, call_sid: str, structured: Dict[str, Any]):
        if not self.enabled or self._collection is None or not call_sid:
            return
        now = utcnow()
        try:
            self._collection.update_one(
                {"callSid": call_sid},
                {
                    "$set": {
                        "structuredResponse": structured,
                        "structuredResponseAt": now,
                        "lastActivityAt": now,
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    def get_call_document(self, call_sid: str, include_turns: bool = False) -> Optional[Dict[str, Any]]:
        if not self.enabled or self._collection is None or not call_sid:
            return None
        projection = {
            "_id": 0,
            "callSid": 1,
            "from": 1,
            "to": 1,
            "status": 1,
            "startedAt": 1,
            "lastActivityAt": 1,
            "endedAt": 1,
            "expiresAt": 1,
            "structuredResponse": 1,
        }
        if include_turns:
            projection["turns"] = 1
        try:
            doc = self._collection.find_one({"callSid": call_sid}, projection)
        except Exception:
            return None
        return self._serialize_for_json(doc)

    def _serialize_for_json(self, obj: Any) -> Any:
        if obj is None:
            return None
        if isinstance(obj, datetime):
            return obj.astimezone(timezone.utc).isoformat()
        if isinstance(obj, dict):
            return {k: self._serialize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize_for_json(v) for v in obj]
        return obj
