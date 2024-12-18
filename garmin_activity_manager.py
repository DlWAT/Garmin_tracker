import os
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import re
import time

class GarminActivityManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.activities_file = os.path.join("data", f"{self.user_id}_activities.json")
        os.makedirs("data", exist_ok=True)
        self.activities = self._load_data()

    def _load_data(self):
        """Charge uniquement les activités pour l'utilisateur spécifié."""
        if os.path.exists(self.activities_file):
            with open(self.activities_file, 'r') as f:
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
