import os
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re
import time
import folium

class GarminActivityManager:
    def __init__(self, user_id):
        self.user_id = user_id
        os.makedirs("static/maps", exist_ok=True)
        os.makedirs("static/graphs", exist_ok=True)
        self.activities_file = os.path.join("data", f"{self.user_id}_activities.json")
        self.details_file = os.path.join("data", f"{self.user_id}_activity_details.json")
        os.makedirs("data", exist_ok=True)
        self.activities = self._load_data(self.activities_file)
        self.details = self._load_data(self.details_file)
        
    def _load_data(self, file_path):
        """Charge les données depuis un fichier JSON."""
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                try:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [
                            activity for activity in data
                            if isinstance(activity, dict)
                        ]
                    elif isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    logging.error(f"Fichier JSON corrompu : {file_path}")
        logging.error(f"Fichier non trouvé ou illisible : {file_path}")
        return []


    def plot_interactive_graphs(self, output_dir):
        """Crée des graphiques interactifs pour l'utilisateur spécifié et pour les activités running."""
        os.makedirs(output_dir, exist_ok=True)
        six_months_ago = datetime.now() - timedelta(days=180)

        # Filtrer les activités
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

        # Trier par date
        filtered_activities.sort(key=lambda x: datetime.fromisoformat(x["startTimeLocal"]))

        # Extraire les données
        dates = [datetime.fromisoformat(a["startTimeLocal"]) for a in filtered_activities]
        distances = [a["distance"] / 1000 for a in filtered_activities]
        durations = [a["duration"] / 60 for a in filtered_activities]
        avg_hrs = [a.get("averageHR") for a in filtered_activities if a.get("averageHR")]
        avg_paces = [(d / dist) for d, dist in zip(durations, distances)]

        # Créer un DataFrame
        data = pd.DataFrame({
            "Date": dates,
            "Distance (km)": distances,
            "Duration (min)": durations,
            "Pace (min/km)": avg_paces,
            "Average HR": avg_hrs,
        })
        data.set_index("Date", inplace=True)

        # Ajouter moyennes lissées et intervalles de confiance
        for col in ["Distance (km)", "Duration (min)", "Pace (min/km)", "Average HR"]:
            if col in data:
                data[f"{col}_MA"] = data[col].rolling(window=7, min_periods=1).mean()
                data[f"{col}_Std"] = data[col].rolling(window=7, min_periods=1).std()
                data[f"{col}_CI"] = 1.96 * (data[f"{col}_Std"] / np.sqrt(7))

        def ensure_rgba(color, alpha=0.2):
            """
            Convertit une couleur en rgba avec transparence.
            Si la couleur n'est pas au format 'rgb(x, y, z)', retourne une couleur par défaut.
            """
            if re.match(r"^rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\)$", color):
                return color.replace("rgb", "rgba").replace(")", f", {alpha})")
            else:
                return f"rgba(100, 100, 100, {alpha})"  # Gris par défaut avec transparence

        def create_plot(data, column, title, yaxis_title, color, output_file):
            if column not in data or data[column].isnull().all():
                logging.warning(f"Aucune donnée valide pour {column}.")
                return

            fig = go.Figure()

            # Données brutes
            fig.add_trace(go.Scatter(
                x=data.index,
                y=data[column],
                mode="markers",
                marker=dict(size=6, color=color),
                showlegend=False
            ))

            # Moyenne lissée
            fig.add_trace(go.Scatter(
                x=data.index,
                y=data[f"{column}_MA"],
                mode="lines",
                line=dict(color=color, width=2),
                showlegend=False
            ))

            # Intervalle de confiance avec transparence
            fill_color = ensure_rgba(color)  # Assure le format rgba
            fig.add_trace(go.Scatter(
                x=np.concatenate((data.index, data.index[::-1])),
                y=np.concatenate((data[f"{column}_MA"] + data[f"{column}_CI"], 
                                (data[f"{column}_MA"] - data[f"{column}_CI"])[::-1])),
                fill="toself",
                fillcolor=fill_color,
                line=dict(width=0),
                showlegend=False
            ))

            # Ajout des lignes verticales pour chaque semaine
            weekly_ticks = pd.date_range(start=data.index.min(), end=data.index.max(), freq='W')
            for tick in weekly_ticks:
                fig.add_vline(
                    x=tick,
                    line=dict(color="gray", width=1, dash="dot"),
                    opacity=0.5
                )

            # Configuration pour afficher une seule étiquette par mois
            fig.update_xaxes(
                tickmode="array",
                tickvals=[date for date in data.index if date.day == 1],  # Seulement les 1ers jours de chaque mois
                tickformat="%b %Y",  # Format des mois et années
                tickangle=0,
                showgrid=False  # Désactive la grille horizontale par défaut
            )

            # Configuration globale sombre
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

            # Sauvegarde du graphique
            fig.write_html(os.path.join(output_dir, output_file))

    def update_data(self):
        """Récupère les résumés d'activités depuis l'API Garmin et les sauvegarde."""
        # Simuler la récupération des activités via l'API
        logging.info("Récupération des résumés d'activités depuis l'API...")
        # Remplir cette section avec les appels à l'API Garmin
        pass

    def update_activity_details(self):
        """Récupère les détails des activités depuis l'API Garmin et les sauvegarde."""
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
                # Simuler l'appel API (remplacer par un vrai appel API)
                details = self._fetch_activity_details(activity_id)
                with open(details_file, "w") as f:
                    json.dump(details, f, indent=4)
                logging.info(f"Détails enregistrés pour l'activité ID {activity_id}.")
            except Exception as e:
                logging.error(f"Erreur lors de la récupération des détails pour l'activité ID {activity_id} : {e}")

            # Pause pour éviter de surcharger l'API
            time.sleep(5)
        for activity in self.activities.values():
            activity_id = activity.get("activityId")
            if not activity_id:
                continue
            
            details = self.details.get(activity_id)
            if details:
                metrics = details.get("activityDetailMetrics", [])
                for metric in metrics:
                    metric_name = metric.get("key")
                    data = {
                        "time": metric.get("timestamps", []),
                        metric_name: metric.get("values", [])
                    }
                    self.create_metric_graph(activity_id, metric_name, data)
                    
                    
                    
    def _fetch_activity_details(self, activity_id):
        """Simule la récupération des détails d'une activité via l'API Garmin."""
        # Remplacer cette simulation par un vrai appel API
        return {
            "activityId": activity_id,
            "path": [{"lat": 48.8566, "lon": 2.3522}, {"lat": 48.8570, "lon": 2.3530}],
            "heartRate": [120, 125, 130, 135],
            "pace": [5.2, 5.1, 5.0, 4.9],
        }
    
    def convert_activities_to_trainings(self):
        """Convertit les activités existantes au format trainings.json."""
        activities = self._load_data()  # Charge les activités
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
        """Sauvegarde les données transformées dans trainings.json."""
        file_path = os.path.join("data", "trainings.json")
        with open(file_path, "w") as f:
            json.dump(trainings, f, indent=4)
        logging.info(f"Trainings sauvegardés dans {file_path}.")

    def plot_tracking_graphs(self, output_dir):
        """Crée des graphiques de suivi pour les 6 derniers mois, semaine par semaine."""
        os.makedirs(output_dir, exist_ok=True)

        # Définir les zones de fréquence cardiaque
        fc_zones = [
            {"zone": "Zone 1: Endurance fondamentale (60-120 bpm)", "min": 60, "max": 120},
            {"zone": "Zone 2: Endurance active (120-140 bpm)", "min": 120, "max": 140},
            {"zone": "Zone 3: Seuil anaérobie (140-160 bpm)", "min": 140, "max": 160},
            {"zone": "Zone 4: Intensité maximale (>160 bpm)", "min": 160, "max": 300},
        ]

        # Préparer les résultats pour l'agrégation
        weekly_data = []

        # Filtrer les activités des 6 derniers mois
        six_months_ago = datetime.now() - timedelta(days=180)

        for summary in self.activities:  # Parcourir la liste
            activity_id = summary.get("activityId")
            start_time_local = summary.get("startTimeLocal")

            if not start_time_local or datetime.fromisoformat(start_time_local) < six_months_ago:
                continue

            details = self.details.get(activity_id)
            if not details:
                logging.warning(f"Détails introuvables pour l'activité ID {activity_id}.")
                continue

            # Identifier l'index de la fréquence cardiaque
            hr_index = None
            for descriptor in details.get("metricDescriptors", []):
                if descriptor["key"] == "directHeartRate":
                    hr_index = descriptor["metricsIndex"]
                    break

            if hr_index is None:
                logging.warning(f"Aucune donnée de fréquence cardiaque pour l'activité ID {activity_id}.")
                continue

            # Parcourir les détails pour calculer le temps dans chaque zone
            zone_durations = {zone["zone"]: 0 for zone in fc_zones}
            for metric in details.get("activityDetailMetrics", []):
                hr = metric["metrics"][hr_index]
                if hr is not None:
                    for zone in fc_zones:
                        if zone["min"] <= hr < zone["max"]:
                            zone_durations[zone["zone"]] += 1  # 1 seconde par mesure

            # Calcul de la semaine
            try:
                start_time = datetime.fromisoformat(start_time_local)
                week_start = start_time - timedelta(days=start_time.weekday())
                weekly_data.append({"week_start": week_start, **zone_durations})
                logging.info(f"Activité ID {activity_id}: ajoutée avec week_start={week_start} et zones={zone_durations}")
            except ValueError:
                logging.warning(f"Format de date invalide pour l'activité ID {activity_id}. Ignorée.")

        # Vérifier les données avant création du DataFrame
        if not weekly_data:
            logging.warning("Aucune donnée hebdomadaire disponible pour créer le graphique.")
            return

        # Créer le DataFrame
        df = pd.DataFrame(weekly_data)
        logging.info(f"Données hebdomadaires: {df.head()}")

        if df.empty:
            logging.warning("Aucune donnée à regrouper. Vérifiez les activités et les détails.")
            return

        # Agréger les données par semaine
        df = df.groupby("week_start").sum()

        # Graphique empilé
        fig = go.Figure()
        for zone in fc_zones:
            zone_name = zone["zone"]
            if zone_name in df.columns:
                fig.add_trace(go.Bar(
                    x=df.index,
                    y=df[zone_name],
                    name=zone_name
                ))

        fig.update_layout(
            barmode="stack",
            title="Temps dans les zones de fréquence cardiaque (6 derniers mois)",
            xaxis_title="Semaine",
            yaxis_title="Temps (secondes)",
            template="plotly_dark",
            legend=dict(
                title="Zones de fréquence cardiaque",
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5
            ),
            height=500,
            width=1400
        )
        fig.write_html(os.path.join(output_dir, "hr_zones_weekly_detailed.html"))
        
    def create_activity_map(self, activity_id, path):
        """
        Génère une carte interactive pour l'activité.
        """
        if not path:
            logging.warning(f"Aucun parcours pour l'activité {activity_id}.")
            return
        
        try:
            # Crée la carte centrée sur le premier point du parcours
            m = folium.Map(location=[path[0]["lat"], path[0]["lon"]], zoom_start=14)
            
            # Ajoute les points du parcours
            points = [(p["lat"], p["lon"]) for p in path]
            folium.PolyLine(points, color="blue", weight=2.5).add_to(m)
            
            # Sauvegarde la carte
            map_path = f"static/maps/activity_map_{activity_id}.html"
            m.save(map_path)
            logging.info(f"Carte sauvegardée pour l'activité {activity_id} : {map_path}")
        except Exception as e:
            logging.error(f"Erreur lors de la création de la carte pour l'activité {activity_id}: {e}")

    def create_metric_graph(self, activity_id, metric_name, data):
        """
        Génère un graphique pour une métrique donnée d'une activité.
        """
        if not data:
            logging.warning(f"Aucune donnée pour la métrique {metric_name} de l'activité {activity_id}.")
            return
        
        try:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=data["time"],
                y=data[metric_name],
                mode="lines",
                name=metric_name
            ))
            fig.update_layout(
                title=f"{metric_name} au cours du temps",
                xaxis_title="Temps",
                yaxis_title=metric_name,
                template="plotly_dark"
            )
            
            # Sauvegarde le graphique
            graph_path = f"static/graphs/{activity_id}_{metric_name}.html"
            fig.write_html(graph_path)
            logging.info(f"Graphique sauvegardé pour {metric_name} de l'activité {activity_id} : {graph_path}")
        except Exception as e:
            logging.error(f"Erreur lors de la création du graphique pour {metric_name} de l'activité {activity_id}: {e}")