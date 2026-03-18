import json
import os
from typing import Any, Dict, List, Optional

try:
    import redis
except Exception:
    redis = None


class SessionStore:

    def __init__(self):
        self._enabled = False
        self._redis = None
        self._mem: Dict[str, Dict[str, Any]] = {}

        url = (os.environ.get("REDIS_URL") or "").strip()
        if not url or not redis:
            return

        try:
            self._redis = redis.Redis.from_url(url, decode_responses=True)
            self._redis.ping()
            self._enabled = True
        except Exception:
            self._redis = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ttl_seconds(self) -> int:
        try:
            return int(os.environ.get("SESSION_TTL_SECONDS", "21600"))
        except Exception:
            return 21600

    def _key(self, call_sid: str) -> str:
        return f"twilio:session:{call_sid}"

    def get(self, call_sid: str) -> Dict[str, Any]:
        if not call_sid:
            return {"messages": [], "greeted": False}

        if self._enabled and self._redis is not None:
            raw = self._redis.get(self._key(call_sid))
            if raw:
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        obj.setdefault("messages", [])
                        obj.setdefault("greeted", False)
                        return obj
                except Exception:
                    pass
            return {"messages": [], "greeted": False}

        # Memory fallback
        sess = self._mem.get(call_sid)
        if not sess:
            sess = {"messages": [], "greeted": False}
            self._mem[call_sid] = sess
        return sess

    def save(self, call_sid: str, session: Dict[str, Any]) -> None:
        if not call_sid:
            return

        #Normalize
        greeted = bool(session.get("greeted", False))
        messages = session.get("messages") or []
        if not isinstance(messages, list):
            messages = []

        payload = {"greeted": greeted, "messages": messages}

        if isinstance(call_context, dict) and call_context:
            payload["call_context"] = call_context

        no_input_count = session.get("no_input_count", 0)
        if isinstance(no_input_count, int):
            payload["no_input_count"] = no_input_count

        if self._enabled and self._redis is not None:
            try:
                self._redis.setex(self._key(call_sid), self._ttl_seconds(), json.dumps(payload))
                return
            except Exception:
                pass

        self._mem[call_sid] = payload

    def clear(self, call_sid: str) -> None:
        if not call_sid:
            return
        if self._enabled and self._redis is not None:
            try:
                self._redis.delete(self._key(call_sid))
            except Exception:
                pass
        self._mem.pop(call_sid, None)
