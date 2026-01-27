import os
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import re
import time

from typing import Any, Optional

from .echarts import write_timeseries_chart_html


def _generate_pace_ticks(min_pace: float, max_pace: float, step_seconds: float = 15.0) -> list[float]:
    """Generate Y-axis ticks for pace graphs (every 15 seconds by default).
    
    Args:
        min_pace: Minimum pace in minutes/km
        max_pace: Maximum pace in minutes/km
        step_seconds: Step between ticks in seconds (default 15)
    
    Returns:
        List of pace values in minutes/km for ticks
    """
    if min_pace >= max_pace or max_pace <= 0:
        return []
    
    step_minutes = step_seconds / 60.0  # Convert to minutes
    start = int(min_pace * 60 / step_seconds) * step_seconds / 60.0  # Round down to nearest step
    ticks = []
    current = start
    while current <= max_pace + 0.001:  # Small epsilon for float comparison
        if current >= min_pace - 0.001:
            ticks.append(round(current, 4))  # 4 decimals to avoid float precision issues
        current += step_minutes
    
    return ticks if ticks else []


def _assign_zone_colors(hr_values: list[Optional[float]], zones: list[dict]) -> list[Optional[str]]:
    """Assign HR zone colors to data points.
    
    Args:
        hr_values: List of HR values (nullable)
        zones: List of zone dicts with 'min', 'max' keys
    
    Returns:
        List of color strings (or None) corresponding to each HR value
    """
    if not zones or len(zones) < 5:
        return [None] * len(hr_values)
    
    zone_colors = [
        "rgba(76, 201, 240, 0.9)",      # Z1 - light blue
        "rgba(72, 219, 251, 0.9)",      # Z2 - lighter blue
        "rgba(255, 223, 0, 0.9)",       # Z3 - yellow
        "rgba(255, 140, 0, 0.9)",       # Z4 - orange
        "rgba(255, 77, 141, 0.9)",      # Z5 - red/pink
    ]
    
    colors = []
    for hr in hr_values:
        if hr is None:
            colors.append(None)
        else:
            hr_val = float(hr)
            # Find which zone this HR belongs to
            for z_idx, zone in enumerate(zones[:5]):
                if hr_val <= zone.get("max", float('inf')):
                    colors.append(zone_colors[z_idx])
                    break
            else:
                # Beyond Z5
                colors.append(zone_colors[4])
    
    return colors


def _graph_cache_path(output_dir: str) -> str:
    return os.path.join(output_dir, ".graph_cache.json")


def _read_graph_cache(output_dir: str) -> dict[str, Any] | None:
    path = _graph_cache_path(output_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_graph_cache(output_dir: str, payload: dict[str, Any]) -> None:
    path = _graph_cache_path(output_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception:
        # Best-effort only.
        pass


def _has_any_html(output_dir: str) -> bool:
    try:
        return any(name.endswith(".html") for name in os.listdir(output_dir))
    except Exception:
        return False


def _clean_html(output_dir: str) -> None:
    try:
        for name in os.listdir(output_dir):
            if name.endswith(".html"):
                try:
                    os.remove(os.path.join(output_dir, name))
                except OSError:
                    pass
    except Exception:
        pass


def _source_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


def _echarts_mtime() -> float:
    try:
        path = os.path.join(os.path.dirname(__file__), "echarts.py")
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


class GarminActivityManager:
    def __init__(self, user_id, *, activities=None, hr_zones=None):
        self.user_id = user_id
        self.activities_file = os.path.join("data", f"{self.user_id}_activities.json")
        os.makedirs("data", exist_ok=True)
        self.activities = activities if isinstance(activities, list) else self._load_data()
        self.hr_zones = hr_zones  # Optional: list of dicts with 'min', 'max'

    def _load_data(self):
        """Charge uniquement les activités pour l'utilisateur spécifié."""
        if os.path.exists(self.activities_file):
            with open(self.activities_file, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    logging.info(f"Données chargées depuis {self.activities_file}.")
                    return data
                except json.JSONDecodeError:
                    logging.warning(f"Fichier corrompu : {self.activities_file}.")
        else:
            logging.error(f"Fichier non trouvé : {self.activities_file}.")
        return []

    def plot_interactive_graphs(self, output_dir):
        """Crée des graphiques interactifs (ECharts) pour les activités running."""
        os.makedirs(output_dir, exist_ok=True)

        src_mtime = _source_mtime(self.activities_file)
        echarts_mtime = _echarts_mtime()
        cache = _read_graph_cache(output_dir)
        if (
            cache
            and cache.get("source") == self.activities_file
            and float(cache.get("source_mtime") or 0.0) == src_mtime
            and float(cache.get("echarts_mtime") or 0.0) == echarts_mtime
            and _has_any_html(output_dir)
        ):
            return

        _clean_html(output_dir)

        six_months_ago = datetime.now() - timedelta(days=1460)

        filtered_activities = [
            a for a in self.activities
            if a.get("activityType", {}).get("typeKey") == "running"
            and datetime.fromisoformat(a["startTimeLocal"]) >= six_months_ago
            and a.get("distance", 0) > 0
            and a.get("duration", 0) > 0
        ]

        if not filtered_activities:
            logging.warning("Aucune activité valide pour tracer les graphiques.")
            return

        filtered_activities.sort(key=lambda x: datetime.fromisoformat(x["startTimeLocal"]))

        dates = [datetime.fromisoformat(a["startTimeLocal"]) for a in filtered_activities]
        distances = [a["distance"] / 1000 for a in filtered_activities]
        durations = [a["duration"] / 60 for a in filtered_activities]
        avg_hrs = [a.get("averageHR") for a in filtered_activities]
        avg_paces = [(d / dist) for d, dist in zip(durations, distances)]

        data = pd.DataFrame({
            "Date": dates,
            "Distance (km)": distances,
            "Duration (min)": durations,
            "Pace (min/km)": avg_paces,
            "Average HR": avg_hrs,
        })
        data.set_index("Date", inplace=True)

        for col in ["Distance (km)", "Duration (min)", "Pace (min/km)", "Average HR"]:
            if col in data:
                data[f"{col}_MA"] = data[col].rolling(window=7, min_periods=1).mean()
                data[f"{col}_Std"] = data[col].rolling(window=7, min_periods=1).std()
                data[f"{col}_CI"] = 1.96 * (data[f"{col}_Std"] / np.sqrt(7))

        def create_plot(data, column, title, yaxis_title, color, output_file):
            if column not in data or data[column].isnull().all():
                logging.warning(f"Aucune donnée valide pour {column}.")
                return

            x = [d.date().isoformat() for d in data.index]
            y = [None if pd.isna(v) else float(v) for v in data[column].tolist()]
            y_ma = [None if pd.isna(v) else float(v) for v in data[f"{column}_MA"].tolist()]
            y_ci = [None if pd.isna(v) else float(v) for v in data[f"{column}_CI"].tolist()]

            # Generate pace ticks and set fixed limits for pace graphs
            y_ticks = None
            is_pace_graph = False
            y_axis_min_override = None
            y_axis_max_override = None
            
            if "Pace" in column or "pace" in column.lower():
                is_pace_graph = True
                y_axis_min_override = 3.0  # 3:00/km
                y_axis_max_override = 7.0  # 7:00/km
                y_ticks = _generate_pace_ticks(3.0, 7.0)

            # Color HR points by zone
            y_series_colors = None
            if "HR" in column or "averageHR" in column or "Average HR" in column:
                if self.hr_zones and len(self.hr_zones) >= 5:
                    y_series_colors = _assign_zone_colors(y, self.hr_zones)
                    y_axis_max_override = self.hr_zones[4].get("max", 200)  # Z5 max = FC max

            write_timeseries_chart_html(
                os.path.join(output_dir, output_file),
                title=title,
                x=x,
                y=y,
                y_label=yaxis_title,
                color=color,
                y_ma=y_ma,
                y_ci=y_ci,
                y_ticks=y_ticks,
                y_series_colors=y_series_colors,
                is_pace_graph=is_pace_graph,
                y_axis_min_override=y_axis_min_override,
                y_axis_max_override=y_axis_max_override,
            )

        # Use the app theme accent color
        create_plot(data, "Distance (km)", "Distance", "Distance (km)", "#4CC9F0", "distance.html")
        create_plot(data, "Duration (min)", "Durée", "Durée (min)", "#4CC9F0", "duration.html")
        create_plot(data, "Pace (min/km)", "Allure", "Allure (min/km)", "#4CC9F0", "pace.html")
        create_plot(data, "Average HR", "Fréquence cardiaque moyenne", "BPM", "#4CC9F0", "average_hr.html")

        _write_graph_cache(
            output_dir,
            {
                "engine": "echarts",
                "source": self.activities_file,
                "source_mtime": src_mtime,
                "echarts_mtime": echarts_mtime,
            },
        )

    def plot_interactive_graphs_by_type(self, output_dir: str) -> None:
        """Crée des graphiques interactifs (ECharts) par sport.

        Objectif: proposer des graphes pertinents pour chaque sport, tout en restant
        robuste face aux champs manquants (ex: averageHR absent).
        """

        os.makedirs(output_dir, exist_ok=True)

        src_mtime = _source_mtime(self.activities_file)
        echarts_mtime = _echarts_mtime()
        cache = _read_graph_cache(output_dir)
        if (
            cache
            and cache.get("source") == self.activities_file
            and float(cache.get("source_mtime") or 0.0) == src_mtime
            and float(cache.get("echarts_mtime") or 0.0) == echarts_mtime
            and _has_any_html(output_dir)
        ):
            return

        _clean_html(output_dir)

        six_months_ago = datetime.now() - timedelta(days=1460)

        def activity_type(a: dict) -> str:
            return (a.get("activityType") or {}).get("typeKey") or "other"

        def parse_date(a: dict):
            s = a.get("startTimeLocal")
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        allowed_types = {"swimming", "cycling", "running", "strength_training"}

        def metrics_for_type(type_key: str) -> list[tuple[str, str, str]]:
            """Return list of (metric_key, title, y_label)."""
            if type_key == "running":
                return [
                    ("distance_km", "Distance", "Distance (km)"),
                    ("duration_min", "Durée", "Durée (min)"),
                    ("pace_min_km", "Allure", "Allure (min/km)"),
                    ("avg_hr", "Fréquence cardiaque moyenne", "BPM"),
                ]
            if type_key in {"cycling"}:
                return [
                    ("distance_km", "Distance", "Distance (km)"),
                    ("duration_min", "Durée", "Durée (min)"),
                    ("avg_hr", "Fréquence cardiaque moyenne", "BPM"),
                ]
            if type_key in {"swimming"}:
                return [
                    ("distance_km", "Distance", "Distance (km)"),
                    ("duration_min", "Durée", "Durée (min)"),
                    ("pace_min_100m", "Allure moyenne", "Allure (min/100m)"),
                    ("avg_swolf", "SWOLF moyen (50m)", "SWOLF (50m)"),
                    ("swim_cadence_spm", "Mouvements par minute", "Coups/min"),
                    ("strokes_per_length", "Mouvements par 50m", "Coups/50m"),
                ]
            if type_key in {"strength_training"}:
                return [
                    ("duration_min", "Durée", "Durée (min)"),
                    ("avg_hr", "Fréquence cardiaque moyenne", "BPM"),
                ]
            return []

        def canonical_type(type_key: str) -> str | None:
            if not type_key:
                return None
            k = str(type_key)
            if k in {"running", "treadmill_running", "trail_running", "track_running", "virtual_running", "indoor_running"}:
                return "running"
            if k in {
                "cycling",
                "road_biking",
                "mountain_biking",
                "gravel_cycling",
                "indoor_cycling",
                "virtual_cycling",
                "e_bike_fitness",
                "e_bike_mountain",
            }:
                return "cycling"
            if k in {"swimming", "lap_swimming", "pool_swimming", "open_water_swimming"}:
                return "swimming"
            if k in {"strength_training"}:
                return "strength_training"
            return None

        def pool_length_m(a: dict) -> float | None:
            """Return pool length in meters if present.

            Garmin summary often contains:
              - poolLength: e.g. 2500.0
              - unitOfPoolLength: { unitKey: 'meter', factor: 100.0 }
            In that case poolLength / factor = 25m.
            """

            raw = a.get("poolLength")
            if raw is None:
                return None
            try:
                v = float(raw)
            except Exception:
                return None
            if v <= 0:
                return None

            unit = a.get("unitOfPoolLength")
            if isinstance(unit, dict):
                factor = unit.get("factor")
                try:
                    f = float(factor) if factor is not None else None
                except Exception:
                    f = None
                if f and f > 0:
                    v = v / f

            # Defensive: ignore absurd values
            if v <= 0 or v > 200:
                return None
            return v

        # Group activities by type
        groups: dict[str, list[dict]] = {}
        for a in self.activities:
            dt = parse_date(a)
            if not dt or dt < six_months_ago:
                continue
            canon = canonical_type(activity_type(a))
            if canon not in allowed_types:
                continue
            # Keep only activities with a minimum of structure
            if (a.get("duration") or 0) <= 0 and (a.get("distance") or 0) <= 0:
                continue
            groups.setdefault(canon, []).append(a)

        if not groups:
            return

        def create_plot(
            df: pd.DataFrame,
            column: str,
            title: str,
            yaxis_title: str,
            output_file: str,
            *,
            color: str = "#4CC9F0",
        ) -> None:
            if column not in df or df[column].isnull().all():
                return

            x = [d.date().isoformat() for d in df.index]
            y = [None if pd.isna(v) else float(v) for v in df[column].tolist()]

            y_ma = None
            y_ci = None
            ma_col = f"{column}_MA"
            ci_col = f"{column}_CI"
            if ma_col in df:
                y_ma = [None if pd.isna(v) else float(v) for v in df[ma_col].tolist()]
            if ci_col in df:
                y_ci = [None if pd.isna(v) else float(v) for v in df[ci_col].tolist()]

            # Prepare Y-axis overrides and colors based on metric type
            y_axis_min_override = None
            y_axis_max_override = None
            y_ticks = None
            is_pace_graph = False
            y_series_colors = None

            if column == "avg_hr" and self.hr_zones and len(self.hr_zones) >= 5:
                # For HR graphs: max is FC max, color points by zone
                y_axis_max_override = self.hr_zones[4].get("max", 200)  # Z5 max = FC max
                y_series_colors = _assign_zone_colors(y, self.hr_zones)
            elif column == "pace_min_km":
                # For running pace: fixed 3:00-7:00/km
                is_pace_graph = True
                y_axis_min_override = 3.0
                y_axis_max_override = 7.0
                y_ticks = _generate_pace_ticks(3.0, 7.0)
            elif column == "pace_min_100m":
                # For swimming pace: fixed 1:00-3:00/100m
                is_pace_graph = True
                y_axis_min_override = 1.0
                y_axis_max_override = 3.0
                y_ticks = _generate_pace_ticks(1.0, 3.0)
            elif "cadence" in column.lower() or "spm" in column.lower():
                # For cadence: 0-200
                y_axis_min_override = 0.0
                y_axis_max_override = 200.0

            write_timeseries_chart_html(
                os.path.join(output_dir, output_file),
                title=title,
                x=x,
                y=y,
                y_label=yaxis_title,
                color=color,
                y_ma=y_ma,
                y_ci=y_ci,
                y_ticks=y_ticks,
                y_series_colors=y_series_colors,
                is_pace_graph=is_pace_graph,
                y_axis_min_override=y_axis_min_override,
                y_axis_max_override=y_axis_max_override,
            )

        # Generate graphs for each type
        for type_key, items in groups.items():
            items.sort(key=lambda x: parse_date(x) or datetime.min)

            rows = []
            for a in items:
                dt = parse_date(a)
                if not dt:
                    continue

                swim_pool_m = pool_length_m(a) if type_key == "swimming" else None
                norm_factor = (50.0 / swim_pool_m) if (swim_pool_m and swim_pool_m > 0) else 1.0

                distance_km = (a.get("distance") or 0) / 1000
                duration_min = (a.get("duration") or 0) / 60

                pace_min_km = None
                if distance_km and duration_min and distance_km > 0:
                    pace_min_km = duration_min / distance_km

                pace_min_100m = None
                if distance_km and duration_min and distance_km > 0:
                    pace_min_100m = duration_min / (distance_km * 10.0)

                avg_swolf = a.get("averageSwolf")
                swim_cadence_spm = a.get("averageSwimCadenceInStrokesPerMinute")
                strokes_per_length = a.get("avgStrokes")

                rows.append(
                    {
                        "Date": dt,
                        "distance_km": distance_km if distance_km > 0 else None,
                        "duration_min": duration_min if duration_min > 0 else None,
                        "pace_min_km": pace_min_km,
                        "pace_min_100m": pace_min_100m,
                        "avg_hr": a.get("averageHR"),
                        # Normalize swim metrics to a 50m pool when pool length is known.
                        "avg_swolf": (float(avg_swolf) * norm_factor) if avg_swolf is not None else None,
                        "swim_cadence_spm": swim_cadence_spm,
                        "strokes_per_length": (float(strokes_per_length) * norm_factor) if strokes_per_length is not None else None,
                    }
                )

            if len(rows) < 1:
                continue

            df = pd.DataFrame(rows)
            df.set_index("Date", inplace=True)

            # rolling bands
            for col in [
                "distance_km",
                "duration_min",
                "pace_min_km",
                "pace_min_100m",
                "avg_hr",
                "avg_swolf",
                "swim_cadence_spm",
                "strokes_per_length",
            ]:
                if col in df and not df[col].isnull().all():
                    df[f"{col}_MA"] = df[col].rolling(window=7, min_periods=1).mean()
                    df[f"{col}_Std"] = df[col].rolling(window=7, min_periods=1).std()
                    df[f"{col}_CI"] = 1.96 * (df[f"{col}_Std"] / np.sqrt(7))

            for metric_key, title, y_label in metrics_for_type(type_key):
                # Skip pace for non-distance sports / missing values
                out_name = f"{type_key}__{metric_key}.html"
                metric_color = "#4CC9F0"
                if metric_key in {"avg_swolf", "swim_cadence_spm", "strokes_per_length"}:
                    metric_color = "#FF4D8D"
                create_plot(
                    df,
                    metric_key,
                    f"{type_key.replace('_', ' ').title()} — {title}",
                    y_label,
                    out_name,
                    color=metric_color,
                )

        _write_graph_cache(
            output_dir,
            {
                "engine": "echarts",
                "source": self.activities_file,
                "source_mtime": src_mtime,
            },
        )

    def update_data(self):
        logging.info("Récupération des résumés d'activités depuis l'API...")
        pass

    def update_activity_details(self):
        if not self.activities:
            logging.warning("Aucune activité disponible. Veuillez d'abord récupérer les résumés.")
            return

        for activity in self.activities:
            activity_id = activity.get("activityId")
            if not activity_id:
                logging.warning(f"Aucune ID trouvée pour l'activité : {activity}")
                continue

            details_file = os.path.join("data", f"activity_{activity_id}_details.json")
            if os.path.exists(details_file):
                logging.info(f"Détails déjà récupérés pour l'activité ID {activity_id}.")
                continue

            logging.info(f"Récupération des détails pour l'activité ID {activity_id}...")
            try:
                details = self._fetch_activity_details(activity_id)
                with open(details_file, "w", encoding="utf-8") as f:
                    json.dump(details, f, indent=4)
                logging.info(f"Détails enregistrés pour l'activité ID {activity_id}.")
            except Exception as e:
                logging.error(f"Erreur lors de la récupération des détails pour l'activité ID {activity_id} : {e}")

            time.sleep(5)

    def _fetch_activity_details(self, activity_id):
        return {
            "activityId": activity_id,
            "path": [{"lat": 48.8566, "lon": 2.3522}, {"lat": 48.8570, "lon": 2.3530}],
            "heartRate": [120, 125, 130, 135],
            "pace": [5.2, 5.1, 5.0, 4.9],
        }

    def convert_activities_to_trainings(self):
        activities = self._load_data()
        trainings = []
        for activity in activities:
            training = {
                "name": activity.get("activityName", "Activité non nommée"),
                "date": activity.get("startTimeLocal", "Date inconnue").split(" ")[0],
                "distance": round(activity.get("distance", 0) / 1000, 2),
                "description": activity.get("description", "Aucune description")
            }
            trainings.append(training)
        return trainings

    def save_to_trainings_file(self, trainings):
        file_path = os.path.join("data", "trainings.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(trainings, f, indent=4)
        logging.info(f"Trainings sauvegardés dans {file_path}.")
