"""Envoi d'entraînements structurés vers Garmin Connect.

Ce module convertit un entraînement planifié (dict JSON) en workout Garmin Connect
et l'uploade via l'API. Seul le sport 'running' est pleinement supporté (les autres
sports sont uploadés sous forme de workout générique avec un seul step).

Structure attendue d'un entraînement (training dict) :
    {
        "id": "...",
        "title": "Fractionné 10x400m",
        "sport": "running",  # running | cycling | swimming | strength_training | other
        "date": "2026-05-20",             # optionnel pour l'upload, requis pour schedule
        "distance_km": 10.0,              # optionnel
        "description": "...",             # optionnel
        "content": "- 20' EF\\n- 10x400m (r=1') @4:00/km\\n- 10' RAC",  # optionnel
        "duration_min": 60,               # optionnel
    }

Allures reconnues dans le contenu (champ "content") :
    - "4:30/km"           → plage ±10 s (4:20–4:40/km)
    - "@4:30/km"          → idem
    - "allure seuil"      → ~4:10/km  (zone FR commune)
    - "allure marathon"   → ~5:00/km
    - "allure semi"       → ~4:35/km
    - "allure 10km"       → ~4:15/km
    - "vma", "vo2max"     → ~3:40/km
    - "ef", "endurance fondamentale" → ~5:30/km

Usage:
    from .garmin_workout_sender import send_training_to_garmin, schedule_workout_on_garmin

    workout_id = send_training_to_garmin(garmin_client, training_dict)
    schedule_workout_on_garmin(garmin_client, workout_id, "2026-05-20")
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sport types
# ---------------------------------------------------------------------------

_RUNNING_SPORT = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
_CYCLING_SPORT = {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2}
_SWIMMING_SPORT = {"sportTypeId": 3, "sportTypeKey": "swimming", "displayOrder": 3}


# ---------------------------------------------------------------------------
# Pace helpers
# ---------------------------------------------------------------------------

def _pace_to_ms(minutes: int, seconds: int) -> float:
    """Convert a pace in min:sec per km to m/s.

    Examples:
        _pace_to_ms(4, 30)  -> 3.7037  (4:30/km)
        _pace_to_ms(5,  0)  -> 3.3333  (5:00/km)
    """
    total_s = minutes * 60 + seconds
    return round(1000.0 / total_s, 6)


def _pace_target(
    target_min: int,
    target_sec: int,
    margin_sec: int = 10,
) -> dict[str, Any]:
    """Build a speed.zone target block for a pace (min:sec/km) ± margin.

    Garmin uses m/s internally; targetValueOne = slower bound, targetValueTwo = faster bound.
    The Connect UI and device display this as min/km.

    Args:
        target_min: Target pace minutes (e.g. 4 for 4:30/km).
        target_sec: Target pace seconds (e.g. 30 for 4:30/km).
        margin_sec: Tolerance in seconds around target (default ±10 s).

    Returns:
        Dict with targetType, targetValueOne, targetValueTwo ready to merge into a step.
    """
    total_s = target_min * 60 + target_sec
    fast_s = max(1, total_s - margin_sec)   # faster = fewer seconds/km = higher m/s
    slow_s = total_s + margin_sec           # slower = more seconds/km = lower m/s
    return {
        "targetType": {
            "workoutTargetTypeId": 5,
            "workoutTargetTypeKey": "speed.zone",
            "displayOrder": 5,
        },
        "targetValueOne": round(1000.0 / slow_s, 6),   # slower bound (lower m/s)
        "targetValueTwo": round(1000.0 / fast_s, 6),   # faster bound (higher m/s)
    }


def _parse_pace_annotation(text: str) -> dict[str, Any] | None:
    """Extract a pace annotation from a line of text and return a target block.

    Recognised patterns (case-insensitive):
        @4:30/km   4:30/km   4'30/km   4'30''   →  explicit pace
        allure seuil / seuil anaérobie          →  ~4:10/km
        allure marathon / mara                  →  ~5:00/km
        allure semi / semi-marathon             →  ~4:35/km
        allure 10km / allure 10k                →  ~4:15/km
        vma / vo2max / vitesse max              →  ~3:40/km  (margin 15s)
        ef / endurance fondamentale / easy      →  ~5:30/km  (margin 20s)

    Returns None if no pace annotation is found.
    """
    lo = text.lower()

    # ── Named zones (French) ──────────────────────────────────────────────
    named_zones = [
        (r"\bvma\b|vo2\s*max|vitesse\s*max",                (3, 40), 15),
        (r"\bseuil\b|anaérobie|anaerobie",                  (4, 10), 12),
        (r"\b10\s*km\b|allure\s*10",                        (4, 15), 10),
        (r"\bsemi[\-\s]?marathon\b|\bsemi\b",               (4, 35), 10),
        (r"\bmarathon\b|\bmara\b",                          (5,  0), 12),
        (r"\bef\b|endurance\s+fond|footing\b|easy\b",       (5, 30), 20),
    ]
    for pattern, (pmin, psec), margin in named_zones:
        if re.search(pattern, lo):
            return _pace_target(pmin, psec, margin)

    # ── Explicit pace  "@4:30/km"  "4:30/km"  ─────────────────────────────
    # Priority 1: @ prefix avoids catching recovery times like "r=1'30"
    pace_m = re.search(r"@\s*(\d+)\s*[:']\s*(\d{1,2})\s*(?:/\s*km)?", text)
    if not pace_m:
        # Priority 2: N:NN/km — /km disambiguator required (no @)
        pace_m = re.search(r"\b(\d+):(\d{2})\s*/\s*km", text)
    if pace_m:
        pmin = int(pace_m.group(1))
        psec = int(pace_m.group(2))
        if 1 <= pmin <= 15 and 0 <= psec < 60:
            return _pace_target(pmin, psec, margin_sec=10)

    return None


# ---------------------------------------------------------------------------
# Step builders
# ---------------------------------------------------------------------------

def _step(
    step_order: int,
    step_type_id: int,
    step_type_key: str,
    end_condition_id: int,
    end_condition_key: str,
    end_value: float | None = None,
    *,
    target_type_id: int = 1,
    target_type_key: str = "no.target",
    target_value_one: float | None = None,
    target_value_two: float | None = None,
) -> dict[str, Any]:
    """Build a generic ExecutableStepDTO dict.

    Note: endConditionValue is ALWAYS included when end_value is provided,
    which is required for distance-based conditions (otherwise Garmin silently
    converts them to 'lap.button').
    """
    s: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": step_order,
        "stepType": {
            "stepTypeId": step_type_id,
            "stepTypeKey": step_type_key,
            "displayOrder": step_type_id,
        },
        "endCondition": {
            "conditionTypeId": end_condition_id,
            "conditionTypeKey": end_condition_key,
            "displayOrder": end_condition_id,
            "displayable": True,
        },
        "targetType": {
            "workoutTargetTypeId": target_type_id,
            "workoutTargetTypeKey": target_type_key,
            "displayOrder": target_type_id,
        },
    }
    if end_value is not None:
        s["endConditionValue"] = end_value
    if target_value_one is not None:
        s["targetValueOne"] = target_value_one
    if target_value_two is not None:
        s["targetValueTwo"] = target_value_two
    return s


def _apply_pace_target(step: dict[str, Any], pace: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a pace target block into an existing step dict (in-place + return)."""
    if pace is None:
        return step
    step["targetType"] = pace["targetType"]
    step["targetValueOne"] = pace["targetValueOne"]
    step["targetValueTwo"] = pace["targetValueTwo"]
    return step


def _warmup_step(
    duration_s: float,
    step_order: int = 1,
    pace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    s = _step(step_order, 1, "warmup", 2, "time", duration_s)
    return _apply_pace_target(s, pace)


def _interval_step(
    duration_s: float,
    step_order: int = 2,
    pace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    s = _step(step_order, 3, "interval", 2, "time", duration_s)
    return _apply_pace_target(s, pace)


def _interval_distance_step(
    distance_m: float,
    step_order: int = 2,
    pace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Distance-based interval step.

    endConditionValue is mandatory: without it the Garmin API silently
    replaces conditionTypeKey 'distance' with 'lap.button'.
    """
    s = _step(step_order, 3, "interval", 1, "distance", float(distance_m))
    return _apply_pace_target(s, pace)


def _recovery_step(
    duration_s: float,
    step_order: int = 3,
    pace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    s = _step(step_order, 4, "recovery", 2, "time", duration_s)
    return _apply_pace_target(s, pace)


def _cooldown_step(
    duration_s: float,
    step_order: int = 4,
    pace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    s = _step(step_order, 2, "cooldown", 2, "time", duration_s)
    return _apply_pace_target(s, pace)


def _repeat_group(
    step_order: int,
    iterations: int,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": step_order,
        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
        "numberOfIterations": iterations,
        "workoutSteps": steps,
        "smartRepeat": False,
    }


def _segment(sport: dict[str, Any], steps: list[dict[str, Any]], order: int = 1) -> dict[str, Any]:
    return {
        "segmentOrder": order,
        "sportType": sport,
        "workoutSteps": steps,
    }


def _workout_payload(
    name: str,
    sport: dict[str, Any],
    segments: list[dict[str, Any]],
    description: str | None = None,
    estimated_duration_s: int = 3600,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workoutName": name,
        "sportType": sport,
        "estimatedDurationInSecs": max(1, int(estimated_duration_s)),
        "workoutSegments": segments,
    }
    if description:
        payload["description"] = description[:500]
    return payload


# ---------------------------------------------------------------------------
# Content parser  (best-effort)
# ---------------------------------------------------------------------------

def _parse_minutes(text: str) -> float | None:
    """Parse durations like '20min', "20'", '1h30', '45' → seconds (float)."""
    t = text.strip().lower()
    # e.g. "1h30" or "1h30min"
    m = re.match(r"(\d+)h(\d+)", t)
    if m:
        return float(m.group(1)) * 3600 + float(m.group(2)) * 60
    # e.g. "20min", "20'"
    m = re.match(r"(\d+(?:\.\d+)?)\s*(?:min|'|m\b)", t)
    if m:
        return float(m.group(1)) * 60
    # bare number treated as minutes
    m = re.match(r"^(\d+(?:\.\d+)?)$", t)
    if m:
        return float(m.group(1)) * 60
    return None


def _parse_content_to_steps(content: str) -> list[dict[str, Any]] | None:
    """Try to build a structured step list from free-text workout content.

    Recognized patterns (case-insensitive, French/English):
    - "20' EF"                      → warmup  (with pace if annotated)
    - "10x400m (r=1') @4:00/km"    → RepeatGroup with pace on interval
    - "5x1km r=2' allure seuil"    → RepeatGroup with named zone
    - "10' RAC"                     → cooldown
    - Generic "30min @5:00/km"      → interval with pace

    Returns None if content is too complex or unparseable.
    """
    lines = [ln.strip(" -•*") for ln in (content or "").splitlines() if ln.strip(" -•*")]
    if not lines:
        return None

    steps: list[dict[str, Any]] = []
    order = 1

    warmup_kw = {
        "ef", "endurance fondamentale", "échauffement", "warmup", "warm up",
        "echauffement", "footing", "recup", "récup", "très facile", "tres facile", "easy",
    }
    cooldown_kw = {
        "retour au calme", "rac", "cooldown", "cool down", "décompression", "decompression",
    }

    for line in lines:
        lo = line.lower()

        # ── Repeat blocks: "10x400m (r=1') @4:00/km" ──────────────────────
        rep_m = re.search(r"(\d+)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*(km|m)\b", lo)
        if rep_m:
            reps = int(rep_m.group(1))
            dist_num = float(rep_m.group(2))
            unit = rep_m.group(3)
            dist_m = dist_num * 1000 if unit == "km" else dist_num

            # Recovery duration
            rec_s = 60.0
            rec_m = re.search(r"r\s*[=:]\s*(\d+(?:\.\d+)?)\s*(?:'|min|m\b)?", lo)
            if rec_m:
                parsed = _parse_minutes(rec_m.group(1) + "'")
                if parsed:
                    rec_s = parsed

            # Pace on the interval (from the full original line, case-preserved)
            pace = _parse_pace_annotation(line)

            int_step = _interval_distance_step(dist_m, step_order=1, pace=pace)
            rec_step = _recovery_step(rec_s, step_order=2)
            group = _repeat_group(order, reps, [int_step, rec_step])
            steps.append(group)
            order += 1
            continue

        # ── Duration-based steps ───────────────────────────────────────────
        dur_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:'|min|m\b|h\d)", lo)
        duration_s = None
        if dur_m:
            duration_s = _parse_minutes(dur_m.group(0))

        if duration_s is None:
            continue

        pace = _parse_pace_annotation(line)

        is_warmup = any(kw in lo for kw in warmup_kw)
        is_cooldown = any(kw in lo for kw in cooldown_kw)

        if is_cooldown:
            steps.append(_cooldown_step(duration_s, step_order=order, pace=pace))
        elif is_warmup or order == 1:
            steps.append(_warmup_step(duration_s, step_order=order, pace=pace))
        else:
            steps.append(_interval_step(duration_s, step_order=order, pace=pace))
        order += 1

    return steps if steps else None


# ---------------------------------------------------------------------------
# Swimming content parser
# ---------------------------------------------------------------------------

def _parse_pace100_annotation(text: str) -> dict[str, Any] | None:
    """Extract a /100m pace annotation and return a speed.zone target block.

    Recognised patterns:
        @1:30/100m   1:45/100m   @2:00   [1:30–1:45/100m]
        allure seuil / seuil    → ~1:35/100m ±8s
        endurance / fond        → ~2:00/100m ±15s
        sprint / vitesse max    → ~1:10/100m ±10s
        récup / récupération    → ~2:30/100m ±20s
    """
    lo_text = text.lower()

    # Named swim zones
    named = [
        (r"\bsprint\b|vitesse\s*max",          (1, 10), 10),
        (r"\bseuil\b|anaérobie|anaerobie",      (1, 35),  8),
        (r"\bvo2\b|vo2max",                     (1, 25), 10),
        (r"\bfond\b|endurance",                 (2,  0), 15),
        (r"\brécup\b|récupération|recup",        (2, 30), 20),
    ]
    for pattern, (pmin, psec), margin in named:
        if re.search(pattern, lo_text):
            return _pace_target(pmin, psec, margin)

    # Explicit  @1:30/100m  or  1:30/100m  or  [1:30–1:45/100m]
    pace_m = re.search(r"@\s*(\d+):(\d{2})\s*(?:/\s*100\s*m)?", text)
    if not pace_m:
        pace_m = re.search(r"\b(\d+):(\d{2})\s*/\s*100\s*m", text)
    if pace_m:
        pmin, psec = int(pace_m.group(1)), int(pace_m.group(2))
        if 0 <= pmin <= 10 and 0 <= psec < 60:
            return _pace_target(pmin, psec, margin_sec=8)

    # Range  [1:30–1:45/100m]
    range_m = re.search(r"(\d+):(\d{2})\s*[–\-]\s*(\d+):(\d{2})", text)
    if range_m:
        fast_s = int(range_m.group(1)) * 60 + int(range_m.group(2))
        slow_s = int(range_m.group(3)) * 60 + int(range_m.group(4))
        if fast_s > 0 and slow_s > 0 and fast_s < slow_s:
            return {
                "targetType": {
                    "workoutTargetTypeId": 5,
                    "workoutTargetTypeKey": "speed.zone",
                    "displayOrder": 5,
                },
                "targetValueOne": round(100.0 / slow_s, 6),
                "targetValueTwo": round(100.0 / fast_s, 6),
            }

    return None


def _parse_content_to_steps_swimming(content: str) -> list[dict[str, Any]] | None:
    """Best-effort parser for swimming workout content.

    Handles the format produced by the JS step builder:
        Matériel: planche, pull-buoy
        - 400m Échauffement [crawl]
        - 50m Éducatif [crawl, catch-up, pull-buoy]
        6x
          - 100m Intervalle · 1:30–1:45/100m [crawl]
          - 30s Récupération
        - 200m Retour au calme [dos]
    """
    raw_lines = (content or "").splitlines()
    if not any(ln.strip() for ln in raw_lines):
        return None

    # Keep original lines (with indentation) but strip "Matériel:" header
    raw_lines = [ln for ln in raw_lines if not re.match(r"^\s*mat.riel\s*:", ln, re.I)]

    steps: list[dict[str, Any]] = []
    order = 1
    i = 0

    warmup_kw   = {"échauffement", "warmup", "warm up", "echauffement"}
    cooldown_kw = {"retour au calme", "rac", "cooldown", "cool down", "décompression"}
    recovery_kw = {"récupération", "recuperation", "recovery", "repos"}
    edu_kw      = {"éducatif", "educatif", "drill", "edu"}

    def _classify(lo: str) -> str:
        if any(k in lo for k in cooldown_kw): return "cooldown"
        if any(k in lo for k in recovery_kw): return "recovery"
        if any(k in lo for k in edu_kw):      return "edu"
        if any(k in lo for k in warmup_kw):   return "warmup"
        return "interval"

    def _extract_brackets(line: str) -> tuple[str, str]:
        """Return (line_without_brackets, bracket_content)."""
        m = re.search(r"\[([^\]]+)\]", line)
        if m:
            return line[:m.start()].strip() + line[m.end():].strip(), m.group(1)
        return line, ""

    def _parse_swim_extras(bracket: str) -> tuple[str | None, str | None]:
        """Parse '[crawl, catch-up, planche]' → (stroke, equipment_csv)."""
        strokes = {"crawl", "dos", "brasse", "papillon", "4nages", "4 nages"}
        equips  = {"planche", "pull-buoy", "palmes", "plaquettes", "élastique", "tuba"}
        parts   = [p.strip().lower() for p in bracket.split(",") if p.strip()]
        stroke  = next((p for p in parts if p in strokes), None)
        equip_list = [p for p in parts if p in equips]
        edu     = [p for p in parts if p not in strokes and p not in equips and p]
        desc    = ", ".join(edu) if edu else None
        return stroke, ", ".join(equip_list) if equip_list else None

    def _build_swim_step(
        step_type: str, line: str, step_order: int
    ) -> dict[str, Any] | None:
        line_clean, bracket = _extract_brackets(line)
        stroke, equip = _parse_swim_extras(bracket)
        lo = line_clean.lower()

        pace = _parse_pace100_annotation(line)

        # Distance?
        dist_m = re.search(r"(\d+(?:\.\d+)?)\s*m\b", lo)
        dur_s  = None
        dist_val = None
        if dist_m:
            dist_val = float(dist_m.group(1))
        else:
            # Duration (30s, 1min…)
            dur_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:s\b|sec\b)", lo)
            if dur_m:
                dur_s = float(dur_m.group(1))
            else:
                dur_min_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:'|min)", lo)
                if dur_min_m:
                    dur_s = float(dur_min_m.group(1)) * 60

        s: dict[str, Any]
        if step_type in ("recovery", "cooldown", "warmup", "edu") and dur_s is not None:
            fn = {"recovery": _recovery_step, "cooldown": _cooldown_step, "warmup": _warmup_step, "edu": _interval_step}
            s = fn[step_type](dur_s, step_order=step_order)
        elif dist_val is not None:
            s = _interval_distance_step(dist_val, step_order=step_order, pace=pace)
            # Override step type
            type_map = {"warmup": (1,"warmup"), "cooldown": (2,"cooldown"), "recovery": (4,"recovery"), "edu": (3,"interval")}
            if step_type in type_map:
                sid, skey = type_map[step_type]
                s["stepType"] = {"stepTypeId": sid, "stepTypeKey": skey, "displayOrder": sid}
        elif dur_s is not None:
            s = _interval_step(dur_s, step_order=step_order, pace=pace)
        else:
            return None  # unparseable

        # Attach swim extras as description tag (Garmin ignores unknown fields)
        if stroke:
            s["_stroke"] = stroke
        if equip:
            s["_equipment"] = equip
        return s

    while i < len(raw_lines):
        raw = raw_lines[i]
        line = raw.strip().lstrip("-•* ")
        lo   = line.lower()

        if not line:
            i += 1
            continue

        # Repeat block: "6x" or "6×" (line must be just the repeat marker)
        rep_m = re.match(r"^(\d+)\s*[x×]\s*$", lo)
        if rep_m:
            reps = int(rep_m.group(1))
            i += 1
            children: list[dict[str, Any]] = []
            child_order = 1
            while i < len(raw_lines) and (raw_lines[i].startswith("  ") or raw_lines[i].startswith("\t")):
                child_line = raw_lines[i].strip().lstrip("-•* ")
                if child_line:
                    ctype = _classify(child_line.lower())
                    cs = _build_swim_step(ctype, child_line, child_order)
                    if cs:
                        children.append(cs)
                        child_order += 1
                i += 1
            if children:
                steps.append(_repeat_group(order, reps, children))
                order += 1
            continue

        # Regular step
        stype = _classify(lo)
        s = _build_swim_step(stype, line, order)
        if s:
            steps.append(s)
            order += 1
        i += 1

    return steps if steps else None


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def training_to_workout_payload(training: dict[str, Any]) -> dict[str, Any]:
    """Convert a planned training dict to a Garmin workout upload payload dict."""

    sport_key = str(training.get("sport") or "other").strip().lower()
    title = (training.get("title") or "Entraînement").strip() or "Entraînement"
    description = (training.get("description") or "").strip() or None
    content = (training.get("content") or "").strip()

    # Estimated duration
    dur_min = None
    try:
        dur_min = float(training.get("duration_min") or 0) or None
    except (TypeError, ValueError):
        pass
    if dur_min is None:
        try:
            dist_km = float(training.get("distance_km") or 0) or None
            if dist_km:
                # Running: ~5 min/km; swimming: ~3 min/100m = 30 min/km
                pace_per_km = 30.0 if sport_key == "swimming" else 5.0
                dur_min = dist_km * pace_per_km
        except (TypeError, ValueError):
            pass
    estimated_s = int((dur_min or 60) * 60)

    # Sport mapping
    if sport_key == "running":
        sport = _RUNNING_SPORT
    elif sport_key == "cycling":
        sport = _CYCLING_SPORT
    elif sport_key == "swimming":
        sport = _SWIMMING_SPORT
    else:
        sport = {"sportTypeId": 8, "sportTypeKey": "other", "displayOrder": 8}

    # Try structured parsing for running and swimming workouts
    parsed_steps: list[dict[str, Any]] | None = None
    if sport_key == "running" and content:
        try:
            parsed_steps = _parse_content_to_steps(content)
        except Exception:
            logger.warning(
                "Content parser (running) failed for '%s', falling back.", title
            )
    elif sport_key == "swimming" and content:
        try:
            parsed_steps = _parse_content_to_steps_swimming(content)
        except Exception:
            logger.warning(
                "Content parser (swimming) failed for '%s', falling back.", title
            )

    if parsed_steps:
        steps = parsed_steps
    else:
        steps = [_interval_step(estimated_s, step_order=1)]

    segment = _segment(sport, steps, order=1)
    payload = _workout_payload(
        title, sport, [segment], description=description, estimated_duration_s=estimated_s
    )
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class GarminWorkoutSendError(Exception):
    """Raised when uploading or scheduling a workout fails."""


def send_training_to_garmin(garmin_client: Any, training: dict[str, Any]) -> str:
    """Upload a training as a Garmin workout and return its workout ID.

    Args:
        garmin_client: A logged-in garminconnect.Garmin instance.
        training: A planned training dict (see module docstring).

    Returns:
        The Garmin workout ID (str) of the newly created workout.

    Raises:
        GarminWorkoutSendError: on API or conversion errors.
    """
    try:
        payload = training_to_workout_payload(training)
    except Exception as e:
        raise GarminWorkoutSendError(
            f"Impossible de convertir l'entraînement en workout Garmin: {e}"
        ) from e

    upload_fn = getattr(garmin_client, "upload_workout", None)
    if not callable(upload_fn):
        raise GarminWorkoutSendError(
            "La version de la librairie garminconnect ne supporte pas upload_workout."
        )

    try:
        result = upload_fn(payload)
        logger.info("Workout uploadé: %s", result)
    except Exception as e:
        raise GarminWorkoutSendError(
            f"Erreur lors de l'upload vers Garmin Connect: {e}"
        ) from e

    workout_id: str | None = None
    if isinstance(result, dict):
        workout_id = (
            str(result.get("workoutId") or result.get("id") or "").strip() or None
        )

    if not workout_id:
        logger.warning("Workout uploadé mais ID introuvable dans la réponse: %s", result)
        return str(result)

    return workout_id


def schedule_workout_on_garmin(garmin_client: Any, workout_id: str, date: str) -> None:
    """Schedule (assign) a workout to a specific date in Garmin Connect.

    Args:
        garmin_client: A logged-in garminconnect.Garmin instance.
        workout_id: The Garmin workout ID returned by send_training_to_garmin.
        date: ISO date string "YYYY-MM-DD".

    Raises:
        GarminWorkoutSendError: on API errors.
    """
    schedule_fn = getattr(garmin_client, "schedule_workout", None)
    if not callable(schedule_fn):
        raise GarminWorkoutSendError(
            "La version de la librairie garminconnect ne supporte pas schedule_workout."
        )

    try:
        result = schedule_fn(workout_id, date)
        logger.info("Workout %s planifié le %s: %s", workout_id, date, result)
    except Exception as e:
        raise GarminWorkoutSendError(
            f"Erreur lors de la planification sur Garmin Connect: {e}"
        ) from e
