import os
import json
import time
import logging
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go

class GarminHealthManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.data_file = os.path.join("data", f"{self.user_id}_health.json")
        os.makedirs("data", exist_ok=True)
        self.health_data = self._load_data()

    def _load_data(self):
        """Charge les données de santé depuis un fichier JSON."""
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                try:
                    data = json.load(f)
                    logging.info(f"Données de santé chargées depuis {self.data_file}.")
                    return data
                except json.JSONDecodeError:
                    logging.warning(f"Fichier corrompu, réinitialisation : {self.data_file}.")
        return []

    def _save_data(self):
        """Sauvegarde les données de santé dans un fichier JSON."""
        with open(self.data_file, 'w') as f:
            json.dump(self.health_data, f, indent=4)
        logging.info(f"Données de santé sauvegardées dans {self.data_file}.")

    def find_missing_dates(self):
        """Trouve les dates manquantes pour les six derniers mois."""
        start_date = datetime.now() - timedelta(days=180)
        end_date = datetime.now()
        existing_dates = {datetime.fromisoformat(entry['date']).date() for entry in self.health_data}
        all_dates = {start_date.date() + timedelta(days=n) for n in range((end_date - start_date).days + 1)}
        return sorted(all_dates - existing_dates)

    def update_data(self, client_handler):
        """Met à jour les données de santé pour les dates manquantes."""
        missing_dates = self.find_missing_dates()
        new_data = []

        for single_date in missing_dates:
            logging.info(f"Fetching health data for {single_date}.")
            try:
                health_data = client_handler.client.get_stats(single_date)
                if health_data:
                    health_data['date'] = single_date.isoformat()
                    new_data.append(health_data)
            except Exception as e:
                logging.error(f"Erreur lors de la récupération des données de santé pour {single_date}: {e}")
            time.sleep(1)

        self.health_data.extend(new_data)
        if new_data:
            logging.info(f"{len(new_data)} nouvelles données de santé ajoutées.")
        else:
            logging.info("Aucune nouvelle donnée de santé ajoutée.")
        self._save_data()

    
    def plot_interactive_graphs(self, output_dir):
        """Trace les graphiques interactifs pour les données de santé."""
        os.makedirs(output_dir, exist_ok=True)

        six_months_ago = datetime.now() - timedelta(days=180)

        # Filtrer les données pour les 6 derniers mois
        filtered_health_data = [
            h for h in self.health_data
            if datetime.fromisoformat(h["date"]) >= six_months_ago
        ]

        if not filtered_health_data:
            logging.warning("Aucune donnée de santé valide des 6 derniers mois pour tracer les graphiques.")
            return

        # Préparer les données
        dates = [datetime.fromisoformat(h["date"]) for h in filtered_health_data]
        metrics = {
            "Body Battery": [h.get("bodyBatteryMostRecentValue") for h in filtered_health_data],
            "Calories Total": [h.get("totalKilocalories") for h in filtered_health_data],
            "Calories Active": [h.get("activeKilocalories") for h in filtered_health_data],
            "Heart Rate Resting": [h.get("restingHeartRate") for h in filtered_health_data],
            "Respiration Average": [h.get("avgWakingRespirationValue") for h in filtered_health_data],
            "SpO2 Average": [h.get("averageSpo2") for h in filtered_health_data],
            "Steps": [h.get("totalSteps") for h in filtered_health_data],
            "Stress Total": [h.get("totalStressDuration") for h in filtered_health_data],
        }

        # Créer un DataFrame
        data = pd.DataFrame({"Date": dates, **metrics})
        data.set_index("Date", inplace=True)

        # Ajouter la moyenne lissée et l'intervalle de confiance
        for col in data.columns:
            data[f"{col}_MA"] = data[col].rolling(window=14, min_periods=1).mean()
            data[f"{col}_Std"] = data[col].rolling(window=14, min_periods=1).std()
            data[f"{col}_CI"] = 1.96 * (data[f"{col}_Std"] / np.sqrt(14))

        # Tracer chaque métrique individuellement
        for metric in metrics.keys():
            fig = go.Figure()

            # Valeurs brutes
            fig.add_trace(go.Scatter(
                x=data.index,
                y=data[metric],
                mode="markers",
                name=f"{metric} brut",
                marker=dict(size=6, color="blue"),
            ))

            # Moyenne lissée
            fig.add_trace(go.Scatter(
                x=data.index,
                y=data[f"{metric}_MA"],
                mode="lines",
                name=f"{metric} lissé (14 jours)",
                line=dict(color="blue"),
            ))

            # Intervalle de confiance
            fig.add_trace(go.Scatter(
                x=data.index,
                y=data[f"{metric}_MA"] + data[f"{metric}_CI"],
                mode="lines",
                line=dict(width=0),
                showlegend=False,
            ))
            fig.add_trace(go.Scatter(
                x=data.index,
                y=data[f"{metric}_MA"] - data[f"{metric}_CI"],
                fill="tonexty",
                mode="lines",
                name="Intervalle de confiance",
                line=dict(width=0, color="rgba(0,0,255,0.2)"),
            ))

            # Mise en page
            fig.update_layout(
                title=f"{metric} (6 derniers mois)",
                xaxis_title="Date",
                yaxis_title=metric,
                legend=dict(x=0, y=1),
                xaxis=dict(tickformat="%d-%m-%Y", tickmode="auto", tickangle=45),
                template="plotly_white"
            )

            # Sauvegarder le graphique
            fig.write_html(os.path.join(output_dir, f"{metric.replace(' ', '_').lower()}.html"))

        logging.info("Graphiques interactifs de santé générés avec succès.")
