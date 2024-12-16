import os
import json
import time
import logging
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import re

# Importez la bibliothèque Garmin Connect
from garminconnect import Garmin

# Configuration des logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Constantes
DATA_DIR = "garmin_data"
SLEEP_TIME = 5  # Temps d'attente entre les requêtes API

class GarminClientHandler:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.client = None

    def login(self):
        """Connecte le client à l'API Garmin."""
        try:
            self.client = Garmin(self.email, self.password)
            self.client.login()
            logging.info("Connexion réussie à Garmin Connect.")
        except Exception as e:
            logging.error(f"Erreur de connexion : {e}")
            raise

    def get_activities(self, start, limit):
        """Récupère une liste d'activités via l'API."""
        try:
            return self.client.get_activities(start, limit)
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des activités : {e}")
            return []

class GarminActivityManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.activities_file = os.path.join(DATA_DIR, f"{self.user_id}_activities.json")
        os.makedirs(DATA_DIR, exist_ok=True)
        self.activities = self._load_data()

    def _load_data(self):
        """Charge les données d'activités depuis un fichier JSON."""
        if os.path.exists(self.activities_file):
            with open(self.activities_file, 'r') as f:
                try:
                    data = json.load(f)
                    logging.info(f"Données chargées depuis {self.activities_file}.")
                    return data
                except json.JSONDecodeError:
                    logging.warning(f"Fichier corrompu, réinitialisation : {self.activities_file}.")
        return []

    def _save_data(self):
        """Sauvegarde les données d'activités dans un fichier JSON."""
        with open(self.activities_file, 'w') as f:
            json.dump(self.activities, f, indent=4)
        logging.info(f"Données sauvegardées dans {self.activities_file}.")

    def update_data(self, client_handler):
        """Met à jour les activités en récupérant les données manquantes."""
        start_date = datetime.now() - timedelta(days=180)  # Derniers 6 mois
        start_index = 0
        limit = 50
        new_activities = []

        while True:
            logging.info(f"Fetching activities from index {start_index}.")
            activities = client_handler.get_activities(start_index, limit)
            if not activities:
                break

            for activity in activities:
                if activity['startTimeLocal'] not in {a['startTimeLocal'] for a in self.activities}:
                    if activity['activityType']['typeKey'] == 'running' and activity.get('distance', 0) > 2000:
                        # Ajout uniquement des activités running avec distance > 2 km
                        new_activities.append(activity)

            start_index += limit
            time.sleep(SLEEP_TIME)

        self.activities.extend(new_activities)
        if new_activities:
            logging.info(f"{len(new_activities)} nouvelles activités ajoutées.")
        else:
            logging.info("Aucune nouvelle activité ajoutée.")
        self._save_data()

    def fetch_activity_details(self, client_handler):
        """Récupère les détails des activités pour ajouter des métriques manquantes."""
        for activity in self.activities:
            if 'averagePower' not in activity:  # Si la puissance moyenne n'est pas présente
                try:
                    details = client_handler.client.get_activity_details(activity['activityId'])
                    activity['averagePower'] = details.get('averagePower')
                    logging.info(f"Puissance moyenne ajoutée pour l'activité {activity['activityId']}.")
                except Exception as e:
                    logging.error(f"Erreur lors de la récupération des détails pour l'activité {activity['activityId']}: {e}")
                time.sleep(SLEEP_TIME)

        self._save_data()

    def plot_graphs(self, output_dir):
        """Trace les graphiques des activités avec moyenne glissante sur 14 jours."""
        os.makedirs(output_dir, exist_ok=True)

        six_months_ago = datetime.now() - timedelta(days=180)

        # Filtrage pour les activités de course à pied uniquement : running et trail_running
        filtered_activities = [
            a for a in self.activities
            if datetime.fromisoformat(a['startTimeLocal']) >= six_months_ago
            and a['activityType']['typeKey'] in ['running', 'trail_running']
        ]

        if not filtered_activities:
            logging.warning("Aucune activité de course à pied des 6 derniers mois pour tracer les graphiques.")
            return

        # Exclusion des activités avec distance ou durée nulle
        filtered_activities = [a for a in filtered_activities if a.get('distance', 0) > 0 and a.get('duration', 0) > 0]

        if not filtered_activities:
            logging.warning("Aucune activité valide après filtrage pour tracer les graphiques.")
            return

        # Extraction des données avec gestion des valeurs manquantes
        dates = [datetime.fromisoformat(a['startTimeLocal']) for a in filtered_activities]
        distances = [a['distance'] / 1000 for a in filtered_activities]  # Distance en km
        durations = [a['duration'] / 60 for a in filtered_activities]  # Durée en minutes
        avg_paces = [(a['duration'] / 60) / (a['distance'] / 1000) for a in filtered_activities]  # Allure en min/km
        avg_hr = [a.get('averageHR', None) for a in filtered_activities]
        avg_power = [a.get('averagePower', None) for a in filtered_activities]

        # Vérification des longueurs et ajout de None pour les données manquantes
        max_length = max(len(dates), len(distances), len(durations), len(avg_paces), len(avg_hr), len(avg_power))

        def pad_to_length(lst, length):
            """Ajoute des None à la liste pour qu'elle atteigne la longueur spécifiée."""
            return lst + [None] * (length - len(lst))

        dates = pad_to_length(dates, max_length)
        distances = pad_to_length(distances, max_length)
        durations = pad_to_length(durations, max_length)
        avg_paces = pad_to_length(avg_paces, max_length)
        avg_hr = pad_to_length(avg_hr, max_length)
        avg_power = pad_to_length(avg_power, max_length)

        # Création du DataFrame
        data = pd.DataFrame({
            'Date': dates,
            'Distance': distances,
            'Duration': durations,
            'Pace': avg_paces,
            'AvgHR': avg_hr,
            'AvgPower': avg_power
        })
        data.set_index('Date', inplace=True)

        # Tracé des graphiques
        metrics = ['Distance', 'Duration', 'Pace', 'AvgHR', 'AvgPower']
        for metric in metrics:
            if metric in data:
                self._plot_with_ci(data, metric, f"{metric} (6 derniers mois)", output_dir)




    def _plot_with_ci(self, data, column, title, output_dir):
        """Trace un graphique avec moyenne glissante et intervalle de confiance."""
        plt.figure(figsize=(10, 6))
        data[f'{column}_MA'] = data[column].rolling('14D').mean()
        data[f'{column}_Std'] = data[column].rolling('14D').std()
        data[f'{column}_CI'] = 1.96 * (data[f'{column}_Std'] / np.sqrt(14))

        plt.scatter(data.index, data[column], alpha=0.5, label=f"{column} brut")
        plt.plot(data.index, data[f'{column}_MA'], color='blue', label="Moyenne glissante (14 jours)")
        plt.fill_between(data.index, data[f'{column}_MA'] - data[f'{column}_CI'], data[f'{column}_MA'] + data[f'{column}_CI'], color='blue', alpha=0.2)

        plt.title(title)
        plt.xlabel("Date")
        plt.ylabel(column)
        plt.legend()
        plt.grid()
        plt.xticks(rotation=45)

        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, f"{column}.png"))
        plt.close()

class GarminHealthManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.data_file = os.path.join(DATA_DIR, f"{self.user_id}_health.json")
        os.makedirs(DATA_DIR, exist_ok=True)
        self.health_data = self.load_data()

    def load_data(self):
        """Charge les données de santé existantes depuis un fichier JSON."""
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                try:
                    data = json.load(f)
                    logging.info(f"Données de santé chargées depuis {self.data_file}.")
                    return data
                except json.JSONDecodeError:
                    logging.warning(f"Fichier corrompu, réinitialisation : {self.data_file}.")
        return []

    def save_data(self):
        """Sauvegarde les données de santé dans un fichier JSON."""
        with open(self.data_file, 'w') as f:
            json.dump(self.health_data, f, indent=4)
        logging.info(f"Données de santé sauvegardées dans {self.data_file}.")

    def find_missing_dates(self):
        """Trouve les dates manquantes parmi les six derniers mois."""
        start_date = datetime.now() - timedelta(days=180)  # 6 derniers mois
        end_date = datetime.now()

        # Convertir les dates existantes en un ensemble pour une recherche rapide
        existing_dates = {datetime.fromisoformat(entry['date']).date() for entry in self.health_data}

        # Générer toutes les dates des 6 derniers mois
        all_dates = {start_date.date() + timedelta(days=n) for n in range((end_date - start_date).days + 1)}

        # Identifier les dates manquantes
        missing_dates = sorted(all_dates - existing_dates)
        return missing_dates

    def update_data(self, client_handler):
        """Met à jour les données de santé en complétant les dates manquantes."""
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
                logging.error(f"Erreur lors de la récupération des données de santé pour {single_date} : {e}")

            time.sleep(SLEEP_TIME)  # Éviter de spammer l'API

        self.health_data.extend(new_data)
        if new_data:
            logging.info(f"{len(new_data)} nouvelles données de santé ajoutées.")
        else:
            logging.info("Aucune nouvelle donnée de santé ajoutée.")
        self.save_data()

    def plot_graphs(self, output_dir):
        """Trace les graphiques des données de santé sur les six derniers mois."""
        six_months_ago = datetime.now() - timedelta(days=180)

        # Filtrer les données pour les six derniers mois
        filtered_health_data = [h for h in self.health_data if datetime.fromisoformat(h['date']) >= six_months_ago]

        if not filtered_health_data:
            logging.warning("Aucune donnée de santé des 6 derniers mois pour tracer les graphiques.")
            return

        # Préparer les données
        dates = [datetime.fromisoformat(h['date']) for h in filtered_health_data]
        metrics = {
            'Calories': {
                'Total': [h.get('totalKilocalories') for h in filtered_health_data],
                'Active': [h.get('activeKilocalories') for h in filtered_health_data],
                'BMR': [h.get('bmrKilocalories') for h in filtered_health_data]
            },
            'Steps_Distance': {
                'Steps': [h.get('totalSteps') for h in filtered_health_data],
                'Distance': [h.get('totalDistanceMeters') for h in filtered_health_data]
            },
            'HeartRate': {
                'Resting': [h.get('restingHeartRate') for h in filtered_health_data],
                'Max': [h.get('maxHeartRate') for h in filtered_health_data]
            },
            'SpO2': {
                'Average': [h.get('averageSpo2') for h in filtered_health_data],
                'Lowest': [h.get('lowestSpo2') for h in filtered_health_data]
            },
            'Stress': {
                'Total': [h.get('totalStressDuration') for h in filtered_health_data],
                'Low': [h.get('lowStressDuration') for h in filtered_health_data],
                'Medium': [h.get('mediumStressDuration') for h in filtered_health_data],
                'High': [h.get('highStressDuration') for h in filtered_health_data]
            },
            'Respiration': {
                'Average': [h.get('avgWakingRespirationValue') for h in filtered_health_data],
                'Highest': [h.get('highestRespirationValue') for h in filtered_health_data],
                'Lowest': [h.get('lowestRespirationValue') for h in filtered_health_data]
            },
            'BodyBattery': {
                'Highest': [h.get('bodyBatteryHighestValue') for h in filtered_health_data],
                'Lowest': [h.get('bodyBatteryLowestValue') for h in filtered_health_data],
                'MostRecent': [h.get('bodyBatteryMostRecentValue') for h in filtered_health_data]
            }
        }

        # Trace des graphiques pour chaque catégorie
        for category, metrics_data in metrics.items():
            category_dir = os.path.join(output_dir, category)
            os.makedirs(category_dir, exist_ok=True)

            for metric_name, values in metrics_data.items():
                if not any(values):  # Skip if no data
                    logging.warning(f"Pas de données disponibles pour {metric_name} dans la catégorie {category}.")
                    continue

                # Préparer le DataFrame pour le graphique
                data = pd.DataFrame({'Date': dates, metric_name: values})
                data.set_index('Date', inplace=True)

                # Tracer le graphique avec CI
                self._plot_with_ci(data, metric_name, f"{metric_name} (6 derniers mois)", category_dir)



    def _plot_with_ci(self, data, column, ylabel, output_dir):
        """Trace un graphique avec moyenne glissante et intervalle de confiance."""
        if column not in data.columns or data[column].isnull().all():
            logging.warning(f"Pas de données disponibles pour {column}.")
            return

        # Calculer la moyenne glissante et les intervalles de confiance uniquement si la colonne est valide
        data[f'{column}_MA'] = data[column].rolling('14D', min_periods=1).mean()
        data[f'{column}_Std'] = data[column].rolling('14D', min_periods=1).std()
        data[f'{column}_CI'] = 1.96 * (data[f'{column}_Std'] / np.sqrt(14))

        plt.figure(figsize=(10, 6))

        # Points bruts
        plt.scatter(data.index, data[column], label=f"{column} brut", alpha=0.5)

        # Moyenne glissante
        plt.plot(data.index, data[f'{column}_MA'], label=f"Moyenne glissante (14 jours)", color='blue')

        # Intervalle de confiance
        if f'{column}_CI' in data:
            plt.fill_between(
                data.index,
                data[f'{column}_MA'] - data[f'{column}_CI'],
                data[f'{column}_MA'] + data[f'{column}_CI'],
                color='blue',
                alpha=0.2,
                label="Intervalle de confiance (95%)"
            )

        # Configurer les étiquettes et le titre
        plt.xlabel('Date')
        plt.ylabel(ylabel)
        plt.title(f"{ylabel} avec moyenne glissante sur 14 jours")
        plt.legend()

        # Format des axes
        plt.grid()
        plt.xticks(rotation=45)

        # Sauvegarder le graphique
        plt.tight_layout()
        file_path = os.path.join(output_dir, f"{column}.png")
        plt.savefig(file_path)
        logging.info(f"Graphique sauvegardé : {file_path}")
        plt.close()

class TrainingAnalysis:
    def __init__(self, activity_manager, health_manager):
        self.activity_manager = activity_manager
        self.health_manager = health_manager
        self.hr_max = None
        self.hr_resting = None
        self.zones = None

    def calculate_hr_limits(self):
        """Calcule HR_max et HR_resting à partir des activités et des données de santé."""
        six_months_ago = datetime.now() - timedelta(days=180)
        
        # HR_max : Chercher la fréquence max dans les détails des activités
        max_hrs = []
        for activity in self.activity_manager.activities:
            if datetime.fromisoformat(activity['startTimeLocal']) >= six_months_ago:
                max_hrs.append(activity.get('maxHR', 0))
        self.hr_max = max(max_hrs) if max_hrs else None

        # HR_resting : Fréquence cardiaque minimale au repos depuis les données santé
        resting_hrs = [
            health_entry.get('restingHeartRate')
            for health_entry in self.health_manager.health_data
            if datetime.fromisoformat(health_entry['date']) >= six_months_ago
        ]

        # Filtrer les valeurs None avant de chercher la valeur minimale
        resting_hrs = [hr for hr in resting_hrs if hr is not None]

        self.hr_resting = min(resting_hrs) if resting_hrs else None

        logging.info(f"HR_max: {self.hr_max}, HR_resting: {self.hr_resting}")


    def calculate_zones(self):
        """Calcule les zones de fréquence cardiaque."""
        if self.hr_max is None or self.hr_resting is None:
            logging.error("Impossible de calculer les zones sans HR_max et HR_resting.")
            return
        
        self.zones = {
            'Zone 1': (self.hr_resting, self.hr_resting + 0.5 * (self.hr_max - self.hr_resting)),
            'Zone 2': (self.hr_resting + 0.5 * (self.hr_max - self.hr_resting), self.hr_resting + 0.7 * (self.hr_max - self.hr_resting)),
            'Zone 3': (self.hr_resting + 0.7 * (self.hr_max - self.hr_resting), self.hr_resting + 0.85 * (self.hr_max - self.hr_resting)),
            'Zone 4': (self.hr_resting + 0.85 * (self.hr_max - self.hr_resting), self.hr_resting + 0.95 * (self.hr_max - self.hr_resting)),
            'Zone 5': (self.hr_resting + 0.95 * (self.hr_max - self.hr_resting), self.hr_max),
        }
        logging.info(f"Zones calculées : {self.zones}")

    def analyze_activity_zones(self):
        """Analyse le temps passé dans chaque zone pour toutes les activités."""
        data = []
        six_months_ago = datetime.now() - timedelta(days=180)

        for activity in self.activity_manager.activities:
            # Vérifier la validité des données
            if 'startTimeLocal' not in activity or 'heartRateValues' not in activity:
                logging.warning(f"Activité ignorée, données manquantes : {activity.get('activityId', 'ID inconnu')}")
                continue

            activity_date = datetime.fromisoformat(activity['startTimeLocal']).date()
            if activity_date < six_months_ago.date():
                continue

            # Récupérer les valeurs de fréquence cardiaque
            heart_rate_values = activity['heartRateValues']

            # Calculer le temps passé dans chaque zone
            time_in_zones = {zone: 0 for zone in self.zones}
            for hr in heart_rate_values:
                for zone, (low, high) in self.zones.items():
                    if low <= hr < high:
                        time_in_zones[zone] += 1

            # Ajouter les résultats
            data.append({
                'Date': activity_date,
                **time_in_zones
            })

        if not data:
            logging.warning("Aucune donnée disponible pour analyser les zones d'activité.")
            return pd.DataFrame()

        # Créer un DataFrame
        df = pd.DataFrame(data)

        # Vérifier les colonnes avant de définir l'index
        logging.info(f"Colonnes disponibles dans le DataFrame : {df.columns}")

        if 'Date' not in df.columns:
            logging.error("La colonne 'Date' est absente. Vérifiez les données sources.")
            return pd.DataFrame()

        df.set_index('Date', inplace=True)
        return df

    def plot_zones(self, df, output_dir):
        """Trace les graphes des temps passés dans chaque zone au cours du temps."""
        if df.empty:
            logging.warning("Aucune donnée pour tracer les zones.")
            return

        os.makedirs(output_dir, exist_ok=True)

        # Moyenne glissante sur 14 jours
        df_smoothed = df.rolling('14D', min_periods=1).mean()

        plt.figure(figsize=(12, 8))
        for zone in self.zones.keys():
            plt.plot(df_smoothed.index, df_smoothed[zone], label=zone, alpha=0.7)

        plt.title("Temps passé dans chaque zone de fréquence cardiaque (14 jours)")
        plt.xlabel("Date")
        plt.ylabel("Temps (minutes)")
        plt.legend()
        plt.grid()
        plt.tight_layout()

        output_file = os.path.join(output_dir, "zones_over_time.png")
        plt.savefig(output_file)
        logging.info(f"Graphique des zones sauvegardé : {output_file}")
        plt.close()

# Main
if __name__ == "__main__":

    email = "gregoire.macquet@gmail.com"
    password = "Coachdiwat40"
    user_id = "Greg"

    client_handler = GarminClientHandler(email, password)
    client_handler.login()

    # Gestion des activités
    activity_manager = GarminActivityManager(user_id)
    activity_manager.update_data(client_handler)
    activity_manager.plot_graphs("output_graphs_activities\Greg")

    # Gestion des données de santé
    health_manager = GarminHealthManager(user_id)
    health_manager.update_data(client_handler)
    health_manager.plot_graphs("output_graphs_health\Greg")

     # Analyse de l'entraînement
    training_analysis = TrainingAnalysis(activity_manager, health_manager)
    training_analysis.calculate_hr_limits()
    training_analysis.calculate_zones()

    # Analyse des zones
    df_zones = training_analysis.analyze_activity_zones()
    training_analysis.plot_zones(df_zones, "output_graphs_training\Greg")


    # email = "duwat.adrien@gmail.com"
    # password = "Duwat9897."
    # user_id = "Adri"

    # client_handler = GarminClientHandler(email, password)
    # client_handler.login()

    # # Gestion des activités
    # activity_manager = GarminActivityManager(user_id)
    # activity_manager.update_data(client_handler)
    # activity_manager.plot_graphs("output_graphs_activities\Adri")

    # # Gestion des données de santé
    # health_manager = GarminHealthManager(user_id)
    # health_manager.update_data(client_handler)
    # health_manager.plot_graphs("output_graphs_health\Adri")