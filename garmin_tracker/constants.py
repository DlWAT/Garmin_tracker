"""Application-wide constants and shared helpers."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Sport label mapping (canonical key → display label in French)
# ---------------------------------------------------------------------------

SPORT_LABELS: dict[str, str] = {
    "running": "Course à pied",
    "cycling": "Vélo",
    "swimming": "Natation",
    "strength_training": "Musculation",
    "other": "Autre",
}


def sport_label(key: str | None) -> str:
    """Return the French display label for a canonical sport key."""
    return SPORT_LABELS.get(str(key or "other"), "Autre")


# ---------------------------------------------------------------------------
# Canonical sport type mapping (Garmin typeKey → our 4 canonical sports)
# ---------------------------------------------------------------------------

_RUNNING_TYPES = frozenset({
    "running",
    "treadmill_running",
    "trail_running",
    "track_running",
    "virtual_running",
    "indoor_running",
})

_CYCLING_TYPES = frozenset({
    "cycling",
    "road_biking",
    "mountain_biking",
    "gravel_cycling",
    "indoor_cycling",
    "virtual_cycling",
    "e_bike_fitness",
    "e_bike_mountain",
})

_SWIMMING_TYPES = frozenset({
    "swimming",
    "lap_swimming",
    "pool_swimming",
    "open_water_swimming",
})

_STRENGTH_TYPES = frozenset({
    "strength_training",
})


def canonical_sport_type(type_key: str | None) -> str | None:
    """Map Garmin typeKey variants to one of our 4 canonical sports.

    Returns None when the type is unknown / not mapped.
    """
    if not type_key:
        return None
    k = str(type_key)
    if k in _RUNNING_TYPES:
        return "running"
    if k in _CYCLING_TYPES:
        return "cycling"
    if k in _SWIMMING_TYPES:
        return "swimming"
    if k in _STRENGTH_TYPES:
        return "strength_training"
    return None


# ---------------------------------------------------------------------------
# Duration / pace formatting helpers
# ---------------------------------------------------------------------------

def fmt_hms(seconds: float) -> str:
    """Format a duration in seconds as H:MM:SS (or MM:SS when < 1 hour)."""
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "—"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_pace(seconds: float, meters: float, *, per_100m: bool = False) -> str:
    """Format pace as MM:SS/km (running) or MM:SS/100m (swimming)."""
    try:
        s = float(seconds)
        m = float(meters)
    except (TypeError, ValueError):
        return "—"
    if m <= 0 or s <= 0:
        return "—"
    if per_100m:
        pace_s = s / (m / 100.0)
        limit_lo, limit_hi = 60.0, 180.0  # 1–3 min/100m
    else:
        pace_s = s / (m / 1000.0)
        limit_lo, limit_hi = 180.0, 420.0  # 3–7 min/km
    if not (limit_lo <= pace_s <= limit_hi):
        return "—"
    mins, secs = divmod(int(round(pace_s)), 60)
    suffix = "/100m" if per_100m else "/km"
    return f"{mins}:{secs:02d}{suffix}"
