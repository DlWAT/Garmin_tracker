from __future__ import annotations

import os
import datetime as dt
from functools import wraps
from typing import Any
from collections.abc import Sequence
import re
import uuid
import threading
import time
import unicodedata
import secrets
import shutil

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from passlib.hash import argon2

from .activity_manager import GarminActivityManager, _generate_pace_ticks
from .client_manager import GarminClientHandler, GarminLoginError
from .health_manager import GarminHealthManager
from .training_analysis import TrainingAnalysis

from .repository import JsonRepository
from .task_manager import TaskManager
from .echarts import YBand, write_timeseries_chart_html

from .creds_store import GarminCredentials, InMemoryCredentialsStore
from .storage import read_json, write_json
from .db import db_session
from .models import GarminAccount, User


_SESSION_KEY = "garmin_session_token"


URL_PREFIX = (os.getenv("GARMIN_TRACKER_URL_PREFIX") or "/mytrainer").rstrip("/") or ""


# Minimal admin support (no DB migration).
# Primary rule: DB user id 1 is admin.
# Fallback rule: allowlist by user_id.
_ADMIN_DB_IDS = {1}
_ADMIN_USER_IDS = {"adri"}


def _canonical_sport_type(type_key: str) -> str | None:
    """Map Garmin typeKey variants to our 4 canonical sports."""

    if not type_key:
        return None

    k = str(type_key)

    running = {
        "running",
        "treadmill_running",
        "trail_running",
        "track_running",
        "virtual_running",
        "indoor_running",
    }
    cycling = {
        "cycling",
        "road_biking",
        "mountain_biking",
        "gravel_cycling",
        "indoor_cycling",
        "virtual_cycling",
        "e_bike_fitness",
        "e_bike_mountain",
    }
    swimming = {
        "swimming",
        "lap_swimming",
        "pool_swimming",
        "open_water_swimming",
    }
    strength = {
        "strength_training",
    }

    if k in running:
        return "running"
    if k in cycling:
        return "cycling"
    if k in swimming:
        return "swimming"
    if k in strength:
        return "strength_training"
    return None


def _ensure_folders() -> None:
    for folder in ["static/activity", "static/health", "static/training", "data", "instance"]:
        os.makedirs(folder, exist_ok=True)


def _normalize_user_id_from_pseudo(pseudo: str) -> str:
    # Stable ID safe for filenames and URLs.
    s = (pseudo or "").strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_-]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:80] or "user")


def _normalize_pseudo(pseudo: str) -> str | None:
    p = (pseudo or "").strip()
    if not p:
        return None
    if len(p) < 2 or len(p) > 80:
        return None
    return p


def _normalize_pin(pin: str) -> str | None:
    p = (pin or "").strip()
    if not p:
        return None
    if len(p) < 4 or len(p) > 12:
        return None
    if not p.isdigit():
        return None
    return p


def _db_get_user_by_email(email: str) -> User | None:
    try:
        with db_session() as db:
            return db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    except SQLAlchemyError:
        return None


def _db_get_user_by_user_id(user_id: str) -> User | None:
    try:
        with db_session() as db:
            return db.execute(select(User).where(User.user_id == user_id)).scalar_one_or_none()
    except SQLAlchemyError:
        return None


def _db_list_users() -> list[User]:
    try:
        with db_session() as db:
            return list(db.execute(select(User).order_by(User.display_name.asc())).scalars().all())
    except SQLAlchemyError:
        return []


def _db_set_user_pin(*, user_id: str, pin: str) -> bool:
    try:
        with db_session() as db:
            u = db.execute(select(User).where(User.user_id == user_id)).scalar_one_or_none()
            if not u:
                return False
            u.pin_hash = argon2.hash(pin)
            return True
    except SQLAlchemyError:
        return False


def _db_delete_user(*, user_id: str) -> bool:
    try:
        with db_session() as db:
            u = db.execute(select(User).where(User.user_id == user_id)).scalar_one_or_none()
            if not u:
                return False
            db.delete(u)
            return True
    except SQLAlchemyError:
        return False


def _db_create_user(*, email: str, pin: str, pseudo: str) -> User:
    user_id = _normalize_user_id_from_pseudo(pseudo)
    u = User(email=email, display_name=pseudo, user_id=user_id, pin_hash=argon2.hash(pin))
    ga = GarminAccount(garmin_email=email)
    u.garmin_account = ga
    with db_session() as db:
        db.add(u)
    return u


def _db_verify_user(*, email: str, pin: str) -> User | None:
    u = _db_get_user_by_email(email)
    if not u:
        return None
    try:
        ok = argon2.verify(pin, u.pin_hash)
    except Exception:
        ok = False
    return u if ok else None


def _purge_plotly_static_html() -> None:
    """Remove stale Plotly-generated chart HTML files.

    Graphs are now generated with ECharts. This prevents accidentally serving old
    Plotly HTML blobs that might still exist on disk from previous runs.
    """

    for folder in ["static/activity", "static/health"]:
        if not os.path.isdir(folder):
            continue

        for name in os.listdir(folder):
            if not name.endswith(".html"):
                continue

            path = os.path.join(folder, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(4096)
                if "PlotlyConfig" in head or "plotly.js" in head or "js-plotly-plot" in head:
                    os.remove(path)
            except OSError:
                # Best-effort cleanup only.
                pass


def _load_or_create_secret_key() -> str:
    env_key = os.getenv("FLASK_SECRET_KEY")
    if env_key:
        return env_key

    # Persist a key locally so sessions survive restarts in dev.
    key_path = os.path.join("instance", "secret_key")
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    import secrets

    key = secrets.token_urlsafe(48)
    os.makedirs("instance", exist_ok=True)
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def create_app() -> Flask:
    _ensure_folders()
    _purge_plotly_static_html()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    templates_dir = os.path.join(repo_root, "templates")
    static_dir = os.path.join(repo_root, "static")

    app = Flask(
        __name__,
        template_folder=templates_dir,
        static_folder=static_dir,
        static_url_path=f"{URL_PREFIX}/static",
    )
    # Allow multiple Flask apps to coexist on the same domain/IP under different
    # URL prefixes (e.g. /polytalk and /mytrainer) without session cookie clashes.
    app.config["SESSION_COOKIE_NAME"] = "garmin_tracker_session"
    app.config["SESSION_COOKIE_PATH"] = URL_PREFIX or "/"
    # Improve perceived performance when navigating between pages.
    # In debug mode, Flask tends to be conservative with caching.
    app.config.setdefault("SEND_FILE_MAX_AGE_DEFAULT", 3600)
    app.secret_key = _load_or_create_secret_key()

    creds_store = InMemoryCredentialsStore()
    repo = JsonRepository()
    tasks = TaskManager()

    def current_creds() -> GarminCredentials | None:
        token = session.get(_SESSION_KEY)
        return creds_store.get(token)

    def require_creds() -> GarminCredentials:
        creds = current_creds()
        if not creds:
            # Should be prevented by @require_login.
            raise RuntimeError("Not logged in")
        return creds

    def _is_safe_user_id(s: str) -> bool:
        # User IDs are used in filenames; keep it strict.
        return bool(re.fullmatch(r"[a-z0-9_-]{1,80}", str(s or "")))

    def _is_admin(creds: GarminCredentials | None) -> bool:
        if not creds:
            return False
        try:
            with db_session() as session:
                user = (
                    session.query(User)
                    .filter(User.user_id == str(creds.user_id))
                    .one_or_none()
                )
                if user and int(user.id) in _ADMIN_DB_IDS:
                    return True
        except Exception:
            # Best-effort: if DB is unavailable, fall back to static allowlist.
            pass
        return str(creds.user_id) in _ADMIN_USER_IDS

    def require_admin(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            creds = current_creds()
            if not creds:
                flash("Veuillez vous connecter à Garmin.", "error")
                return redirect(url_for("home"))
            if not _is_admin(creds):
                flash("Accès administrateur requis.", "error")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)

        return wrapper

    def _delete_user_data_files(user_id: str) -> dict[str, int]:
        """Delete JSON/static artifacts for a user. Best-effort, returns counts."""

        deleted_files = 0
        deleted_dirs = 0

        if not _is_safe_user_id(user_id):
            return {"files": 0, "dirs": 0}

        data_paths = [
            os.path.join("data", f"{user_id}_activities.json"),
            os.path.join("data", f"{user_id}_activity_details.json"),
            os.path.join("data", f"{user_id}_garmin_methods.json"),
            os.path.join("data", f"{user_id}_health.json"),
            os.path.join("data", f"{user_id}_health_daily.json"),
            os.path.join("data", f"{user_id}_profile.json"),
        ]

        for p in data_paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    deleted_files += 1
            except OSError:
                pass

        # Per-user dashboard charts
        dash_dir = os.path.join("static", "dashboard", user_id)
        try:
            if os.path.isdir(dash_dir):
                shutil.rmtree(dash_dir)
                deleted_dirs += 1
        except OSError:
            pass

        return {"files": deleted_files, "dirs": deleted_dirs}

    def _parse_int(v: Any) -> int | None:
        try:
            s = str(v).strip()
            if not s:
                return None
            return int(round(float(s)))
        except Exception:
            return None

    def _parse_float(v: Any) -> float | None:
        try:
            s = str(v).strip()
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    def _normalize_zones(*, fcmax: int | None, z1: int | None, z2: int | None, z3: int | None, z4: int | None) -> dict[str, int] | None:
        if not fcmax or fcmax <= 0:
            return None
        thresholds = [z1, z2, z3, z4]
        if any(t is None for t in thresholds):
            return None
        z1v, z2v, z3v, z4v = [int(t) for t in thresholds if t is not None]
        if not (0 < z1v < z2v < z3v < z4v < fcmax):
            return None
        return {"z1_max": z1v, "z2_max": z2v, "z3_max": z3v, "z4_max": z4v, "z5_max": int(fcmax)}

    def _get_profile(user_id: str) -> dict[str, Any]:
        p = repo.profile(user_id)
        return p if isinstance(p, dict) else {}

    def _build_zone_scale(zones: dict[str, int] | None) -> list[dict[str, Any]]:
        if not zones:
            return []
        bounds = [0, zones["z1_max"], zones["z2_max"], zones["z3_max"], zones["z4_max"], zones["z5_max"]]
        out: list[dict[str, Any]] = []
        colors = ["var(--zone1)", "var(--zone2)", "var(--zone3)", "var(--zone4)", "var(--zone5)"]
        for i in range(5):
            lo = bounds[i]
            hi = bounds[i + 1]
            span = max(1, hi - lo)
            out.append(
                {
                    "label": f"Z{i+1}: {lo}–{hi} bpm",
                    "short": f"Z{i+1}",
                    "flex": span,
                    "color": colors[i],
                }
            )
        return out

    def _default_zones_from_fcmax(fcmax: int | None) -> dict[str, int] | None:
        if not fcmax or fcmax <= 0:
            return None
        # Simple 5-zone default (roughly 60/70/80/90/100%).
        z1 = int(round(fcmax * 0.60))
        z2 = int(round(fcmax * 0.70))
        z3 = int(round(fcmax * 0.80))
        z4 = int(round(fcmax * 0.90))
        if not (0 < z1 < z2 < z3 < z4 < fcmax):
            return None
        return {"z1_max": z1, "z2_max": z2, "z3_max": z3, "z4_max": z4, "z5_max": int(fcmax)}

    def _zone_bands(zones: dict[str, int]) -> list[YBand]:
        # Keep colors consistent with CSS vars in common.css
        return [
            {"low": 0.0, "high": float(zones["z1_max"]), "color": "rgba(76, 201, 240, 0.28)", "label": "Z1"},
            {"low": float(zones["z1_max"]), "high": float(zones["z2_max"]), "color": "rgba(76, 201, 240, 0.48)", "label": "Z2"},
            {"low": float(zones["z2_max"]), "high": float(zones["z3_max"]), "color": "rgba(255, 77, 141, 0.30)", "label": "Z3"},
            {"low": float(zones["z3_max"]), "high": float(zones["z4_max"]), "color": "rgba(255, 77, 141, 0.50)", "label": "Z4"},
            {"low": float(zones["z4_max"]), "high": float(zones["z5_max"]), "color": "rgba(255, 77, 141, 0.70)", "label": "Z5"},
        ]

    def _parse_activity_dt(s: Any) -> dt.datetime | None:
        if not s:
            return None
        txt = str(s)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return dt.datetime.strptime(txt[:19], fmt)
            except Exception:
                continue
        return None

    def _load_planned_trainings() -> list[dict[str, Any]]:
        raw = read_json(os.path.join("data", "trainings.json"), [])
        items = raw if isinstance(raw, list) else []
        out: list[dict[str, Any]] = []
        changed = False
        for t in items:
            if not isinstance(t, dict):
                changed = True
                continue

            # Legacy cleanup: previous versions overwrote trainings.json with Garmin activities.
            # Those entries had {name,date,distance,description} but no id/sport/title/content.
            # They cause duplicates because activities are already shown separately.
            if (
                "id" not in t
                and "sport" not in t
                and "title" not in t
                and "content" not in t
                and "description" in t
                and "name" in t
                and "date" in t
                and "distance" in t
            ):
                changed = True
                continue
            tid = t.get("id") or t.get("training_id")
            if not tid:
                tid = uuid.uuid4().hex
                changed = True
            title = t.get("title") or t.get("name") or "Entraînement"
            date = t.get("date")
            sport = t.get("sport") or t.get("sport_key") or "other"

            user_id = str(t.get("user_id") or "").strip().lower()

            done = bool(t.get("done"))
            feeling = str(t.get("feeling") or "")
            post_notes = str(t.get("post_notes") or "")

            distance_km = t.get("distance_km")
            if distance_km is None and t.get("distance") is not None:
                distance_km = t.get("distance")
                changed = True

            if "name" in t or "training_id" in t or "sport_key" in t or "distance" in t:
                changed = True

            out.append(
                {
                    "id": str(tid),
                    "title": str(title),
                    "date": str(date) if date else "",
                    "sport": str(sport),
                    "user_id": user_id,
                    "distance_km": distance_km,
                    "description": t.get("description") or "",
                    "content": t.get("content") or "",
                    "notes": t.get("notes") or "",
                    "done": done,
                    "feeling": feeling,
                    "post_notes": post_notes,
                }
            )

        if changed:
            write_json(os.path.join("data", "trainings.json"), out)
        return out

    def _save_planned_trainings(items: list[dict[str, Any]]) -> None:
        write_json(os.path.join("data", "trainings.json"), items)

    def _load_competitions() -> list[dict[str, Any]]:
        raw = read_json(os.path.join("data", "competitions.json"), [])
        items = raw if isinstance(raw, list) else []
        out: list[dict[str, Any]] = []
        changed = False
        for c in items:
            if not isinstance(c, dict):
                changed = True
                continue
            cid = c.get("id")
            if not cid:
                stable_src = f"{c.get('name') or ''}|{c.get('date') or ''}|{c.get('location') or ''}"
                cid = uuid.uuid5(uuid.NAMESPACE_URL, stable_src).hex
                changed = True

            user_id = str(c.get("user_id") or "").strip().lower()
            out.append(
                {
                    "id": str(cid),
                    "name": str(c.get("name") or "Compétition"),
                    "date": str(c.get("date") or ""),
                    "location": str(c.get("location") or ""),
                    "sport": str(c.get("sport") or "other"),
                    "distance": c.get("distance"),
                    "user_id": user_id,
                }
            )

        if changed:
            write_json(os.path.join("data", "competitions.json"), out)
        return out

    def _save_competitions(items: list[dict[str, Any]]) -> None:
        write_json(os.path.join("data", "competitions.json"), items)

    def _add_months(d: dt.date, months: int) -> dt.date:
        # month arithmetic without external deps
        y = d.year + (d.month - 1 + months) // 12
        m = (d.month - 1 + months) % 12 + 1
        day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
        return dt.date(y, m, day)

    def _period_bounds(period: str, anchor: dt.date) -> tuple[dt.datetime, dt.datetime, str, dt.date, dt.date]:
        period = (period or "week").strip().lower()
        if period not in {"week", "month", "year"}:
            period = "week"

        if period == "week":
            # Rolling 7 days (includes anchor day)
            start_date = anchor - dt.timedelta(days=6)
            end_date = anchor + dt.timedelta(days=1)
            label = f"7 jours: {start_date.isoformat()} → {anchor.isoformat()}"
        elif period == "month":
            # Rolling 4 full weeks, aligned on ISO weeks
            anchor_week_start = anchor - dt.timedelta(days=anchor.weekday())
            start_date = anchor_week_start - dt.timedelta(days=21)
            end_date = anchor_week_start + dt.timedelta(days=7)
            label = f"4 semaines: {start_date.isoformat()} → {(end_date - dt.timedelta(days=1)).isoformat()}"
        else:
            # Rolling 12 months (includes current month)
            end_month_start = anchor.replace(day=1)
            start_date = _add_months(end_month_start, -11)
            end_date = _add_months(end_month_start, 1)
            label = f"12 mois: {start_date.year}-{start_date.month:02d} → {end_month_start.year}-{end_month_start.month:02d}"

        start_dt = dt.datetime.combine(start_date, dt.time.min)
        end_dt = dt.datetime.combine(end_date, dt.time.min)
        return start_dt, end_dt, label, start_date, end_date

    def _zone_for_hr(hr: float | None, zones: dict[str, int]) -> int | None:
        if hr is None:
            return None
        try:
            v = float(hr)
        except Exception:
            return None
        if v <= zones["z1_max"]:
            return 1
        if v <= zones["z2_max"]:
            return 2
        if v <= zones["z3_max"]:
            return 3
        if v <= zones["z4_max"]:
            return 4
        return 5

    def _compute_zone_load_from_metrics(details_dict: dict[str, Any], zones: dict[str, int]) -> tuple[list[float], list[float]]:
        """Return (seconds_by_zone[1..5], meters_by_zone[1..5]) from detail metrics."""

        md = details_dict.get("metricDescriptors")
        rows_raw = details_dict.get("activityDetailMetrics")
        if not isinstance(md, list) or not isinstance(rows_raw, list):
            return [0.0] * 6, [0.0] * 6

        idx_map: dict[str, int] = {}
        for d in md:
            if not isinstance(d, dict):
                continue
            k = d.get("key")
            mi = d.get("metricsIndex")
            if k and isinstance(mi, int):
                idx_map[str(k)] = mi

        hr_idx = idx_map.get("directHeartRate") or idx_map.get("heartRate")
        t_idx = idx_map.get("sumElapsedDuration")
        ts_idx = idx_map.get("directTimestamp")
        dist_idx = idx_map.get("sumDistance") or idx_map.get("directDistance")
        if hr_idx is None or (t_idx is None and ts_idx is None):
            return [0.0] * 6, [0.0] * 6

        def _get_metric(r: Any, idx: int | None) -> float | None:
            if idx is None:
                return None
            if not isinstance(r, dict):
                return None
            m = r.get("metrics")
            if not isinstance(m, list) or idx >= len(m):
                return None
            try:
                v = m[idx]
                return float(v) if v is not None else None
            except Exception:
                return None

        rows = [r for r in rows_raw if isinstance(r, dict)]
        if len(rows) < 2:
            return [0.0] * 6, [0.0] * 6

        sec_by_zone = [0.0] * 6
        m_by_zone = [0.0] * 6

        prev_t = _get_metric(rows[0], t_idx)
        prev_ts = _get_metric(rows[0], ts_idx)
        prev_dist = _get_metric(rows[0], dist_idx)

        for r in rows[1:]:
            hr = _get_metric(r, hr_idx)

            cur_t = _get_metric(r, t_idx)
            cur_ts = _get_metric(r, ts_idx)
            cur_dist = _get_metric(r, dist_idx)

            dt_s: float | None = None
            if cur_t is not None and prev_t is not None:
                dt_s = cur_t - prev_t
            elif cur_ts is not None and prev_ts is not None:
                # directTimestamp appears to be ms epoch
                dt_s = (cur_ts - prev_ts) / 1000.0

            dd_m: float | None = None
            if cur_dist is not None and prev_dist is not None:
                dd_m = cur_dist - prev_dist

            # sanity
            if dt_s is None or not (0.0 < dt_s <= 30.0):
                prev_t, prev_ts, prev_dist = cur_t, cur_ts, cur_dist
                continue
            if dd_m is not None and not (0.0 <= dd_m <= 200.0):
                dd_m = None

            z = _zone_for_hr(hr, zones)
            if z is not None:
                sec_by_zone[z] += float(dt_s)
                if dd_m is not None:
                    m_by_zone[z] += float(dd_m)

            prev_t, prev_ts, prev_dist = cur_t, cur_ts, cur_dist

        return sec_by_zone, m_by_zone

    @app.context_processor
    def inject_user_context():
        creds = current_creds()
        return {
            "is_logged_in": bool(creds),
            "user_id": (creds.user_id if creds else None),
            "is_admin": _is_admin(creds),
        }

    def require_login(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not current_creds():
                flash("Veuillez vous connecter à Garmin.", "error")
                return redirect(url_for("home"))
            return view_func(*args, **kwargs)

        return wrapper

    def build_managers(creds: GarminCredentials):
        activity_manager = GarminActivityManager(creds.user_id, activities=repo.activities(creds.user_id))
        health_manager = GarminHealthManager(creds.user_id, health_data=repo.health_stats(creds.user_id))
        training_analysis = TrainingAnalysis(activity_manager, health_manager)
        return activity_manager, health_manager, training_analysis

    _garmin_lock = threading.Lock()
    _garmin_cache: dict[str, tuple[GarminClientHandler, float]] = {}

    def build_garmin_handler(creds: GarminCredentials) -> GarminClientHandler:
        now = time.time()
        with _garmin_lock:
            hit = _garmin_cache.get(creds.user_id)
            if hit:
                handler, expires_at = hit
                if now < expires_at and getattr(handler, "client", None) is not None:
                    return handler

        handler = GarminClientHandler(creds.email, creds.password, creds.user_id)
        handler.login()
        with _garmin_lock:
            _garmin_cache[creds.user_id] = (handler, now + 20 * 60)
        return handler

    @app.get("/")
    def root_redirect():
        return redirect(f"{URL_PREFIX}/")

    @app.get(f"{URL_PREFIX}/")
    def home():
        return render_template("home.html")

    @app.post(f"{URL_PREFIX}/login")
    def login():
        email = (request.form.get("email") or "").strip().lower()
        pin_raw = request.form.get("pin") or ""
        pin = _normalize_pin(pin_raw)

        if not email or not pin:
            flash("Email et PIN requis (4 à 12 chiffres).", "error")
            return redirect(url_for("home"))

        u = _db_verify_user(email=email, pin=pin)
        if not u:
            flash("Identifiants invalides.", "error")
            return redirect(url_for("home"))

        # We do not store Garmin password in session.
        token = creds_store.create_session(GarminCredentials(user_id=u.user_id, email=u.email, password=""))
        session[_SESSION_KEY] = token
        flash(
            f"Connecté en tant que {u.user_id}. La synchro Garmin se fera lors de la mise à jour (Activités / Santé).",
            "success",
        )
        return redirect(url_for("dashboard"))

    @app.post(f"{URL_PREFIX}/register")
    def register():
        email = (request.form.get("email") or "").strip().lower()
        pseudo_raw = request.form.get("pseudo") or ""
        pseudo = _normalize_pseudo(pseudo_raw)
        pin_raw = request.form.get("pin") or ""
        pin = _normalize_pin(pin_raw)

        if not pseudo:
            flash("Pseudo requis (2 à 80 caractères).", "error")
            return redirect(url_for("home"))
        if not email or not pin:
            flash("Email et PIN requis (4 à 12 chiffres).", "error")
            return redirect(url_for("home"))

        existing = _db_get_user_by_email(email)
        if existing:
            flash("Compte déjà existant. Connecte-toi.", "error")
            return redirect(url_for("home"))

        desired_user_id = _normalize_user_id_from_pseudo(pseudo)
        if _db_get_user_by_user_id(desired_user_id):
            flash("Pseudo déjà utilisé. Choisis-en un autre.", "error")
            return redirect(url_for("home"))

        try:
            u = _db_create_user(email=email, pin=pin, pseudo=pseudo)
        except Exception:
            app.logger.exception("Failed to create user")
            flash("Création du compte impossible. Vérifie la base de données.", "error")
            return redirect(url_for("home"))

        token = creds_store.create_session(GarminCredentials(user_id=u.user_id, email=u.email, password=""))
        session[_SESSION_KEY] = token
        flash(
            f"Inscription OK. Connecté en tant que {u.user_id}. La synchro Garmin se fera lors de la mise à jour.",
            "success",
        )
        return redirect(url_for("dashboard"))

    @app.get(f"{URL_PREFIX}/logout")
    def logout():
        token = session.pop(_SESSION_KEY, None)
        creds_store.delete(token)
        flash("Déconnecté.", "success")
        return redirect(url_for("home"))

    @app.get(f"{URL_PREFIX}/dashboard")
    @require_login
    def dashboard():
        creds = require_creds()

        # Community view: allow browsing another user's dashboard when logged in.
        requested_user = (request.args.get("user") or "").strip().lower()
        if requested_user and _is_safe_user_id(requested_user) and _db_get_user_by_user_id(requested_user):
            viewing_user_id = requested_user
        else:
            viewing_user_id = creds.user_id

        period = (request.args.get("period") or "week").strip().lower()
        anchor_raw = (request.args.get("anchor") or "").strip()
        try:
            anchor_date = dt.date.fromisoformat(anchor_raw) if anchor_raw else dt.date.today()
        except Exception:
            anchor_date = dt.date.today()

        start_dt, end_dt, period_label, start_date, end_date = _period_bounds(period, anchor_date)

        profile_obj = _get_profile(viewing_user_id)
        profile_zones = profile_obj.get("zones") if isinstance(profile_obj.get("zones"), dict) else None
        fcmax = profile_obj.get("fcmax")
        try:
            fcmax_i = int(fcmax) if fcmax is not None else None
        except Exception:
            fcmax_i = None
        zones_inferred = False
        inferred_fcmax = None

        # If the user hasn't configured FC max / zones, infer FC max from observed activities
        # so the dashboard can still show a useful zone breakdown.
        if not profile_zones and not fcmax_i:
            max_hr_seen = None
            for a in repo.activities(viewing_user_id):
                if not isinstance(a, dict):
                    continue
                v = a.get("maxHR")
                try:
                    hr = float(v) if v is not None else None
                except Exception:
                    hr = None
                if hr is None or hr <= 0:
                    continue
                if max_hr_seen is None or hr > max_hr_seen:
                    max_hr_seen = hr
            if max_hr_seen is not None:
                inferred_fcmax = int(round(max_hr_seen))
                if inferred_fcmax > 0:
                    fcmax_i = inferred_fcmax
                    zones_inferred = True

        zones = profile_zones or _default_zones_from_fcmax(fcmax_i)

        # Optional sport filter for charts.
        requested_sport = (request.args.get("sport") or "all").strip().lower()

        acts = repo.activities(viewing_user_id)
        details_all = repo.activity_details(viewing_user_id)
        details_map = details_all.get("activities") if isinstance(details_all, dict) else {}
        if not isinstance(details_map, dict):
            details_map = {}

        acts_in_range: list[dict[str, Any]] = []
        for a in acts:
            if not isinstance(a, dict):
                continue
            dt0 = _parse_activity_dt(a.get("startTimeLocal") or a.get("startTimeGMT"))
            if not dt0:
                continue
            if not (start_dt <= dt0 < end_dt):
                continue
            acts_in_range.append(a)

        acts_in_range.sort(key=lambda x: str(x.get("startTimeLocal") or ""), reverse=True)

        total_dist_m = 0.0
        total_dur_s = 0.0
        total_load = 0.0

        # Per-sport aggregates
        sport_agg: dict[str, dict[str, float]] = {}

        # Dashboard charts: aggregate by bucket depending on period
        bucket_points_x: list[str] = []
        load_points_y: list[float | None] = []
        dur_points_y: list[float | None] = []
        dist_points_y: list[float | None] = []

        load_bucket: dict[str, float] = {}
        dur_bucket: dict[str, float] = {}
        dist_bucket: dict[str, float] = {}

        if period == "week":
            bucket_dates = [start_date + dt.timedelta(days=i) for i in range(7)]
            bucket_keys = [d.isoformat() for d in bucket_dates]
        elif period == "month":
            # 4 ISO weeks, use monday date as key
            bucket_weeks = [start_date + dt.timedelta(days=7 * i) for i in range(4)]
            bucket_keys = [d.isoformat() for d in bucket_weeks]
        else:
            # 12 months, use month start date as key (ISO) for time axis
            bucket_months = [_add_months(start_date.replace(day=1), i) for i in range(12)]
            bucket_keys = [d.isoformat() for d in bucket_months]
        sec_by_zone = [0.0] * 6
        m_by_zone = [0.0] * 6

        for a in acts_in_range:
            aid = a.get("activityId")
            try:
                dist_m = float(a.get("distance") or 0.0)
            except Exception:
                dist_m = 0.0
            try:
                dur_s = float(a.get("duration") or 0.0)
            except Exception:
                dur_s = 0.0

            try:
                load = float(a.get("activityTrainingLoad") or 0.0)
            except Exception:
                load = 0.0

            total_dist_m += dist_m
            total_dur_s += dur_s

            if load > 0:
                total_load += load

            raw_key = (a.get("activityType") or {}).get("typeKey") or ""
            sport = _canonical_sport_type(raw_key) or "other"
            srow = sport_agg.get(sport)
            if srow is None:
                srow = {"count": 0.0, "dur_s": 0.0, "dist_m": 0.0, "load": 0.0}
                sport_agg[sport] = srow
            srow["count"] += 1.0
            srow["dur_s"] += dur_s
            srow["dist_m"] += dist_m
            srow["load"] += load

            # Charts can be filtered by sport. Keep summary totals across all sports.
            include_in_charts = requested_sport in ("", "all") or requested_sport == sport

            dt0 = _parse_activity_dt(a.get("startTimeLocal") or a.get("startTimeGMT"))
            if dt0 and include_in_charts:
                if period == "week":
                    k = dt0.date().isoformat()
                elif period == "month":
                    wk = dt0.date() - dt.timedelta(days=dt0.date().weekday())
                    k = wk.isoformat()
                else:
                    k = dt0.date().replace(day=1).isoformat()
                load_bucket[k] = load_bucket.get(k, 0.0) + load
                dur_bucket[k] = dur_bucket.get(k, 0.0) + dur_s
                dist_bucket[k] = dist_bucket.get(k, 0.0) + dist_m

            av = a.get("averageHR")
            try:
                av_hr = float(av) if av is not None else None
            except Exception:
                av_hr = None

            if not zones:
                continue
            entry = details_map.get(str(aid)) if aid is not None else None
            details_dict = entry.get("details") if isinstance(entry, dict) else None

            # Zones breakdown should include all activities in the interval.
            # Prefer detailed metrics; fall back to session avg HR when missing.
            if isinstance(details_dict, dict):
                s_z, m_z = _compute_zone_load_from_metrics(details_dict, zones)
            else:
                s_z, m_z = [0.0] * 6, [0.0] * 6

            if sum(s_z) > 0.0:
                for z in range(1, 6):
                    sec_by_zone[z] += s_z[z]
                    m_by_zone[z] += m_z[z]

                # If distance could not be computed from metrics, distribute summary distance by time.
                if sum(m_z) <= 0.0 and dist_m > 0 and sum(s_z) > 0.0:
                    s_total = sum(s_z)
                    for z in range(1, 6):
                        m_by_zone[z] += dist_m * (s_z[z] / s_total)
            else:
                fallback_zone = _zone_for_hr(av_hr, zones) if av_hr is not None else 1
                if fallback_zone is None:
                    fallback_zone = 1
                sec_by_zone[fallback_zone] += dur_s
                if dist_m > 0:
                    m_by_zone[fallback_zone] += dist_m
        # Build chart series in bucket order, including empty buckets
        bucket_points_x = [k for k in bucket_keys]
        for k in bucket_keys:
            load_points_y.append(load_bucket.get(k, 0.0) if load_bucket.get(k, 0.0) > 0 else None)
            # duration in hours
            dur_s = dur_bucket.get(k, 0.0)
            dur_points_y.append((dur_s / 3600.0) if dur_s > 0 else None)
            # distance in km
            dist_m = dist_bucket.get(k, 0.0)
            dist_points_y.append((dist_m / 1000.0) if dist_m > 0 else None)

        def _fmt_hms(seconds: float) -> str:
            s = int(round(seconds))
            if s <= 0:
                return "0m"
            h = s // 3600
            m = (s % 3600) // 60
            sec = s % 60
            if h:
                return f"{h}h {m:02d}m"
            if m:
                return f"{m}m {sec:02d}s"
            return f"{sec}s"

        zones_rows: list[dict[str, Any]] = []
        z_total_s = sum(sec_by_zone)
        z_total_m = sum(m_by_zone)
        for z in range(1, 6):
            zs = sec_by_zone[z]
            zm = m_by_zone[z]
            zones_rows.append(
                {
                    "zone": f"Z{z}",
                    "color": f"var(--zone{z})",
                    "time": _fmt_hms(zs),
                    "time_s": zs,
                    "km": round(zm / 1000.0, 2) if zm > 0 else 0.0,
                    "pct_time": round((zs / z_total_s * 100.0), 1) if z_total_s > 0 else 0.0,
                    "pct_km": round((zm / z_total_m * 100.0), 1) if z_total_m > 0 else 0.0,
                }
            )

        # Build zone distribution bars (time %) for UI.
        zone_dist: list[dict[str, Any]] = []
        for z in range(1, 6):
            pct = next((r["pct_time"] for r in zones_rows if r.get("zone") == f"Z{z}"), 0.0)
            zone_dist.append(
                {
                    "flex": max(0.0, float(pct)),
                    "color": f"var(--zone{z})",
                    "label": f"Z{z} — {pct}%",
                    "short": f"Z{z}",
                    "pct": pct,
                }
            )

        # Default target distribution (time %) — simple polarized-ish baseline.
        # Can be refined later or made user-configurable.
        target_pct = {1: 70.0, 2: 20.0, 3: 7.0, 4: 2.0, 5: 1.0}
        zone_target: list[dict[str, Any]] = []
        for z in range(1, 6):
            pct = float(target_pct.get(z, 0.0))
            zone_target.append(
                {
                    "flex": max(0.0, pct),
                    "color": f"var(--zone{z})",
                    "label": f"Z{z} — {pct}%",
                    "short": f"Z{z}",
                    "pct": pct,
                }
            )

        # Compute narrative report
        z3_5_s = sum(sec_by_zone[3:6])
        intensity_txt = f"{_fmt_hms(z3_5_s)} en Z3–Z5" if zones else "Zones non configurées"

        # Write dashboard charts
        dashboard_graphs: list[dict[str, str]] = []
        try:
            src_path = os.path.join("data", f"{viewing_user_id}_activities.json")
            mtime_src = os.path.getmtime(src_path) if os.path.exists(src_path) else -1
            echarts_path = os.path.join(os.path.dirname(__file__), "echarts.py")
            mtime_echarts = os.path.getmtime(echarts_path) if os.path.exists(echarts_path) else -1
            webapp_path = __file__
            mtime_webapp = os.path.getmtime(webapp_path) if os.path.exists(webapp_path) else -1
            # Include sport in cache key so switching filter regenerates distinct files.
            cache_key = f"{anchor_date.isoformat()}__{requested_sport or 'all'}"
            src_max = max(mtime_src, mtime_echarts, mtime_webapp)

            def _write_chart(rel_name: str, *, title: str, x: list[str], y: list[float | None], y_label: str, color: str):
                out_rel = f"dashboard/{viewing_user_id}/{rel_name}__{period}__{cache_key}.html"
                out_path = os.path.join("static", *out_rel.split("/"))
                if os.path.exists(out_path) and os.path.getmtime(out_path) >= src_max:
                    url = url_for("static", filename=out_rel)
                else:
                    write_timeseries_chart_html(
                        out_path,
                        title=title,
                        x=x,
                        y=y,
                        y_label=y_label,
                        color=color,
                        primary_series="bar",
                    )
                    url = url_for("static", filename=out_rel)
                dashboard_graphs.append({"title": title, "url": url})

            sport_label = requested_sport if requested_sport and requested_sport != "all" else "tous sports"

            if bucket_points_x and any(v is not None for v in load_points_y):
                _write_chart(
                    "all__training_load",
                    title=f"Charge d'entraînement ({sport_label})",
                    x=bucket_points_x,
                    y=load_points_y,
                    y_label="Load",
                    color="#FF4D8D",
                )

            if bucket_points_x and any(v is not None for v in dur_points_y):
                _write_chart(
                    "all__duration_hours",
                    title=f"Volume (heures) ({sport_label})",
                    x=bucket_points_x,
                    y=dur_points_y,
                    y_label="h",
                    color="#4CC9F0",
                )

            if bucket_points_x and any(v is not None for v in dist_points_y):
                _write_chart(
                    "all__distance_km",
                    title=f"Distance (km) ({sport_label})",
                    x=bucket_points_x,
                    y=dist_points_y,
                    y_label="km",
                    color="#4CC9F0",
                )
        except Exception:
            app.logger.exception("Failed to write dashboard charts")

        # navigation anchors (shift by the visible window)
        if period == "week":
            prev_anchor = (anchor_date - dt.timedelta(days=7)).isoformat()
            next_anchor = (anchor_date + dt.timedelta(days=7)).isoformat()
        elif period == "month":
            prev_anchor = (anchor_date - dt.timedelta(days=28)).isoformat()
            next_anchor = (anchor_date + dt.timedelta(days=28)).isoformat()
        else:
            prev_anchor = _add_months(anchor_date, -12).isoformat()
            next_anchor = _add_months(anchor_date, 12).isoformat()

        # Prepare per-sport rows (sorted by duration)
        sport_rows: list[dict[str, Any]] = []
        for sport, agg in sport_agg.items():
            sport_rows.append(
                {
                    "sport": sport,
                    "count": int(round(agg.get("count", 0.0))),
                    "time": _fmt_hms(agg.get("dur_s", 0.0)),
                    "time_s": float(agg.get("dur_s", 0.0)),
                    "km": round(float(agg.get("dist_m", 0.0)) / 1000.0, 2) if float(agg.get("dist_m", 0.0)) > 0 else 0.0,
                    "load": round(float(agg.get("load", 0.0)), 1) if float(agg.get("load", 0.0)) > 0 else 0.0,
                }
            )
        sport_rows.sort(key=lambda r: float(r.get("time_s", 0.0)), reverse=True)

        available_sports = sorted({r.get("sport") for r in sport_rows if r.get("sport")})
        if requested_sport not in ("", "all") and requested_sport not in available_sports:
            requested_sport = "all"

        # Improve narrative report: concise highlights, sorted by sport contribution.
        report_lines: list[str] = []
        report_lines.append(f"Volume: {_fmt_hms(total_dur_s)}")
        report_lines.append(f"Distance: {round(total_dist_m / 1000.0, 2) if total_dist_m else 0.0} km")
        report_lines.append(f"Charge: {round(total_load, 1) if total_load > 0 else 0.0}")
        if sport_rows:
            top_time = [r for r in sport_rows if float(r.get("time_s", 0.0)) > 0][:3]
            if top_time:
                report_lines.append(
                    "Top volume: " + ", ".join(f"{r['sport']} ({r['time']})" for r in top_time)
                )
            top_load = sorted(sport_rows, key=lambda r: float(r.get("load", 0.0)), reverse=True)
            top_load = [r for r in top_load if float(r.get("load", 0.0)) > 0][:3]
            if top_load:
                report_lines.append(
                    "Top charge: " + ", ".join(f"{r['sport']} ({r['load']})" for r in top_load)
                )

        def _parse_date_only(v: Any) -> dt.date | None:
            try:
                s = str(v or "").split(" ")[0]
                if not s:
                    return None
                return dt.date.fromisoformat(s)
            except Exception:
                return None

        def _belongs_to_viewing_user(item: dict[str, Any]) -> bool:
            uid = str(item.get("user_id") or "").strip().lower()
            return (not uid) or uid == viewing_user_id

        # Upcoming planned trainings / competitions (next 3 each)
        today = dt.date.today()

        sport_labels = {
            "running": "Course à pied",
            "cycling": "Vélo",
            "swimming": "Natation",
            "strength_training": "Musculation",
            "other": "Autre",
        }

        next_trainings: list[dict[str, Any]] = []
        try:
            planned_trainings = [t for t in _load_planned_trainings() if isinstance(t, dict) and _belongs_to_viewing_user(t)]
            planned_trainings = [t for t in planned_trainings if (_parse_date_only(t.get("date")) and _parse_date_only(t.get("date")) >= today)]
            planned_trainings.sort(key=lambda t: str(t.get("date") or ""))
            for t in planned_trainings[:3]:
                next_trainings.append(
                    {
                        "id": t.get("id"),
                        "date": t.get("date"),
                        "title": t.get("title") or "Entraînement",
                        "description": t.get("description"),
                        "sport": t.get("sport") or "other",
                        "sport_label": sport_labels.get(str(t.get("sport") or "other"), "Autre"),
                        "distance_km": t.get("distance_km"),
                    }
                )
        except Exception:
            app.logger.exception("Failed to compute next trainings")

        next_competitions: list[dict[str, Any]] = []
        try:
            planned_competitions = [c for c in _load_competitions() if isinstance(c, dict) and _belongs_to_viewing_user(c)]
            planned_competitions = [c for c in planned_competitions if (_parse_date_only(c.get("date")) and _parse_date_only(c.get("date")) >= today)]
            planned_competitions.sort(key=lambda c: str(c.get("date") or ""))
            for c in planned_competitions[:3]:
                next_competitions.append(
                    {
                        "id": c.get("id"),
                        "date": c.get("date"),
                        "name": c.get("name") or "Compétition",
                        "sport": c.get("sport") or "other",
                        "sport_label": sport_labels.get(str(c.get("sport") or "other"), "Autre"),
                        "distance": c.get("distance"),
                        "location": c.get("location"),
                    }
                )
        except Exception:
            app.logger.exception("Failed to compute next competitions")

        return render_template(
            "index.html",
            period=period,
            anchor=anchor_date.isoformat(),
            sport=requested_sport,
            available_sports=available_sports,
            viewing_user_id=viewing_user_id,
            period_label=period_label,
            prev_anchor=prev_anchor,
            next_anchor=next_anchor,
            activities_count=len(acts_in_range),
            total_km=round(total_dist_m / 1000.0, 2) if total_dist_m else 0.0,
            total_time=_fmt_hms(total_dur_s),
            total_load=round(total_load, 1) if total_load > 0 else 0.0,
            intensity_summary=intensity_txt,
            report_lines=report_lines,
            next_trainings=next_trainings,
            next_competitions=next_competitions,
            zones_inferred=zones_inferred,
            fcmax_used=(fcmax_i if fcmax_i and fcmax_i > 0 else None),
            zones=zones,
            zone_dist=zone_dist,
            zone_target=zone_target,
            zones_rows=zones_rows,
            sport_rows=sport_rows,
            dashboard_graphs=dashboard_graphs,
        )

    @app.get(f"{URL_PREFIX}/community")
    @require_login
    def community():
        creds = require_creds()
        users = _db_list_users()
        rows = [
            {
                "user_id": u.user_id,
                "display_name": u.display_name,
            }
            for u in users
        ]
        return render_template("community.html", users=rows, me=creds.user_id)

    @app.get(f"{URL_PREFIX}/admin")
    @require_admin
    def admin():
        users = _db_list_users()
        rows = [
            {
                "user_id": u.user_id,
                "display_name": u.display_name,
                "email": u.email,
                "created_at": (u.created_at.isoformat() if getattr(u, "created_at", None) else ""),
            }
            for u in users
        ]
        return render_template("admin.html", users=rows)

    @app.post(f"{URL_PREFIX}/admin/reset_pin")
    @require_admin
    def admin_reset_pin():
        target = (request.form.get("user_id") or "").strip().lower()
        if not target or not _is_safe_user_id(target) or not _db_get_user_by_user_id(target):
            flash("Utilisateur invalide.", "error")
            return redirect(url_for("admin"))

        pin_raw = (request.form.get("pin") or "").strip()
        pin = _normalize_pin(pin_raw) if pin_raw else None
        if not pin:
            # Generate a new 6-digit PIN.
            pin = f"{secrets.randbelow(1_000_000):06d}"

        if not _db_set_user_pin(user_id=target, pin=pin):
            flash("Échec de la réinitialisation du PIN.", "error")
            return redirect(url_for("admin"))

        flash(f"PIN réinitialisé pour {target}: {pin}", "success")
        return redirect(url_for("admin"))

    @app.post(f"{URL_PREFIX}/admin/delete_data")
    @require_admin
    def admin_delete_data():
        target = (request.form.get("user_id") or "").strip().lower()
        if not target or not _is_safe_user_id(target) or not _db_get_user_by_user_id(target):
            flash("Utilisateur invalide.", "error")
            return redirect(url_for("admin"))

        res = _delete_user_data_files(target)
        flash(f"Données supprimées pour {target} (fichiers: {res['files']}, dossiers: {res['dirs']}).", "success")
        return redirect(url_for("admin"))

    @app.post(f"{URL_PREFIX}/admin/delete_account")
    @require_admin
    def admin_delete_account():
        creds = require_creds()
        target = (request.form.get("user_id") or "").strip().lower()
        if not target or not _is_safe_user_id(target) or not _db_get_user_by_user_id(target):
            flash("Utilisateur invalide.", "error")
            return redirect(url_for("admin"))
        if target == creds.user_id:
            flash("Impossible de supprimer ton propre compte admin.", "error")
            return redirect(url_for("admin"))

        _delete_user_data_files(target)
        if not _db_delete_user(user_id=target):
            flash("Échec de la suppression du compte.", "error")
            return redirect(url_for("admin"))

        flash(f"Compte supprimé: {target}", "success")
        return redirect(url_for("admin"))

    @app.route(f"{URL_PREFIX}/profile", methods=["GET", "POST"])
    @require_login
    def profile():
        creds = require_creds()

        def _fmt_time(seconds: float) -> str:
            s = int(round(seconds))
            if s <= 0:
                return "0:00"
            h = s // 3600
            m = (s % 3600) // 60
            sec = s % 60
            if h:
                return f"{h}:{m:02d}:{sec:02d}"
            return f"{m}:{sec:02d}"

        def _parse_date_only(v: Any) -> dt.date | None:
            try:
                s = str(v or "").split(" ")[0]
                if not s:
                    return None
                return dt.date.fromisoformat(s)
            except Exception:
                return None

        def _belongs_to_user(item: dict[str, Any]) -> bool:
            uid = str(item.get("user_id") or "").strip().lower()
            return (not uid) or uid == creds.user_id

        # Records: scan all running activities and pick best time for common race distances.
        activity_manager = GarminActivityManager(creds.user_id, activities=repo.activities(creds.user_id))
        running = [a for a in activity_manager.activities if isinstance(a, dict) and ((a.get("activityType") or {}).get("typeKey") == "running")]
        targets = [
            {"key": "1k", "label": "1 km", "target_km": 1.0, "tol_km": 0.10},
            {"key": "3k", "label": "3 km", "target_km": 3.0, "tol_km": 0.15},
            {"key": "5k", "label": "5 km", "target_km": 5.0, "tol_km": 0.25},
            {"key": "10k", "label": "10 km", "target_km": 10.0, "tol_km": 0.50},
            {"key": "semi", "label": "Semi-marathon", "target_km": 21.097, "tol_km": 1.00},
            {"key": "marathon", "label": "Marathon", "target_km": 42.195, "tol_km": 2.00},
        ]

        records: list[dict[str, Any]] = []
        for t in targets:
            best: dict[str, Any] | None = None
            for a in running:
                try:
                    dist_km = float(a.get("distance") or 0.0) / 1000.0
                except Exception:
                    dist_km = 0.0
                if dist_km <= 0:
                    continue
                if abs(dist_km - float(t["target_km"])) > float(t["tol_km"]):
                    continue

                try:
                    dur_s = float(a.get("duration") or a.get("movingDuration") or 0.0)
                except Exception:
                    dur_s = 0.0
                if dur_s <= 0:
                    continue

                if best is None or dur_s < float(best.get("duration_s") or 1e18):
                    dt_txt = a.get("startTimeLocal") or a.get("startTimeGMT") or ""
                    date_only = str(dt_txt).split(" ")[0] if dt_txt else ""
                    best = {
                        "key": t["key"],
                        "label": t["label"],
                        "time": _fmt_time(dur_s),
                        "duration_s": dur_s,
                        "date": date_only,
                        "activity_id": a.get("activityId"),
                        "activity_name": a.get("activityName") or a.get("name") or "Activité",
                        "distance_km": round(dist_km, 2),
                    }

            records.append(best or {"key": t["key"], "label": t["label"]})

        # Upcoming planned items for this user.
        today = dt.date.today()
        upcoming_trainings = [t for t in _load_planned_trainings() if isinstance(t, dict) and _belongs_to_user(t) and (_parse_date_only(t.get("date")) and _parse_date_only(t.get("date")) >= today)]
        upcoming_competitions = [c for c in _load_competitions() if isinstance(c, dict) and _belongs_to_user(c) and (_parse_date_only(c.get("date")) and _parse_date_only(c.get("date")) >= today)]
        upcoming_trainings.sort(key=lambda x: str(x.get("date") or ""))
        upcoming_competitions.sort(key=lambda x: str(x.get("date") or ""))
        upcoming_trainings = upcoming_trainings[:10]
        upcoming_competitions = upcoming_competitions[:10]

        if request.method == "POST":
            fcmax = _parse_int(request.form.get("fcmax"))
            vma = _parse_float(request.form.get("vma"))
            z1 = _parse_int(request.form.get("z1_max"))
            z2 = _parse_int(request.form.get("z2_max"))
            z3 = _parse_int(request.form.get("z3_max"))
            z4 = _parse_int(request.form.get("z4_max"))

            zones = _normalize_zones(fcmax=fcmax, z1=z1, z2=z2, z3=z3, z4=z4)
            if fcmax is not None and fcmax <= 0:
                flash("FC max invalide.", "error")
                return redirect(url_for("profile"))
            if any(v is not None for v in [z1, z2, z3, z4]) and not zones:
                flash("Zones invalides: il faut 0 < Z1 < Z2 < Z3 < Z4 < FC max.", "error")
                return redirect(url_for("profile"))

            profile_obj: dict[str, Any] = {
                "fcmax": fcmax,
                "vma": vma,
                "zones": zones,
                "updated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
            repo.save_profile(creds.user_id, profile_obj)
            flash("Profil enregistré.", "success")
            return redirect(url_for("profile"))

        profile_obj = _get_profile(creds.user_id)
        zones = profile_obj.get("zones") if isinstance(profile_obj.get("zones"), dict) else None
        return render_template(
            "profile.html",
            me=creds.user_id,
            profile={"fcmax": profile_obj.get("fcmax"), "vma": profile_obj.get("vma")},
            zones={
                "z1_max": (zones or {}).get("z1_max"),
                "z2_max": (zones or {}).get("z2_max"),
                "z3_max": (zones or {}).get("z3_max"),
                "z4_max": (zones or {}).get("z4_max"),
            },
            zone_scale=_build_zone_scale(zones),
            records=records,
            upcoming_trainings=upcoming_trainings,
            upcoming_competitions=upcoming_competitions,
        )

    @app.get(f"{URL_PREFIX}/activity")
    @require_login
    def activity():
        creds = require_creds()
        activity_manager, _, _ = build_managers(creds)

        planned_trainings = [
            t
            for t in _load_planned_trainings()
            if isinstance(t, dict)
            and ((not str(t.get("user_id") or "").strip().lower()) or str(t.get("user_id") or "").strip().lower() == creds.user_id)
        ]
        planned_by_date_sport: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for t in planned_trainings:
            date = str(t.get("date") or "")
            sport = str(t.get("sport") or "other")
            if not date:
                continue
            planned_by_date_sport.setdefault((date, sport), []).append(t)

        task_id = (request.args.get("task") or "").strip() or None
        task_status_url = None
        task_running = False
        if task_id:
            status = tasks.get(task_id)
            if status and status.user_id == creds.user_id:
                task_status_url = url_for("task_status", task_id=task_id)
                task_running = status.state == "running"
            else:
                task_id = None

        if not task_running:
            activity_manager.plot_interactive_graphs_by_type("static/activity/by_type")

        allowed_order = ["swimming", "cycling", "running", "strength_training"]
        allowed_types = set(allowed_order)
        type_labels = {
            "running": "Course à pied",
            "cycling": "Vélo",
            "swimming": "Natation",
            "strength_training": "Musculation",
        }

        formatted = []
        for item in activity_manager.activities:
            raw_key = (item.get("activityType") or {}).get("typeKey") or ""
            type_key = _canonical_sport_type(raw_key)
            if type_key not in allowed_types:
                continue
            type_label = type_labels.get(type_key, type_key.replace("_", " ").title())

            date = item.get("startTimeLocal", "Date inconnue")
            date_only = str(date).split(" ")[0] if date else ""
            name = item.get("activityName") or type_label

            distance_m = item.get("distance", 0) or 0
            duration_s = item.get("duration", 0) or 0

            distance_km = round(distance_m / 1000, 2) if distance_m else 0
            duration_minutes = round(duration_s / 60) if duration_s else 0
            hours = duration_minutes // 60
            minutes = duration_minutes % 60

            avg_pace = None
            if distance_m and duration_s and distance_m > 0:
                if type_key == "swimming":
                    pace_s_per_100m = duration_s / (distance_m / 100.0)
                    pace_minutes = int(pace_s_per_100m // 60)
                    pace_seconds = int(round(pace_s_per_100m % 60))
                    if pace_seconds == 60:
                        pace_minutes += 1
                        pace_seconds = 0
                    avg_pace = f"{pace_minutes}m {pace_seconds:02d}s/100m"
                else:
                    pace_s_per_km = duration_s / (distance_m / 1000.0)
                    pace_minutes = int(pace_s_per_km // 60)
                    pace_seconds = int(round(pace_s_per_km % 60))
                    if pace_seconds == 60:
                        pace_minutes += 1
                        pace_seconds = 0
                    avg_pace = f"{pace_minutes}m {pace_seconds:02d}s/km"

            formatted.append(
                {
                    "activity_id": item.get("activityId"),
                    "type_key": type_key,
                    "type_label": type_label,
                    "name": name,
                    "date": date,
                    "planned": (planned_by_date_sport.get((date_only, type_key)) or [None])[0],
                    "distance": distance_km,
                    "duration": f"{hours}h {minutes:02d}m",
                    "avg_pace": avg_pace,
                }
            )

        # Newest first
        formatted.sort(key=lambda a: a.get("date") or "", reverse=True)

        activities_by_key: dict[str, list[dict[str, Any]]] = {k: [] for k in allowed_order}
        for a in formatted:
            activities_by_key.setdefault(a["type_key"], []).append(a)

        by_type_dir = os.path.join("static", "activity", "by_type")
        metric_order = [
            "distance_km",
            "duration_min",
            "pace_min_100m",
            "pace_min_km",
            "avg_swolf",
            "swim_cadence_spm",
            "strokes_per_length",
            "avg_hr",
        ]
        graphs_by_key: dict[str, list[str]] = {k: [] for k in allowed_order}
        if os.path.isdir(by_type_dir):
            files = [f for f in os.listdir(by_type_dir) if f.endswith(".html")]
            for f in files:
                if "__" not in f:
                    continue
                type_key, metric_part = f.split("__", 1)
                # files are generated with canonical type keys
                if type_key not in allowed_types:
                    continue
                graphs_by_key.setdefault(type_key, []).append(f)

            for type_key, names in graphs_by_key.items():
                names.sort(
                    key=lambda name: (
                        metric_order.index(name.split("__", 1)[1].replace(".html", ""))
                        if name.split("__", 1)[1].replace(".html", "") in metric_order
                        else 99,
                        name,
                    )
                )

        sport_tabs = []
        for type_key in allowed_order:
            names = graphs_by_key.get(type_key) or []
            graph_urls = [
                url_for("static", filename=f"activity/by_type/{name}")
                for name in names
                if os.path.exists(os.path.join(by_type_dir, name))
            ]
            sport_tabs.append(
                {
                    "type_key": type_key,
                    "type_label": type_labels[type_key],
                    "graphs": graph_urls,
                    "activities": activities_by_key.get(type_key) or [],
                }
            )

        active_tab = None
        for t in sport_tabs:
            if t["activities"] or t["graphs"]:
                active_tab = t["type_key"]
                break

        return render_template(
            "activity.html",
            sport_tabs=sport_tabs,
            active_tab=active_tab,
            task_id=task_id,
            task_status_url=task_status_url,
        )

    @app.get(f"{URL_PREFIX}/activity/<int:activity_id>")
    @require_login
    def activity_detail(activity_id: int):
        creds = require_creds()

        # Try details first (if already synced)
        details_file = repo.activity_details(creds.user_id)
        activities_map = details_file.get("activities") if isinstance(details_file, dict) else {}
        details_bundle = activities_map.get(str(activity_id)) if isinstance(activities_map, dict) else None

        # Always try to find a summary (either inside details bundle or activities list)
        summary = None
        if isinstance(details_bundle, dict):
            summary = details_bundle.get("summary") if isinstance(details_bundle.get("summary"), dict) else None
            if not summary and isinstance(details_bundle.get("details"), dict):
                summary = details_bundle.get("details")

        if not isinstance(summary, dict):
            for a in repo.activities(creds.user_id):
                if isinstance(a, dict) and a.get("activityId") == activity_id:
                    summary = a
                    break

        summary_dict: dict[str, Any] = summary if isinstance(summary, dict) else {"activityId": activity_id}

        activity_type_obj = summary_dict.get("activityType")
        raw_key = ""
        if isinstance(activity_type_obj, dict):
            rk = activity_type_obj.get("typeKey")
            raw_key = str(rk) if rk else ""
        type_key = _canonical_sport_type(raw_key) or (raw_key if raw_key else "other")
        type_labels = {
            "running": "Course à pied",
            "cycling": "Vélo",
            "swimming": "Natation",
            "strength_training": "Musculation",
        }
        sport_label = type_labels.get(type_key, str(raw_key or type_key).replace("_", " ").title())

        distance_m = summary_dict.get("distance") or 0
        duration_s = summary_dict.get("duration") or 0
        try:
            distance_km_val = float(distance_m) / 1000 if distance_m else 0.0
        except Exception:
            distance_km_val = 0.0
        try:
            duration_s_val = float(duration_s) if duration_s else 0.0
        except Exception:
            duration_s_val = 0.0

        duration_min = duration_s_val / 60 if duration_s_val else 0.0
        hours = int(duration_min // 60) if duration_min else 0
        minutes = int(round(duration_min % 60)) if duration_min else 0
        duration_hm = f"{hours}h {minutes:02d}m" if duration_s_val else "—"

        pace = "—"
        if distance_km_val and duration_min:
            if type_key == "swimming":
                dist_100m = (float(distance_m) / 100.0) if distance_m else 0.0
                if dist_100m > 0:
                    pace_min_per_100m = duration_min / dist_100m
                    pm = int(pace_min_per_100m // 60)
                    ps = int(round((pace_min_per_100m % 1) * 60))
                    if ps == 60:
                        pm += 1
                        ps = 0
                    pace = f"{pm}m {ps:02d}s/100m"
            else:
                pace_min_per_km = duration_min / distance_km_val
                pm = int(pace_min_per_km // 60)
                ps = int(round((pace_min_per_km % 1) * 60))
                if ps == 60:
                    pm += 1
                    ps = 0
                pace = f"{pm}m {ps:02d}s/km"

        def fmt_int(v):
            try:
                return str(int(round(float(v))))
            except Exception:
                return "—"

        avg_hr = fmt_int(summary_dict.get("averageHR"))
        max_hr = fmt_int(summary_dict.get("maxHR"))
        calories = fmt_int(summary_dict.get("calories"))
        elev_gain = fmt_int(summary_dict.get("elevationGain"))
        elev_loss = fmt_int(summary_dict.get("elevationLoss"))

        # Swim normalization helpers (pool length -> 50m)
        def _pool_length_m(summary: dict[str, Any]) -> float | None:
            raw = summary.get("poolLength")
            if raw is None:
                return None
            try:
                v = float(raw)
            except Exception:
                return None
            if v <= 0:
                return None
            unit = summary.get("unitOfPoolLength")
            if isinstance(unit, dict):
                factor = unit.get("factor")
                try:
                    f = float(factor) if factor is not None else None
                except Exception:
                    f = None
                if f and f > 0:
                    v = v / f
            if v <= 0 or v > 200:
                return None
            return v

        swim_pool_m = _pool_length_m(summary_dict) if type_key == "swimming" else None
        swim_norm_factor = (50.0 / swim_pool_m) if (swim_pool_m and swim_pool_m > 0) else 1.0

        title = summary_dict.get("activityName") or f"Activité {activity_id}"
        subtitle = f"ID {activity_id}"

        planned = None
        try:
            date_src = summary_dict.get("startTimeLocal") or summary_dict.get("startTimeGMT") or ""
            date_only = str(date_src).split(" ")[0] if date_src else ""
            if date_only:
                for t in _load_planned_trainings():
                    if str(t.get("date")) == date_only and str(t.get("sport")) == str(type_key):
                        planned = t
                        break
        except Exception:
            planned = None

        def fmt_seconds(v: Any) -> float:
            try:
                return float(v) if v is not None else 0.0
            except Exception:
                return 0.0

        def fmt_duration_hms(seconds: Any) -> str:
            s = int(round(fmt_seconds(seconds)))
            if s <= 0:
                return "—"
            h = s // 3600
            m = (s % 3600) // 60
            sec = s % 60
            if h:
                return f"{h}h {m:02d}m {sec:02d}s"
            return f"{m}m {sec:02d}s"

        def fmt_distance_km(meters: Any) -> str:
            try:
                m = float(meters) if meters is not None else 0.0
            except Exception:
                m = 0.0
            if m <= 0:
                return "—"
            return f"{m / 1000.0:.2f} km"

        def fmt_pace(seconds: Any, meters: Any, *, per_100m: bool = False) -> str:
            s = fmt_seconds(seconds)
            try:
                m = float(meters) if meters is not None else 0.0
            except Exception:
                m = 0.0
            if s <= 0 or m <= 0:
                return "—"
            denom = (m / 100.0) if per_100m else (m / 1000.0)
            if denom <= 0:
                return "—"
            pace_s = s / denom
            mm = int(pace_s // 60)
            ss = int(round(pace_s % 60))
            if ss == 60:
                mm += 1
                ss = 0
            unit = " /100m" if per_100m else " /km"
            return f"{mm}:{ss:02d}{unit}"

        def fmt_speed_kmh(seconds: Any, meters: Any) -> str:
            s = fmt_seconds(seconds)
            try:
                m = float(meters) if meters is not None else 0.0
            except Exception:
                m = 0.0
            if s <= 0 or m <= 0:
                return "—"
            kmh = (m / s) * 3.6
            return f"{kmh:.1f} km/h"

        def fmt_optional_int(v: Any) -> str:
            try:
                if v is None:
                    return "—"
                return str(int(round(float(v))))
            except Exception:
                return "—"

        def fmt_optional_float(v: Any, decimals: int = 1) -> str:
            try:
                if v is None:
                    return "—"
                return f"{float(v):.{decimals}f}"
            except Exception:
                return "—"

        details_dict = details_bundle.get("details") if isinstance(details_bundle, dict) else None
        metric_descriptors = []
        points_count = None
        if isinstance(details_dict, dict):
            md = details_dict.get("metricDescriptors")
            if isinstance(md, list):
                metric_descriptors = [d for d in md if isinstance(d, dict) and d.get("key")]
            points_count = details_dict.get("measurementCount")
            if points_count is None:
                adm = details_dict.get("activityDetailMetrics")
                points_count = len(adm) if isinstance(adm, list) else None

        metric_keys = [str(d.get("key")) for d in metric_descriptors if d.get("key")]

        training_items = []
        training_map: list[tuple[str, str, str]] = [
            ("activityTrainingLoad", "Charge entraînement", "int"),
            ("aerobicTrainingEffect", "Effet aérobie", "float"),
            ("anaerobicTrainingEffect", "Effet anaérobie", "float"),
            ("moderateIntensityMinutes", "Minutes modérées", "int"),
            ("vigorousIntensityMinutes", "Minutes intenses", "int"),
            ("differenceBodyBattery", "Body Battery Δ", "int"),
            ("vO2MaxValue", "VO2max", "float"),
            ("avgPower", "Puissance moy.", "int"),
            ("maxPower", "Puissance max", "int"),
            ("normalizedPower", "Puissance normalisée", "int"),
            ("averageRunningCadenceInStepsPerMinute", "Cadence moy.", "int"),
            ("maxRunningCadenceInStepsPerMinute", "Cadence max", "int"),
            ("averageSwimCadenceInStrokesPerMinute", "Cadence natation (coups/min)", "int"),
            ("averageSwolf", "SWOLF moyen", "int"),
            ("activeLengths", "Longueurs", "int"),
            ("strokes", "Coups de bras", "int"),
            ("avgStrokes", "Coups/longueur (moy.)", "float"),
        ]
        for key, label, kind in training_map:
            val = summary_dict.get(key)
            if val is None:
                continue
            if kind == "int":
                sval = fmt_optional_int(val)
            else:
                sval = fmt_optional_float(val, 1)
            if sval == "—":
                continue
            training_items.append({"label": label, "value": sval})

        has_gps = False
        if isinstance(details_dict, dict):
            g = details_dict.get("geoPolylineDTO")
            has_gps = isinstance(g, dict) and bool(g)

        map_polyline: str | None = None
        if isinstance(details_dict, dict):
            g = details_dict.get("geoPolylineDTO")
            if isinstance(g, dict):
                poly = g.get("polyline")
                if isinstance(poly, str) and poly.strip():
                    map_polyline = poly.strip()
                elif isinstance(poly, dict):
                    for k in ["encodedPolyline", "polyline", "value"]:
                        v = poly.get(k)
                        if isinstance(v, str) and v.strip():
                            map_polyline = v.strip()
                            break

        hr_zones_rows = []
        hr_zones = details_bundle.get("hr_zones") if isinstance(details_bundle, dict) else None
        if isinstance(hr_zones, list) and hr_zones:
            total = 0.0
            for z in hr_zones:
                if isinstance(z, dict):
                    total += fmt_seconds(z.get("secsInZone"))
            for z in hr_zones:
                if not isinstance(z, dict):
                    continue
                zn_raw = z.get("zoneNumber")
                try:
                    zn = int(zn_raw) if zn_raw is not None else 0
                except Exception:
                    zn = 0
                color = f"var(--zone{zn})" if 1 <= zn <= 5 else "var(--accent)"
                secs = fmt_seconds(z.get("secsInZone"))
                pct = (secs / total * 100.0) if total > 0 else 0.0
                hr_zones_rows.append(
                    {
                        "zone": f"Z{fmt_optional_int(z.get('zoneNumber'))}",
                        "secs": int(round(secs)),
                        "duration": fmt_duration_hms(secs),
                        "low": fmt_optional_int(z.get("zoneLowBoundary")),
                        "percent": round(pct, 1),
                        "color": color,
                    }
                )

        laps_rows = []
        splits_root = details_bundle.get("splits") if isinstance(details_bundle, dict) else None
        lap_dtos = splits_root.get("lapDTOs") if isinstance(splits_root, dict) else None
        if isinstance(lap_dtos, list) and lap_dtos:
            for lap in lap_dtos:
                if not isinstance(lap, dict):
                    continue
                dist = lap.get("distance")
                dur = lap.get("duration") or lap.get("elapsedDuration")
                per_100m = type_key == "swimming"
                laps_rows.append(
                    {
                        "idx": fmt_optional_int(lap.get("lapIndex")),
                        "distance": fmt_distance_km(dist),
                        "duration": fmt_duration_hms(dur),
                        "pace": fmt_pace(dur, dist, per_100m=per_100m),
                        "speed": fmt_speed_kmh(dur, dist),
                        "avg_hr": fmt_optional_int(lap.get("averageHR")),
                        "max_hr": fmt_optional_int(lap.get("maxHR")),
                        "avg_cad": fmt_optional_int(lap.get("averageRunCadence")),
                        "avg_pwr": fmt_optional_int(lap.get("averagePower")),
                    }
                )

        typed_splits_rows = []
        typed = details_bundle.get("typed_splits") if isinstance(details_bundle, dict) else None
        typed_splits = typed.get("splits") if isinstance(typed, dict) else None
        if isinstance(typed_splits, list) and typed_splits:
            for s in typed_splits:
                if not isinstance(s, dict):
                    continue
                dist = s.get("distance")
                dur = s.get("duration") or s.get("elapsedDuration")
                per_100m = type_key == "swimming"
                typed_splits_rows.append(
                    {
                        "type": str(s.get("type") or "—"),
                        "distance": fmt_distance_km(dist),
                        "duration": fmt_duration_hms(dur),
                        "pace": fmt_pace(dur, dist, per_100m=per_100m),
                        "speed": fmt_speed_kmh(dur, dist),
                        "avg_hr": fmt_optional_int(s.get("averageHR")),
                        "max_hr": fmt_optional_int(s.get("maxHR")),
                        "avg_cad": fmt_optional_int(s.get("averageRunCadence")),
                        "avg_pwr": fmt_optional_int(s.get("averagePower")),
                        "cal": fmt_optional_int(s.get("calories")),
                    }
                )

        def _details_mtime() -> float:
            path = os.path.join("data", f"{creds.user_id}_activity_details.json")
            try:
                return float(os.path.getmtime(path))
            except OSError:
                return 0.0

        def _safe_float(v: Any) -> float | None:
            try:
                if v is None:
                    return None
                return float(v)
            except Exception:
                return None

        def _format_hms(seconds: float) -> str:
            s = int(round(seconds))
            if s < 0:
                s = 0
            h = s // 3600
            m = (s % 3600) // 60
            sec = s % 60
            if h:
                return f"{h}:{m:02d}:{sec:02d}"
            return f"{m}:{sec:02d}"

        def _build_index_map() -> dict[str, int]:
            idx: dict[str, int] = {}
            if not isinstance(details_dict, dict):
                return idx
            md = details_dict.get("metricDescriptors")
            if not isinstance(md, list):
                return idx
            for d in md:
                if not isinstance(d, dict):
                    continue
                k = d.get("key")
                mi = d.get("metricsIndex")
                if k and isinstance(mi, int):
                    idx[str(k)] = mi
            return idx

        def _collect_series(rows: list[dict[str, Any]], idx: int | None) -> list[float | None]:
            if idx is None:
                return []
            out: list[float | None] = []
            for r in rows:
                metrics = r.get("metrics") if isinstance(r, dict) else None
                if not isinstance(metrics, list) or idx >= len(metrics):
                    out.append(None)
                    continue
                out.append(_safe_float(metrics[idx]))
            return out

        activity_graphs: list[dict[str, str]] = []
        graph_urls: list[str] = []
        if isinstance(details_dict, dict):
            rows_raw = details_dict.get("activityDetailMetrics")
            rows = [r for r in rows_raw if isinstance(r, dict)] if isinstance(rows_raw, list) else []
            idx_map = _build_index_map()

            # Prefer elapsed time for x-axis.
            elapsed_idx = idx_map.get("sumElapsedDuration")
            ts_idx = idx_map.get("directTimestamp")
            x: list[str] = []
            if rows and elapsed_idx is not None:
                elapsed = _collect_series(rows, elapsed_idx)
                x = [_format_hms(v) if isinstance(v, (int, float)) else "" for v in elapsed]
            elif rows and ts_idx is not None:
                # directTimestamp seems to be ms epoch. Keep it simple: show hh:mm:ss from epoch.
                import datetime as _dt

                ts = _collect_series(rows, ts_idx)
                for v in ts:
                    if v is None:
                        x.append("")
                        continue
                    try:
                        dt = _dt.datetime.utcfromtimestamp(float(v) / 1000.0)
                        x.append(dt.strftime("%H:%M:%S"))
                    except Exception:
                        x.append("")
            else:
                x = [str(i) for i in range(len(rows))]

            def add_chart(
                metric_id: str,
                title: str,
                y_label: str,
                y: Sequence[float | None],
                *,
                color: str = "#4CC9F0",
                y_axis_min_override: float | None = None,
                y_axis_max_override: float | None = None,
                y_series_colors: list[str | None] | None = None,
                is_pace_graph: bool = False,
            ) -> None:
                if not y or all(v is None for v in y):
                    return
                out_rel = f"activity/detail/{activity_id}__{metric_id}.html"
                out_path = os.path.join("static", *out_rel.split("/"))
                mtime = _details_mtime()
                try:
                    if os.path.exists(out_path) and os.path.getmtime(out_path) >= mtime:
                        graph_urls.append(url_for("static", filename=out_rel))
                        return
                except OSError:
                    pass

                pace_ticks = None
                if is_pace_graph and y_axis_min_override is not None and y_axis_max_override is not None:
                    pace_ticks = _generate_pace_ticks(y_axis_min_override, y_axis_max_override)

                write_timeseries_chart_html(
                    out_path,
                    title=title,
                    x=x,
                    y=list(y),
                    y_label=y_label,
                    color=color,
                    y_series_colors=y_series_colors,
                    is_pace_graph=is_pace_graph,
                    y_axis_min_override=y_axis_min_override,
                    y_axis_max_override=y_axis_max_override,
                    y_ticks=pace_ticks,
                    interaction="fit",
                )
                graph_urls.append(url_for("static", filename=out_rel))

            # Candidate series from detail metrics
            hr = _collect_series(rows, idx_map.get("directHeartRate") or idx_map.get("heartRate"))
            speed_ms = _collect_series(rows, idx_map.get("directSpeed") or idx_map.get("speed"))
            run_cad = _collect_series(rows, idx_map.get("directRunCadence") or idx_map.get("directDoubleCadence"))
            bike_cad = _collect_series(rows, idx_map.get("directBikeCadence") or idx_map.get("directCadence"))
            power = _collect_series(rows, idx_map.get("directPower") or idx_map.get("directBikePower"))

            swim_cad = _collect_series(
                rows,
                idx_map.get("directSwimCadence")
                or idx_map.get("swimCadence")
                or idx_map.get("directDoubleCadence")
                or idx_map.get("directRunCadence"),
            )
            swolf = _collect_series(
                rows,
                idx_map.get("directSwolf")
                or idx_map.get("swolf")
                or idx_map.get("directSwimSwolf")
                or idx_map.get("swolfScore"),
            )

            # Fallback: some activities expose SWOLF/cadence only in summary, not as a time series.
            if x and (not swolf or all(v is None for v in swolf)):
                v = _safe_float(summary_dict.get("averageSwolf"))
                if v is not None:
                    swolf = [v * swim_norm_factor for _ in range(len(x))]

            if x and (not swim_cad or all(v is None for v in swim_cad)):
                v = _safe_float(summary_dict.get("averageSwimCadenceInStrokesPerMinute"))
                if v is not None:
                    swim_cad = [v for _ in range(len(x))]

            if x and swolf and any(v is not None for v in swolf) and swim_norm_factor != 1.0:
                swolf = [((float(v) * swim_norm_factor) if v is not None else None) for v in swolf]

            # Derived series
            speed_kmh: list[float | None] = []
            for v in speed_ms:
                speed_kmh.append((v * 3.6) if isinstance(v, (int, float)) else None)

            pace_min_km: list[float | None] = []
            for v in speed_ms:
                if isinstance(v, (int, float)) and v and v > 0:
                    pace_min_km.append((1000.0 / v) / 60.0)
                else:
                    pace_min_km.append(None)

            pace_min_100m: list[float | None] = []
            for v in speed_ms:
                if isinstance(v, (int, float)) and v and v > 0:
                    pace_min_100m.append((100.0 / v) / 60.0)
                else:
                    pace_min_100m.append(None)

            hr_zones_compat = None
            if isinstance(hr_zones, list) and len(hr_zones) >= 5:
                parsed_zones = []
                for z in hr_zones:
                    if not isinstance(z, dict):
                        continue
                    parsed_zones.append({"min": z.get("zoneLowBoundary"), "max": z.get("zoneHighBoundary")})
                if len(parsed_zones) >= 5:
                    hr_zones_compat = parsed_zones

            hr_colors = None
            hr_max = None
            if hr_zones_compat:
                from garmin_tracker.activity_manager import _assign_zone_colors

                hr_colors = _assign_zone_colors(hr, hr_zones_compat)
                hr_max = hr_zones_compat[4].get("max", 200)

            # Pick up to 3 charts depending on sport.
            if type_key == "running":
                add_chart(
                    "hr",
                    "Fréquence cardiaque",
                    "BPM",
                    hr,
                    y_series_colors=hr_colors,
                    y_axis_max_override=hr_max,
                )
                add_chart(
                    "pace",
                    "Allure",
                    "min/km",
                    pace_min_km,
                    y_axis_min_override=3.0,
                    y_axis_max_override=7.0,
                    is_pace_graph=True,
                )
                add_chart(
                    "cadence",
                    "Cadence",
                    "pas/min",
                    run_cad,
                    y_axis_min_override=0.0,
                    y_axis_max_override=200.0,
                )
            elif type_key == "cycling":
                add_chart(
                    "hr",
                    "Fréquence cardiaque",
                    "BPM",
                    hr,
                    y_series_colors=hr_colors,
                    y_axis_max_override=hr_max,
                )
                add_chart("speed", "Vitesse", "km/h", speed_kmh)
                add_chart("power", "Puissance", "W", power)
            elif type_key == "swimming":
                add_chart(
                    "pace",
                    "Allure",
                    "min/100m",
                    pace_min_100m,
                    y_axis_min_override=1.0,
                    y_axis_max_override=3.0,
                    is_pace_graph=True,
                )
                add_chart(
                    "cadence",
                    "Cadence",
                    "coups/min",
                    swim_cad,
                    color="#FF4D8D",
                    y_axis_min_override=0.0,
                    y_axis_max_override=200.0,
                )
                add_chart("swolf", "SWOLF", "score (50m)", swolf, color="#FF4D8D")
                add_chart("speed", "Vitesse", "km/h", speed_kmh)
                add_chart(
                    "hr",
                    "Fréquence cardiaque",
                    "BPM",
                    hr,
                    y_series_colors=hr_colors,
                    y_axis_max_override=hr_max,
                )
            elif type_key == "strength_training":
                add_chart(
                    "hr",
                    "Fréquence cardiaque",
                    "BPM",
                    hr,
                    y_series_colors=hr_colors,
                    y_axis_max_override=hr_max,
                )
                add_chart("power", "Puissance", "W", power)
            else:
                add_chart(
                    "hr",
                    "Fréquence cardiaque",
                    "BPM",
                    hr,
                    y_series_colors=hr_colors,
                    y_axis_max_override=hr_max,
                )
                add_chart("speed", "Vitesse", "km/h", speed_kmh)
                add_chart("power", "Puissance", "W", power)

        return render_template(
            "activity_detail.html",
            activity_id=activity_id,
            title=title,
            subtitle=subtitle,
            sport_label=sport_label,
            summary=summary_dict,
            planned=planned,
            details_bundle=details_bundle,
            distance_km=(round(distance_km_val, 2) if distance_km_val else 0),
            duration_hm=duration_hm,
            pace=pace,
            avg_hr=avg_hr,
            max_hr=max_hr,
            calories=calories,
            elev_gain=elev_gain,
            elev_loss=elev_loss,
            has_gps=has_gps,
            map_polyline=map_polyline,
            points_count=points_count,
            metric_keys=metric_keys,
            training_items=training_items,
            hr_zones_rows=hr_zones_rows,
            laps_rows=laps_rows,
            typed_splits_rows=typed_splits_rows,
            graph_urls=graph_urls,
        )

    @app.post(f"{URL_PREFIX}/update_activity")
    @require_login
    def update_activity():
        creds = require_creds()
        garmin_password = (request.form.get("garmin_password") or "").strip()
        if not garmin_password:
            flash("Mot de passe Garmin requis pour synchroniser.", "error")
            return redirect(url_for("activity"))

        def run(progress):
            progress(2.0, "Connexion Garmin…")
            handler = GarminClientHandler(creds.email, garmin_password, creds.user_id)
            handler.login()
            progress(5.0, "Synchronisation des activités…")
            handler.update_activity_data(progress=progress)
            repo.invalidate_prefix(f"activities:{creds.user_id}")
            repo.invalidate_prefix(f"activity_details:{creds.user_id}")

        task_id = tasks.start(kind="sync_activities", user_id=creds.user_id, target=run)
        flash("Mise à jour des activités en cours…", "success")
        return redirect(url_for("activity", task=task_id))

    @app.get(f"{URL_PREFIX}/health")
    @require_login
    def health():
        creds = require_creds()
        _, health_manager, _ = build_managers(creds)

        task_id = (request.args.get("task") or "").strip() or None
        task_status_url = None
        task_running = False
        if task_id:
            status = tasks.get(task_id)
            if status and status.user_id == creds.user_id:
                task_status_url = url_for("task_status", task_id=task_id)
                task_running = status.state == "running"
            else:
                task_id = None

        if not task_running:
            health_manager.plot_interactive_graphs("static/health")
        health_graphs = [
            url_for("static", filename=f"health/{f}")
            for f in os.listdir("static/health")
            if f.endswith(".html")
        ]
        return render_template(
            "health.html",
            graphs=health_graphs,
            task_id=task_id,
            task_status_url=task_status_url,
        )

    @app.post(f"{URL_PREFIX}/update_health")
    @require_login
    def update_health():
        creds = require_creds()
        garmin_password = (request.form.get("garmin_password") or "").strip()
        if not garmin_password:
            flash("Mot de passe Garmin requis pour synchroniser.", "error")
            return redirect(url_for("health"))

        def run(progress):
            progress(2.0, "Connexion Garmin…")
            handler = GarminClientHandler(creds.email, garmin_password, creds.user_id)
            handler.login()
            progress(5.0, "Synchronisation de la santé…")
            handler.update_health_data(progress=progress)
            repo.invalidate_prefix(f"health_stats:{creds.user_id}")
            repo.invalidate_prefix(f"health_daily:{creds.user_id}")

        task_id = tasks.start(kind="sync_health", user_id=creds.user_id, target=run)
        flash("Mise à jour santé en cours…", "success")
        return redirect(url_for("health", task=task_id))

    @app.get(f"{URL_PREFIX}/api/tasks/<task_id>")
    @require_login
    def task_status(task_id: str):
        creds = require_creds()
        status = tasks.get(task_id)
        if not status or status.user_id != creds.user_id:
            return jsonify({"error": "not_found"}), 404
        return jsonify(
            {
                "task_id": status.task_id,
                "kind": status.kind,
                "state": status.state,
                "percent": status.percent,
                "message": status.message,
                "error": status.error,
            }
        )

    @app.get(f"{URL_PREFIX}/training")
    @require_login
    def training():
        creds = require_creds()
        # Keep this route lightweight: the calendar fetches month-scoped events via JSON API.
        trainings = [t for t in _load_planned_trainings() if isinstance(t, dict) and (not str(t.get("user_id") or "").strip().lower() or str(t.get("user_id") or "").strip().lower() == creds.user_id)]
        competitions = [c for c in _load_competitions() if isinstance(c, dict) and (not str(c.get("user_id") or "").strip().lower() or str(c.get("user_id") or "").strip().lower() == creds.user_id)]
        return render_template("training.html", trainings=trainings, competitions=competitions)

    @app.get(f"{URL_PREFIX}/api/activity_as_training")
    @require_login
    def api_activity_as_training():
        creds = require_creds()
        activity_id = (request.args.get("activity_id") or "").strip()
        if not activity_id:
            return jsonify({"error": "missing_activity_id"}), 400

        # Find activity in user's dataset
        found = None
        for a in repo.activities(creds.user_id):
            if not isinstance(a, dict):
                continue
            if str(a.get("activityId")) == str(activity_id):
                found = a
                break
        if not found:
            return jsonify({"error": "not_found"}), 404

        raw_key = ((found.get("activityType") or {}).get("typeKey") or "")
        sport = _canonical_sport_type(raw_key) or "other"
        try:
            dist_km = float(found.get("distance") or 0.0) / 1000.0
        except Exception:
            dist_km = 0.0

        dt_txt = found.get("startTimeLocal") or found.get("startTimeGMT") or ""
        date_only = str(dt_txt).split(" ")[0] if dt_txt else ""

        return jsonify(
            {
                "activity_id": found.get("activityId"),
                "title": found.get("activityName") or found.get("name") or "Séance",
                "sport": sport,
                "distance_km": round(dist_km, 2) if dist_km > 0 else None,
                "date_source": date_only,
            }
        )

    @app.get(f"{URL_PREFIX}/api/calendar_events")
    @require_login
    def api_calendar_events():
        """Return month-scoped calendar events.

        This avoids embedding the full activities dataset in the HTML, which can
        slow down page transitions significantly.
        """

        creds = require_creds()

        today = dt.date.today()
        year = _parse_int(request.args.get("year")) or today.year
        month = _parse_int(request.args.get("month")) or today.month

        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            return jsonify({"error": "invalid_month"}), 400

        start = dt.date(year, month, 1)
        if month == 12:
            end = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
        else:
            end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

        def _parse_date_only(v: Any) -> dt.date | None:
            try:
                s = str(v or "").split(" ")[0]
                if not s:
                    return None
                return dt.date.fromisoformat(s)
            except Exception:
                return None

        def _simplify_text(v: Any) -> str:
            txt = str(v or "")
            txt = unicodedata.normalize("NFKD", txt)
            txt = txt.encode("ascii", "ignore").decode("ascii")
            return txt.lower()

        def _infer_training_sport(t: dict[str, Any]) -> str:
            sport = str(t.get("sport") or "other")
            if sport and sport != "other":
                return sport
            title = _simplify_text(t.get("title") or t.get("name") or "")
            if "velo" in title or "cycling" in title or "bike" in title:
                return "cycling"
            if "nat" in title or "swim" in title or "piscine" in title:
                return "swimming"
            if "muscu" in title or "strength" in title:
                return "strength_training"
            if "course" in title or "running" in title or "tapis" in title or "footing" in title:
                return "running"
            return "other"

        def _activity_date_only(a: dict[str, Any]) -> str:
            dt_txt = a.get("startTimeLocal") or a.get("startTimeGMT") or ""
            return str(dt_txt).split(" ")[0] if dt_txt else ""

        activities_raw = repo.activities(creds.user_id)

        # Precompute best activity (by duration) for a given date/sport in this month.
        best_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        best_dur: dict[tuple[str, str], float] = {}
        for a in activities_raw:
            if not isinstance(a, dict):
                continue
            date_only = _activity_date_only(a)
            d = _parse_date_only(date_only)
            if not d or d < start or d > end:
                continue
            sport_key = _canonical_sport_type(((a.get("activityType") or {}).get("typeKey") or "")) or "other"
            if sport_key == "other":
                continue
            try:
                dur = float(a.get("duration") or 0.0)
            except Exception:
                dur = 0.0
            k = (date_only, sport_key)
            if dur > best_dur.get(k, -1.0):
                best_dur[k] = dur
                best_by_key[k] = a

        trainings_raw = [t for t in _load_planned_trainings() if isinstance(t, dict)]
        competitions_raw = [c for c in _load_competitions() if isinstance(c, dict)]

        def _belongs_to_user(item: dict[str, Any]) -> bool:
            uid = str(item.get("user_id") or "").strip().lower()
            return (not uid) or uid == creds.user_id

        trainings_raw = [t for t in trainings_raw if _belongs_to_user(t)]
        competitions_raw = [c for c in competitions_raw if _belongs_to_user(c)]

        enriched_trainings: list[dict[str, Any]] = []
        for t in trainings_raw:
            date_only = str(t.get("date") or "")
            d = _parse_date_only(date_only)
            if not d or d < start or d > end:
                continue
            sport = _infer_training_sport(t)
            out = dict(t)
            if (str(out.get("sport") or "other") in ("", "other")) and sport != "other":
                out["sport"] = sport
            linked = best_by_key.get((date_only, sport)) if (sport and sport != "other") else None
            if linked:
                out["linked_activity_id"] = linked.get("activityId")
                out["linked_activity_name"] = linked.get("activityName") or linked.get("name")
            enriched_trainings.append(out)

        planned_keys: set[tuple[str, str]] = set()
        for t in enriched_trainings:
            date_only = str(t.get("date") or "")
            sport_key = str(t.get("sport") or "other")
            if date_only and sport_key and sport_key != "other":
                planned_keys.add((date_only, sport_key))

        enriched_competitions: list[dict[str, Any]] = []
        for c in competitions_raw:
            date_only = str(c.get("date") or "")
            d = _parse_date_only(date_only)
            if not d or d < start or d > end:
                continue
            enriched_competitions.append(dict(c))

        seen_ids: set[int] = set()
        activities: list[dict[str, Any]] = []
        for a in activities_raw:
            if not isinstance(a, dict):
                continue
            date_only = (a.get("startTimeLocal") or a.get("startTimeGMT") or "").split(" ")[0]
            d = _parse_date_only(date_only)
            if not d or d < start or d > end:
                continue

            aid = a.get("activityId")
            if isinstance(aid, int):
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)

            sport_key = _canonical_sport_type(((a.get("activityType") or {}).get("typeKey") or "")) or "other"
            # If a planned training exists for the same date/sport, keep only the training in the calendar.
            if sport_key != "other" and (date_only, sport_key) in planned_keys:
                continue

            activities.append(
                {
                    "activity_id": aid,
                    "name": a.get("name") or a.get("activityName") or "Nom non spécifié",
                    "date": date_only,
                    "distance": a.get("distance", 0),
                    "description": a.get("description", "Aucune description disponible"),
                    "locationName": a.get("locationName"),
                    "sport": sport_key,
                }
            )

        return jsonify(
            {
                "year": year,
                "month": month,
                "trainings": enriched_trainings,
                "competitions": enriched_competitions,
                "activities": activities,
            }
        )

    @app.get(f"{URL_PREFIX}/training/<training_id>")
    @require_login
    def training_detail(training_id: str):
        creds = require_creds()
        trainings = _load_planned_trainings()
        item = next(
            (
                t
                for t in trainings
                if str(t.get("id")) == str(training_id)
                and ((not str(t.get("user_id") or "").strip().lower()) or str(t.get("user_id") or "").strip().lower() == creds.user_id)
            ),
            None,
        )
        if not item:
            flash("Entraînement introuvable.", "error")
            return redirect(url_for("training"))

        # Best-effort link to a collected activity (same date + sport).
        activities_raw = repo.activities(creds.user_id)
        date_only = str(item.get("date") or "")
        sport_key = str(item.get("sport") or "other")
        linked_activity_id = None
        linked_activity_name = None
        if date_only and sport_key and sport_key != "other":
            best = None
            best_score = -1.0
            for a in activities_raw:
                if not isinstance(a, dict):
                    continue
                a_date = str((a.get("startTimeLocal") or a.get("startTimeGMT") or "")).split(" ")[0]
                if a_date != date_only:
                    continue
                a_sport = _canonical_sport_type(((a.get("activityType") or {}).get("typeKey") or "")) or "other"
                if a_sport != sport_key:
                    continue
                try:
                    dur = float(a.get("duration") or 0.0)
                except Exception:
                    dur = 0.0
                if dur > best_score:
                    best = a
                    best_score = dur
            if best:
                linked_activity_id = best.get("activityId")
                linked_activity_name = best.get("activityName") or best.get("name")

        sport_labels = {
            "running": "Course à pied",
            "cycling": "Vélo",
            "swimming": "Natation",
            "strength_training": "Musculation",
            "other": "Autre",
        }
        return render_template(
            "training_detail.html",
            t=item,
            sport_label=sport_labels.get(str(item.get("sport")), "Autre"),
            linked_activity_id=linked_activity_id,
            linked_activity_name=linked_activity_name,
        )

    @app.post(f"{URL_PREFIX}/training/<training_id>/feedback")
    @require_login
    def training_feedback(training_id: str):
        creds = require_creds()
        trainings = _load_planned_trainings()
        idx = None
        for i, t in enumerate(trainings):
            if not isinstance(t, dict):
                continue
            if str(t.get("id")) != str(training_id):
                continue
            uid = str(t.get("user_id") or "").strip().lower()
            if uid and uid != creds.user_id:
                continue
            idx = i
            break

        if idx is None:
            flash("Entraînement introuvable.", "error")
            return redirect(url_for("training"))

        done = (request.form.get("done") or "").strip().lower() in {"1", "true", "on", "yes"}
        feeling = (request.form.get("feeling") or "").strip()
        post_notes = (request.form.get("post_notes") or "").strip()

        updated = dict(trainings[idx])
        # Ensure ownership is set going forward.
        if not str(updated.get("user_id") or "").strip().lower():
            updated["user_id"] = creds.user_id
        updated["done"] = bool(done)
        updated["feeling"] = feeling
        updated["post_notes"] = post_notes
        trainings[idx] = updated
        _save_planned_trainings(trainings)

        flash("Retour de séance enregistré.", "success")
        return redirect(url_for("training_detail", training_id=training_id))

    @app.get(f"{URL_PREFIX}/competition/<competition_id>")
    @require_login
    def competition_detail(competition_id: str):
        creds = require_creds()
        competitions = _load_competitions()
        item = next(
            (
                c
                for c in competitions
                if str(c.get("id")) == str(competition_id)
                and ((not str(c.get("user_id") or "").strip().lower()) or str(c.get("user_id") or "").strip().lower() == creds.user_id)
            ),
            None,
        )
        if not item:
            flash("Compétition introuvable.", "error")
            return redirect(url_for("training"))

        sport_labels = {
            "running": "Course à pied",
            "cycling": "Vélo",
            "swimming": "Natation",
            "strength_training": "Musculation",
            "other": "Autre",
        }

        matched_activities: list[dict[str, Any]] = []
        try:
            target_date = str(item.get("date") or "")
            target_sport = str(item.get("sport") or "other")
            if target_date and target_sport:
                for a in repo.activities(creds.user_id):
                    if not isinstance(a, dict):
                        continue
                    date_only = str(a.get("startTimeLocal") or a.get("startTimeGMT") or "").split(" ")[0]
                    if date_only != target_date:
                        continue
                    raw_key = ((a.get("activityType") or {}).get("typeKey") or "")
                    a_sport = _canonical_sport_type(raw_key) or "other"
                    if a_sport != target_sport:
                        continue
                    matched_activities.append(
                        {
                            "activity_id": a.get("activityId"),
                            "name": a.get("activityName") or a.get("name") or "Activité",
                            "date": date_only,
                            "distance_m": a.get("distance"),
                        }
                    )
        except Exception:
            matched_activities = []

        return render_template(
            "competition_detail.html",
            c=item,
            sport_label=sport_labels.get(str(item.get("sport")), "Autre"),
            matched_activities=matched_activities,
        )

    # -----------------
    # JSON API (our API)
    # -----------------

    @app.get(f"{URL_PREFIX}/api/status")
    @require_login
    def api_status():
        creds = require_creds()
        return jsonify({"user_id": creds.user_id})

    @app.get(f"{URL_PREFIX}/api/activities")
    @require_login
    def api_activities():
        creds = require_creds()
        data = repo.activities(creds.user_id)
        return jsonify({"count": len(data), "items": data})

    @app.get(f"{URL_PREFIX}/api/activities/<activity_id>")
    @require_login
    def api_activity_details(activity_id: str):
        creds = require_creds()
        raw = repo.activity_details(creds.user_id)

        activities = raw.get("activities", {}) if isinstance(raw, dict) else {}
        item = activities.get(str(activity_id)) if isinstance(activities, dict) else None
        if not item:
            return jsonify({"error": "not_found"}), 404
        return jsonify(item)

    @app.get(f"{URL_PREFIX}/api/health/days")
    @require_login
    def api_health_days():
        creds = require_creds()
        raw = repo.health_daily(creds.user_id)
        days = raw.get("days", {}) if isinstance(raw, dict) else {}
        keys = sorted(days.keys()) if isinstance(days, dict) else []
        return jsonify({"count": len(keys), "days": keys})

    @app.get(f"{URL_PREFIX}/api/health/<day>")
    @require_login
    def api_health_day(day: str):
        creds = require_creds()
        raw = repo.health_daily(creds.user_id)
        days = raw.get("days", {}) if isinstance(raw, dict) else {}
        item = days.get(day) if isinstance(days, dict) else None
        if not item:
            return jsonify({"error": "not_found"}), 404
        return jsonify(item)

    @app.post(f"{URL_PREFIX}/api/sync/activities")
    @require_login
    def api_sync_activities():
        creds = require_creds()

        def run(progress):
            progress(2.0, "Connexion Garmin…")
            handler = build_garmin_handler(creds)
            progress(5.0, "Synchronisation des activités…")
            handler.update_activity_data(progress=progress)
            repo.invalidate_prefix(f"activities:{creds.user_id}")
            repo.invalidate_prefix(f"activity_details:{creds.user_id}")

        task_id = tasks.start(kind="sync_activities", user_id=creds.user_id, target=run)
        return jsonify({"ok": True, "task_id": task_id, "status_url": url_for("task_status", task_id=task_id)})

    @app.post(f"{URL_PREFIX}/api/sync/health")
    @require_login
    def api_sync_health():
        creds = require_creds()

        def run(progress):
            progress(2.0, "Connexion Garmin…")
            handler = build_garmin_handler(creds)
            progress(5.0, "Synchronisation de la santé…")
            handler.update_health_data(progress=progress)
            repo.invalidate_prefix(f"health_stats:{creds.user_id}")
            repo.invalidate_prefix(f"health_daily:{creds.user_id}")

        task_id = tasks.start(kind="sync_health", user_id=creds.user_id, target=run)
        return jsonify({"ok": True, "task_id": task_id, "status_url": url_for("task_status", task_id=task_id)})

    @app.post(f"{URL_PREFIX}/add_competition")
    @require_login
    def add_competition():
        creds = require_creds()
        name = request.form.get("name")
        date = request.form.get("date")
        location = request.form.get("location") or ""
        distance = request.form.get("distance")
        sport = (request.form.get("sport") or "other").strip() or "other"

        if not name or not date:
            flash("Nom et date sont obligatoires.", "error")
            return redirect(url_for("training"))

        competition: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "user_id": creds.user_id,
            "name": name,
            "date": date,
            "location": location,
            "sport": sport,
        }
        if distance:
            try:
                competition["distance"] = float(distance)
            except ValueError:
                pass

        competitions = _load_competitions()
        competitions.append(competition)
        _save_competitions(competitions)

        flash("Compétition ajoutée.", "success")
        return redirect(url_for("training"))

    @app.get(f"{URL_PREFIX}/remove_competition/<path:competition_id>")
    @require_login
    def remove_competition(competition_id: str):
        creds = require_creds()
        competitions = _load_competitions()
        competitions = [
            c
            for c in competitions
            if not (
                str(c.get("id")) == str(competition_id)
                and ((not str(c.get("user_id") or "").strip().lower()) or str(c.get("user_id") or "").strip().lower() == creds.user_id)
            )
        ]
        _save_competitions(competitions)

        flash("Compétition supprimée.", "success")
        return redirect(url_for("training"))

    @app.post(f"{URL_PREFIX}/add_training")
    @require_login
    def add_training():
        creds = require_creds()
        title = (request.form.get("title") or request.form.get("name") or "").strip()
        date = (request.form.get("date") or "").strip()
        sport = (request.form.get("sport") or "other").strip() or "other"
        distance = (request.form.get("distance") or "").strip()
        description = (request.form.get("description") or "").strip()
        content = (request.form.get("content") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        if not title or not date:
            flash("Titre et date sont obligatoires.", "error")
            return redirect(url_for("training"))

        training: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "user_id": creds.user_id,
            "title": title,
            "date": date,
            "sport": sport,
            "description": description,
            "content": content,
        }
        if notes:
            training["notes"] = notes
        if distance:
            try:
                training["distance_km"] = float(distance)
            except ValueError:
                pass

        trainings = _load_planned_trainings()
        trainings.append(training)
        _save_planned_trainings(trainings)

        flash("Entraînement ajouté.", "success")
        return redirect(url_for("training"))

    @app.get(f"{URL_PREFIX}/remove_training/<path:training_id>")
    @require_login
    def remove_training(training_id: str):
        creds = require_creds()
        trainings = _load_planned_trainings()
        trainings = [
            t
            for t in trainings
            if not (
                str(t.get("id")) == str(training_id)
                and ((not str(t.get("user_id") or "").strip().lower()) or str(t.get("user_id") or "").strip().lower() == creds.user_id)
            )
        ]
        _save_planned_trainings(trainings)

        flash("Entraînement supprimé.", "success")
        return redirect(url_for("training"))

    return app


# For "flask run" convenience
app = create_app()
