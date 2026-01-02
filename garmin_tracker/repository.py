from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from .storage import read_json, write_json


@dataclass
class _CacheEntry:
    mtime: float
    value: Any


class JsonRepository:
    """Small in-memory cache on top of JSON files.

    Goal: reuse local data ("database") and avoid re-reading or re-downloading.
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            keys = [k for k in self._cache.keys() if k.startswith(prefix)]
            for k in keys:
                self._cache.pop(k, None)

    def _get_cached(self, key: str, path: str, default: Any) -> Any:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = -1

        with self._lock:
            hit = self._cache.get(key)
            if hit and hit.mtime == mtime:
                return hit.value

        value = read_json(path, default)
        with self._lock:
            self._cache[key] = _CacheEntry(mtime=mtime, value=value)
        return value

    def activities(self, user_id: str) -> list[dict[str, Any]]:
        path = os.path.join(self._data_dir, f"{user_id}_activities.json")
        data = self._get_cached(f"activities:{user_id}", path, [])
        return data if isinstance(data, list) else []

    def activity_details(self, user_id: str) -> dict[str, Any]:
        path = os.path.join(self._data_dir, f"{user_id}_activity_details.json")
        data = self._get_cached(f"activity_details:{user_id}", path, {"activities": {}})
        return data if isinstance(data, dict) else {"activities": {}}

    def health_stats(self, user_id: str) -> list[dict[str, Any]]:
        path = os.path.join(self._data_dir, f"{user_id}_health.json")
        data = self._get_cached(f"health_stats:{user_id}", path, [])
        return data if isinstance(data, list) else []

    def health_daily(self, user_id: str) -> dict[str, Any]:
        path = os.path.join(self._data_dir, f"{user_id}_health_daily.json")
        data = self._get_cached(f"health_daily:{user_id}", path, {"days": {}})
        return data if isinstance(data, dict) else {"days": {}}

    def profile(self, user_id: str) -> dict[str, Any]:
        path = os.path.join(self._data_dir, f"{user_id}_profile.json")
        data = self._get_cached(f"profile:{user_id}", path, {})
        return data if isinstance(data, dict) else {}

    def save_profile(self, user_id: str, profile: dict[str, Any]) -> None:
        path = os.path.join(self._data_dir, f"{user_id}_profile.json")
        write_json(path, profile)
        with self._lock:
            self._cache.pop(f"profile:{user_id}", None)
