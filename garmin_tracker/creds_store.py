from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class GarminCredentials:
    user_id: str
    email: str
    password: str


class InMemoryCredentialsStore:
    """Server-side credential store.

    We keep credentials off the client cookie. A session holds only a random key.
    """

    def __init__(self, ttl: timedelta = timedelta(hours=8)) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._items: dict[str, tuple[GarminCredentials, datetime]] = {}

    def create_session(self, creds: GarminCredentials) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._items[token] = (creds, datetime.utcnow())
        return token

    def get(self, token: str | None) -> GarminCredentials | None:
        if not token:
            return None
        with self._lock:
            item = self._items.get(token)
            if not item:
                return None
            creds, created_at = item
            if datetime.utcnow() - created_at > self._ttl:
                self._items.pop(token, None)
                return None
            return creds

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._items.pop(token, None)

    def cleanup(self) -> None:
        now = datetime.utcnow()
        with self._lock:
            expired = [k for k, (_, t0) in self._items.items() if now - t0 > self._ttl]
            for k in expired:
                self._items.pop(k, None)
