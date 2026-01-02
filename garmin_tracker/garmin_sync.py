from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .storage import read_json, write_json


@dataclass(frozen=True)
class SyncConfig:
    max_activity_pages: int = 20
    page_size: int = 50
    days_back: int = 1460
    sleep_seconds: float = 0.6


class GarminSyncService:
    """High-level sync layer.

    It uses feature-detection for garminconnect client methods so it works across
    different versions of the library.
    """

    def __init__(self, client: Any, *, user_id: str, data_dir: str = "data", config: SyncConfig | None = None) -> None:
        self._client = client
        self._user_id = user_id
        self._data_dir = data_dir
        self._config = config or SyncConfig()

    # -------- Introspection --------

    def dump_available_methods(self) -> list[str]:
        names = sorted([n for n in dir(self._client) if not n.startswith("_")])
        write_json(f"{self._data_dir}/{self._user_id}_garmin_methods.json", {"methods": names})
        return names

    # -------- Activities --------

    def sync_activities(self, progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
        cutoff = datetime.now() - timedelta(days=self._config.days_back)

        activities_path = f"{self._data_dir}/{self._user_id}_activities.json"
        existing_list = read_json(activities_path, [])
        existing: list[dict[str, Any]] = existing_list if isinstance(existing_list, list) else []
        existing_ids: set[int] = set()
        existing_min_dt: datetime | None = None
        for a in existing:
            if not isinstance(a, dict):
                continue
            aid = a.get("activityId")
            if isinstance(aid, int):
                existing_ids.add(aid)

            dt = _parse_activity_datetime(a)
            if dt is not None:
                existing_min_dt = dt if existing_min_dt is None else min(existing_min_dt, dt)

        # If existing data doesn't reach the new cutoff (e.g. you previously synced 6 months
        # and now want 2 years), we must NOT stop early just because the first page overlaps.
        need_backfill = existing_min_dt is None or existing_min_dt > cutoff

        if progress:
            progress(0.0, "Récupération des activités…")

        new_activities: list[dict[str, Any]] = []
        overlap_hits = 0

        get_activities = getattr(self._client, "get_activities", None)
        if not callable(get_activities):
            raise RuntimeError("garmin client missing get_activities")

        for page in range(self._config.max_activity_pages):
            start = page * self._config.page_size
            if progress:
                progress(
                    min(55.0, (page / max(1, self._config.max_activity_pages)) * 55.0),
                    f"Récupération des activités… page {page + 1}",
                )
            raw_batch = get_activities(start, self._config.page_size)
            raw_list = raw_batch if isinstance(raw_batch, list) else []
            batch: list[dict[str, Any]] = [x for x in raw_list if isinstance(x, dict)]
            if not batch:
                break

            for a in batch:
                aid = a.get("activityId")
                if isinstance(aid, int) and aid in existing_ids:
                    overlap_hits += 1
                    continue
                new_activities.append(a)

            # Fast-path: if first page contains no new activities, stop early.
            if (not need_backfill) and page == 0 and existing_ids and not new_activities and overlap_hits >= int(0.8 * len(batch)):
                break

            # stop early if last activity is older than cutoff
            last = batch[-1]
            dt = _parse_activity_datetime(last)
            if dt and dt < cutoff:
                break

            # If we see enough already-known activities, we reached the overlap zone.
            if (not need_backfill) and overlap_hits >= 25:
                break

            time.sleep(self._config.sleep_seconds)

        merged = _merge_activities(new_activities, existing, cutoff=cutoff)

        write_json(activities_path, merged, indent=4)

        if progress:
            progress(60.0, f"Activités: {len(new_activities)} nouvelles (merge en cours)…")

        # Sync details bundle (extend existing file)
        details_path = f"{self._data_dir}/{self._user_id}_activity_details.json"
        details_raw = read_json(details_path, {"activities": {}})
        details_dict: dict[str, Any] = details_raw if isinstance(details_raw, dict) else {}
        activities_map_raw = details_dict.get("activities")
        activities_map: dict[str, Any] = activities_map_raw if isinstance(activities_map_raw, dict) else {}

        # Sync details for new activities only (plus any missing details for merged set)
        to_enrich: list[dict[str, Any]] = []
        for a in merged:
            aid = a.get("activityId")
            if aid is None:
                continue
            key = str(aid)
            if key in activities_map:
                continue

            to_enrich.append(a)

        total = len(to_enrich)
        for idx, a in enumerate(to_enrich):
            aid = a.get("activityId")
            if aid is None:
                continue
            key = str(aid)

            if progress and total:
                progress(
                    60.0 + (idx / total) * 40.0,
                    f"Détails activités… {idx + 1}/{total}",
                )

            bundle: dict[str, Any] = {"summary": a}
            bundle.update(self._fetch_activity_extras(aid))
            activities_map[key] = bundle
            time.sleep(self._config.sleep_seconds)

        write_json(details_path, {"activities": activities_map}, indent=2)

        if progress:
            progress(100.0, "Activités synchronisées")

        return {"activities_saved": len(merged), "new_activities": len(new_activities), "details_saved": len(activities_map)}

    def _fetch_activity_extras(self, activity_id: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}

        out["details"] = _maybe_call(self._client, "get_activity_details", activity_id)

        # Optional extras (method names vary by garminconnect version)
        out["splits"] = _maybe_call(self._client, "get_activity_splits", activity_id)
        out["typed_splits"] = _maybe_call(self._client, "get_activity_typed_splits", activity_id)
        out["hr_zones"] = _maybe_call(self._client, "get_activity_hr_in_timezones", activity_id)
        out["laps"] = _maybe_call(self._client, "get_activity_laps", activity_id)

        # Drop empty keys to keep file readable
        return {k: v for k, v in out.items() if v not in (None, {}, [], "")}

    # -------- Health (daily bundles) --------

    def sync_health_days(self, progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
        days_path = f"{self._data_dir}/{self._user_id}_health_daily.json"
        existing_raw = read_json(days_path, {"days": {}})
        existing_dict: dict[str, Any] = existing_raw if isinstance(existing_raw, dict) else {}
        days_map_raw = existing_dict.get("days")
        days_map: dict[str, Any] = days_map_raw if isinstance(days_map_raw, dict) else {}

        start = date.today() - timedelta(days=self._config.days_back)
        end = date.today()

        missing: list[date] = []
        for d in _date_range(start, end):
            key = d.isoformat()
            if key not in days_map:
                missing.append(d)

        if progress:
            progress(0.0, f"Santé: {len(missing)} jour(s) à synchroniser…")

        saved = 0
        total = len(missing)
        for idx, d in enumerate(missing):
            key = d.isoformat()
            if progress and total:
                progress(min(90.0, (idx / total) * 90.0), f"Santé… {idx + 1}/{total} ({key})")

            bundle = self._fetch_health_day(d)
            if bundle:
                days_map[key] = bundle
                saved += 1

            time.sleep(self._config.sleep_seconds)

        write_json(days_path, {"days": days_map}, indent=2)

        # Keep backward compatibility: write the legacy stats-only list used by GarminHealthManager
        stats_list = []
        for key, bundle in sorted(days_map.items()):
            stats = bundle.get("stats")
            if isinstance(stats, dict):
                x = dict(stats)
                x["date"] = key
                stats_list.append(x)
        write_json(f"{self._data_dir}/{self._user_id}_health.json", stats_list, indent=4)

        if progress:
            progress(100.0, "Santé synchronisée")

        return {"days_saved": saved, "days_total": len(days_map), "missing": total}

    def _fetch_health_day(self, d: date) -> dict[str, Any]:
        bundle: dict[str, Any] = {}

        # Core day stats
        stats = _call_with_date(self._client, "get_stats", d)
        if isinstance(stats, dict) and stats:
            bundle["stats"] = stats

        # Optional health endpoints (best-effort)
        bundle["sleep"] = _call_with_date(self._client, "get_sleep_data", d)
        bundle["stress"] = _call_with_date(self._client, "get_stress_data", d)
        bundle["steps"] = _call_with_date(self._client, "get_steps_data", d)
        bundle["spo2"] = _call_with_date(self._client, "get_spo2_data", d)
        bundle["respiration"] = _call_with_date(self._client, "get_respiration_data", d)
        bundle["hydration"] = _call_with_date(self._client, "get_hydration_data", d)
        bundle["wellness"] = _call_with_date(self._client, "get_wellness_data", d)
        bundle["body_battery"] = _call_with_date(self._client, "get_body_battery", d)

        # remove empties
        bundle = {k: v for k, v in bundle.items() if v not in (None, {}, [], "")}
        return bundle


def _merge_activities(
    new_items: list[dict[str, Any]],
    existing_items: list[dict[str, Any]],
    *,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Merge & de-dup activities, keeping newest-first order.

    - Keeps existing data ("DB") and only adds new items.
    - Enforces the days_back cutoff to keep files reasonably sized.
    """

    by_id: dict[int, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []

    def consider(a: dict[str, Any]) -> None:
        if not isinstance(a, dict):
            return
        dt = _parse_activity_datetime(a)
        if dt and dt < cutoff:
            return
        aid = a.get("activityId")
        if not isinstance(aid, int):
            return
        if aid in by_id:
            return
        by_id[aid] = a
        ordered.append(a)

    # New items first (garmin returns newest -> oldest)
    for a in new_items:
        consider(a)
    for a in existing_items:
        consider(a)

    # Sort to guarantee stable newest-first output
    ordered.sort(key=lambda x: (_parse_activity_datetime(x) or datetime.min), reverse=True)
    return ordered


def _date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur = cur + timedelta(days=1)


def _maybe_call(client: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(client, method_name, None)
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logging.debug("garmin call failed %s: %s", method_name, e)
        return None


def _call_with_date(client: Any, method_name: str, d: date) -> Any:
    fn = getattr(client, method_name, None)
    if not callable(fn):
        return None

    candidates: list[Callable[[], Any]] = [
        lambda: fn(d),
        lambda: fn(datetime(d.year, d.month, d.day)),
        lambda: fn(d.isoformat()),
        lambda: fn(datetime(d.year, d.month, d.day).isoformat()),
    ]

    for attempt in candidates:
        try:
            out = attempt()
            if out not in (None, {}, [], ""):
                return out
        except Exception:
            continue
    return None


def _parse_activity_datetime(activity: dict[str, Any]) -> datetime | None:
    raw = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    if not raw or not isinstance(raw, str):
        return None
    try:
        # Example: "2025-01-20 18:33:21" or ISO format
        raw_norm = raw.replace("Z", "")
        raw_norm = raw_norm.replace("T", " ")
        return datetime.fromisoformat(raw_norm)
    except Exception:
        return None
