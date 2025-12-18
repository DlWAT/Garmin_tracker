# Let's write a corrected and more robust version of `garmin_activity_manager.py`
# matching the paths and filenames your Flask app is trying to load in the logs.
from pathlib import Path
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re
import time
import folium


def _safe_json_read(path: Path, default):
    try:
        if not path.exists():
            logging.error(f"Fichier non trouvé ou illisible : {path}")
            return default
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Lecture JSON échouée ({path}) : {e}")
        return default


def _safe_json_write(path: Path, payload):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Écriture JSON échouée ({path}) : {e}")
        return False


class GarminActivityManager:
    """
    Un gestionnaire robuste des activités Garmin qui :
    - utilise des chemins absolus basés sur ce fichier (pas de surprises de CWD),
    - gère proprement JSON liste ou dict,
    - écrit exactement les fichiers HTML attendus par vos routes Flask, d'après vos logs :
        /static/activity/{distance|pace|duration|average_hr}.html
        /static/tracking/{weekly_volume|hr_zones_weekly}.html
    """
    def __init__(self, user_id: str):
        self.user_id = user_id

        # Dossiers (toujours en absolu, basés sur ce fichier)
        self.BASE_DIR = Path(__file__).resolve().parent
        self.DATA_DIR = (self.BASE_DIR / "data")
        self.STATIC_DIR = (self.BASE_DIR / "static")
        self.STATIC_ACTIVITY = self.STATIC_DIR / "activity"
        self.STATIC_TRACKING = self.STATIC_DIR / "tracking"
        self.STATIC_MAPS = self.STATIC_DIR / "maps"
        self.STATIC_GRAPHS = self.STATIC_DIR / "graphs"

        for d in [self.DATA_DIR, self.STATIC_DIR, self.STATIC_ACTIVITY, self.STATIC_TRACKING, self.STATIC_MAPS, self.STATIC_GRAPHS]:
            d.mkdir(parents=True, exist_ok=True)

        # Fichiers de données
        self.activities_file = self.DATA_DIR / f"{self.user_id}_activities.json"
        self.details_file = self.DATA_DIR / f"{self.user_id}_activity_details.json"

        # Chargement initial
        self.activities = self._load_activities()
        self.details = self._load_details()

    # ---------- Chargement & helpers ----------
    def _load_activities(self):
        data = _safe_json_read(self.activities_file, [])
        if isinstance(data, list):
            # Filtrer proprement
            return [a for a in data if isinstance(a, dict)]
        elif isinstance(data, dict):
            # Certains dumps peuvent être dict {"activities":[...]} → harmoniser
            if "activities" in data and isinstance(data["activities"], list):
                return [a for a in data["activities"] if isinstance(a, dict)]
            # Dernier recours : convertir dict→liste de valeurs
            return [v for v in data.values() if isinstance(v, dict)]
        return []

    def _load_details(self):
        data = _safe_json_read(self.details_file, {})
        if isinstance(data, dict):
            return data  # format attendu : { "<activityId>": {...} }
        if isinstance(data, list):
            # Tenter de mapper id→details si présent
            out = {}
            for d in data:
                if isinstance(d, dict) and "activityId" in d:
                    out[str(d["activityId"])] = d
            return out
        return {}

    def refresh_from_disk(self):
        """Recharger les données depuis le disque (utile après une sync)."""
        self.activities = self._load_activities()
        self.details = self._load_details()

    def _get_details_for(self, activity_id):
        """Récupère un bloc de détails, quel que soit le format du fichier d'origine."""
        if activity_id is None:
            return None
        return self.details.get(str(activity_id)) or self.details.get(activity_id)

    # ---------- Graphiques Activité (pages /activity) ----------
    def plot_interactive_graphs(self, output_dir=None):
        """
        Crée 4 graphiques interactifs (distance, durée, allure, FC moyenne) pour les 6 derniers mois
        et les enregistre sous :
            static/activity/distance.html
            static/activity/duration.html
            static/activity/pace.html
            static/activity/average_hr.html
        """
        if output_dir is None:
            out_dir = self.STATIC_ACTIVITY
        else:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

        six_months_ago = datetime.now() - timedelta(days=180)

        # Filtrer les activités running valides
        filtered = []
        for a in self.activities:
            try:
                is_run = (a.get("activityType", {}) or {}).get("typeKey") == "running"
                start_str = a.get("startTimeLocal")
                if not is_run or not start_str:
                    continue
                start_dt = datetime.fromisoformat(start_str)
                if start_dt < six_months_ago:
                    continue
                dist_m = a.get("distance", 0) or 0
                dur_s = a.get("duration", 0) or 0
                if dist_m <= 0 or dur_s <= 0:
                    continue
                filtered.append(a)
            except Exception:
                continue

        if not filtered:
            logging.warning("Aucune activité valide pour tracer les graphiques.")
            return

        filtered.sort(key=lambda x: datetime.fromisoformat(x["startTimeLocal"]))

        dates = [datetime.fromisoformat(a["startTimeLocal"]) for a in filtered]
        distances_km = [(a.get("distance", 0) or 0) / 1000 for a in filtered]
        durations_min = [(a.get("duration", 0) or 0) / 60 for a in filtered]
        # IMPORTANT : garder la même longueur que les autres colonnes
        avg_hrs = [a.get("averageHR", None) for a in filtered]

        # Allure en min/km
        avg_paces = []
        for dist_km, dur_min in zip(distances_km, durations_min):
            if dist_km > 0:
                avg_paces.append(dur_min / dist_km)
            else:
                avg_paces.append(None)

        df = pd.DataFrame({
            "Date": dates,
            "Distance (km)": distances_km,
            "Duration (min)": durations_min,
            "Pace (min/km)": avg_paces,
            "Average HR": avg_hrs,
        }).set_index("Date")

        # Ajout des moyennes mobiles et CI
        for col in ["Distance (km)", "Duration (min)", "Pace (min/km)", "Average HR"]:
            if col in df.columns:
                df[f"{col}_MA"] = df[col].rolling(window=7, min_periods=1).mean()
                df[f"{col}_Std"] = df[col].rolling(window=7, min_periods=1).std()
                df[f"{col}_CI"] = 1.96 * (df[f"{col}_Std"] / np.sqrt(7))

        def ensure_rgba(color, alpha=0.2):
            if isinstance(color, str) and re.match(r"^rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\)$", color):
                return color.replace("rgb", "rgba").replace(")", f", {alpha})")
            return f"rgba(100, 100, 100, {alpha})"

        def create_plot(dataframe, column, title, yaxis_title, color, filename):
            if column not in dataframe or dataframe[column].isnull().all():
                logging.warning(f"Aucune donnée valide pour {column}.")
                return

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=dataframe.index, y=dataframe[column], mode="markers",
                                     marker=dict(size=6, color=color), showlegend=False))
            if f"{column}_MA" in dataframe:
                fig.add_trace(go.Scatter(x=dataframe.index, y=dataframe[f"{column}_MA"], mode="lines",
                                         line=dict(color=color, width=2), showlegend=False))
            if f"{column}_CI" in dataframe and f"{column}_MA" in dataframe:
                fill_color = ensure_rgba(color)
                upper = (dataframe[f"{column}_MA"] + dataframe[f"{column}_CI"]).to_numpy()
                lower = (dataframe[f"{column}_MA"] - dataframe[f"{column}_CI"]).to_numpy()
                x_vals = dataframe.index.to_numpy()
                fig.add_trace(go.Scatter(
                    x=np.concatenate((x_vals, x_vals[::-1])),
                    y=np.concatenate((upper, lower[::-1])),
                    fill="toself", fillcolor=fill_color, line=dict(width=0), showlegend=False
                ))

            weekly_ticks = pd.date_range(start=dataframe.index.min(), end=dataframe.index.max(), freq='W')
            for tick in weekly_ticks:
                fig.add_vline(x=tick, line=dict(color="gray", width=1, dash="dot"), opacity=0.5)

            fig.update_xaxes(
                tickmode="array",
                tickvals=[d for d in dataframe.index if getattr(d, "day", 0) == 1],
                tickformat="%b %Y",
                tickangle=0,
                showgrid=False
            )
            fig.update_layout(
                title=title,
                xaxis_title="Date",
                yaxis_title=yaxis_title,
                template="plotly_dark",
                plot_bgcolor="black",
                paper_bgcolor="black",
                font=dict(color="white"),
                height=450,
                width=650
            )
            fig.write_html(str((out_dir / filename).resolve()))

        # Génération des 4 graphiques attendus
        create_plot(df, "Distance (km)", "Distance par activité", "Kilomètres", "rgb(0, 153, 255)", "distance.html")
        create_plot(df, "Duration (min)", "Durée par activité", "Minutes", "rgb(255, 153, 0)", "duration.html")
        create_plot(df, "Pace (min/km)", "Allure moyenne", "min/km", "rgb(0, 204, 102)", "pace.html")
        create_plot(df, "Average HR", "Fréquence cardiaque moyenne", "bpm", "rgb(255, 80, 80)", "average_hr.html")

    # ---------- Mise à jour des données ----------
    def update_data(self):
        """
        Cette méthode est un placeholder : elle pourrait déclencher
        une synchronisation via un autre module (ex: GarminClientHandler) puis recharger depuis disque.
        """
        logging.info("update_data() – à implémenter selon votre pipeline de sync.")
        # Exemple si un autre module a écrit self.activities_file / self.details_file :
        # self.refresh_from_disk()

    def update_activity_details(self):
        """
        Exemple de boucle pour générer des cartes + graphes de métriques si les détails sont disponibles.
        On suppose que `self.details` est un dict {activityId: details}.
        """
        if not self.activities:
            logging.warning("Aucune activité disponible. Veuillez d'abord récupérer les résumés.")
            return

        for activity in self.activities:
            activity_id = activity.get("activityId")
            if not activity_id:
                logging.warning(f"Aucune ID trouvée pour l'activité : {activity}")
                continue

            details = self._get_details_for(activity_id)
            if not details:
                logging.info(f"Détails introuvables pour activité {activity_id}.")
                continue

            # Carte si un trajet est dispo
            path = details.get("path") or details.get("polyline") or []
            if path:
                self.create_activity_map(activity_id, path)

            # Parcours des métriques type activityDetailMetrics (format Garmin)
            metrics = details.get("activityDetailMetrics", [])
            # Si le format "garminconnect" est utilisé avec descriptors+metrics :
            descriptors = {d.get("key"): d for d in details.get("metricDescriptors", []) if isinstance(d, dict)}
            if metrics and descriptors:
                # Construire une timeline "time" générique s'il y en a une
                time_list = None
                if "duration" in descriptors:
                    idx = descriptors["duration"].get("metricsIndex")
                    if idx is not None and 0 <= idx < len(metrics):
                        time_list = metrics[idx]

                for key, desc in descriptors.items():
                    idx = desc.get("metricsIndex")
                    if idx is None or not (0 <= idx < len(metrics)):
                        continue
                    series = metrics[idx]
                    if not isinstance(series, list) or not series:
                        continue
                    data = {"time": list(range(len(series))) if time_list is None else time_list, key: series}
                    self.create_metric_graph(activity_id, key, data)

            # Eviter de surcharger si on appelle vraiment une API ailleurs
            time.sleep(0.05)

    # ---------- Conversions / exports ----------
    def convert_activities_to_trainings(self):
        """
        Convertit `self.activities` en format simplifié pour trainings.json.
        """
        trainings = []
        for activity in self.activities:
            start = activity.get("startTimeLocal", "Date inconnue")
            date_only = start.split(" ")[0] if isinstance(start, str) else "Date inconnue"
            training = {
                "name": activity.get("activityName", "Activité non nommée"),
                "date": date_only,
                "distance": round((activity.get("distance", 0) or 0) / 1000, 2),
                "description": activity.get("description", "Aucune description")
            }
            trainings.append(training)
        return trainings

    def save_to_trainings_file(self, trainings):
        file_path = self.DATA_DIR / "trainings.json"
        if _safe_json_write(file_path, trainings):
            logging.info(f"Trainings sauvegardés dans {file_path}.")

    # ---------- Graphiques /tracking ----------
    def plot_tracking_graphs(self, output_dir=None):
        """
        Crée 2 fichiers sous static/tracking/ :
           - weekly_volume.html : volume hebdo (distance km et durée min)
           - hr_zones_weekly.html : cumul hebdo du temps passé dans des zones FC
        """
        if output_dir is None:
            out_dir = self.STATIC_TRACKING
        else:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)

        six_months_ago = datetime.now() - timedelta(days=180)

        # ---- 1) Volume hebdomadaire (distance & durée) ----
        week_rows = []
        for a in self.activities:
            try:
                start_str = a.get("startTimeLocal")
                if not start_str:
                    continue
                start = datetime.fromisoformat(start_str)
                if start < six_months_ago:
                    continue
                week_start = start - timedelta(days=start.weekday())
                dist_km = (a.get("distance", 0) or 0) / 1000
                dur_min = (a.get("duration", 0) or 0) / 60
                week_rows.append({"week_start": week_start, "distance_km": dist_km, "duration_min": dur_min})
            except Exception:
                continue

        weekly_df = pd.DataFrame(week_rows)
        if not weekly_df.empty:
            weekly_df = weekly_df.groupby("week_start").sum().sort_index()

            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(name="Distance (km)", x=weekly_df.index, y=weekly_df["distance_km"]))
            fig_vol.add_trace(go.Bar(name="Durée (min)", x=weekly_df.index, y=weekly_df["duration_min"]))
            fig_vol.update_layout(
                barmode="group",
                title="Volume hebdomadaire (6 derniers mois)",
                xaxis_title="Semaine",
                yaxis_title="Valeur",
                template="plotly_dark",
                height=500,
                width=1000,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
            )
            fig_vol.write_html(str((out_dir / "weekly_volume.html").resolve()))
        else:
            logging.warning("Aucune donnée hebdomadaire disponible pour le volume.")

        # ---- 2) Temps dans les zones de FC (hebdo) ----
        fc_zones = [
            {"name": "Zone 1: Endurance fondamentale (60-120 bpm)", "min": 60, "max": 120},
            {"name": "Zone 2: Endurance active (120-140 bpm)", "min": 120, "max": 140},
            {"name": "Zone 3: Seuil anaérobie (140-160 bpm)", "min": 140, "max": 160},
            {"name": "Zone 4: Intensité maximale (>160 bpm)", "min": 160, "max": 300},
        ]

        weekly_zone_rows = []
        for a in self.activities:
            activity_id = a.get("activityId")
            start_str = a.get("startTimeLocal")
            if not activity_id or not start_str:
                continue
            try:
                start = datetime.fromisoformat(start_str)
            except Exception:
                continue
            if start < six_months_ago:
                continue

            details = self._get_details_for(activity_id)
            if not details:
                logging.info(f"Détails introuvables pour l'activité ID {activity_id}.")
                continue

            # Chercher une série HR utilisable
            hr_series = None

            # Format "metricDescriptors"/"activityDetailMetrics"
            descriptors = {d.get("key"): d for d in details.get("metricDescriptors", []) if isinstance(d, dict)}
            metrics = details.get("activityDetailMetrics", [])
            if descriptors and metrics:
                idx = None
                # Plusieurs clefs possibles selon export : directHeartRate ou heartRate
                for key in ("directHeartRate", "heartRate"):
                    if key in descriptors:
                        idx = descriptors[key].get("metricsIndex")
                        break
                if idx is not None and 0 <= idx < len(metrics):
                    hr_series = metrics[idx]

            # Fallback : certaines structures stockent directement une liste HR
            if hr_series is None and isinstance(details.get("heartRate"), list):
                hr_series = details["heartRate"]

            if not isinstance(hr_series, list) or not hr_series:
                logging.warning(f"Aucune donnée de fréquence cardiaque pour l'activité ID {activity_id}.")
                continue

            zone_durations = {z["name"]: 0 for z in fc_zones}
            for hr in hr_series:
                if hr is None:
                    continue
                for z in fc_zones:
                    if z["min"] <= hr < z["max"]:
                        zone_durations[z["name"]] += 1  # approx 1s par échantillon

            week_start = start - timedelta(days=start.weekday())
            weekly_zone_rows.append({"week_start": week_start, **zone_durations})

        if not weekly_zone_rows:
            logging.warning("Aucune donnée hebdomadaire disponible pour créer le graphique des zones FC.")
            return

        zdf = pd.DataFrame(weekly_zone_rows).groupby("week_start").sum().sort_index()

        fig_zones = go.Figure()
        for z in fc_zones:
            name = z["name"]
            if name in zdf.columns:
                fig_zones.add_trace(go.Bar(x=zdf.index, y=zdf[name], name=name))

        fig_zones.update_layout(
            barmode="stack",
            title="Temps hebdomadaire dans les zones de fréquence cardiaque (6 derniers mois)",
            xaxis_title="Semaine",
            yaxis_title="Temps (secondes)",
            template="plotly_dark",
            height=500,
            width=1000,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        )
        fig_zones.write_html(str((out_dir / "hr_zones_weekly.html").resolve()))

    # ---------- Cartes & graphes par activité ----------
    def create_activity_map(self, activity_id, path):
        if not path:
            logging.warning(f"Aucun parcours pour l'activité {activity_id}.")
            return
        try:
            m = folium.Map(location=[path[0]["lat"], path[0]["lon"]], zoom_start=14)
            points = [(p["lat"], p["lon"]) for p in path if "lat" in p and "lon" in p]
            if points:
                folium.PolyLine(points, weight=2.5).add_to(m)
            map_path = (self.STATIC_MAPS / f"activity_map_{activity_id}.html")
            m.save(str(map_path))
            logging.info(f"Carte sauvegardée pour l'activité {activity_id} : {map_path}")
        except Exception as e:
            logging.error(f"Erreur lors de la création de la carte pour l'activité {activity_id}: {e}")

    def create_metric_graph(self, activity_id, metric_name, data):
        if not data:
            logging.warning(f"Aucune donnée pour la métrique {metric_name} de l'activité {activity_id}.")
            return
        try:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=data.get("time"), y=data.get(metric_name), mode="lines", name=metric_name))
            fig.update_layout(title=f"{metric_name} au cours du temps",
                              xaxis_title="Temps", yaxis_title=metric_name, template="plotly_dark")
            graph_path = (self.STATIC_GRAPHS / f"{activity_id}_{metric_name}.html")
            fig.write_html(str(graph_path))
            logging.info(f"Graphique sauvegardé pour {metric_name} de l'activité {activity_id} : {graph_path}")
        except Exception as e:
            logging.error(f"Erreur lors de la création du graphique pour {metric_name} de l'activité {activity_id}: {e}")
