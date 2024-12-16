import logging
import time
from garminconnect import Garmin
from datetime import datetime, timedelta

# Configuration des logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Temps d'attente entre les requêtes pour éviter les erreurs dues aux limites de l'API
SLEEP_TIME = 5

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
        """
        Récupère les activités depuis Garmin Connect.
        :param start: Index de départ pour les activités.
        :param limit: Nombre maximum d'activités à récupérer.
        :return: Liste des activités récupérées.
        """
        try:
            activities = self.client.get_activities(start, limit)
            logging.info(f"{len(activities)} activités récupérées.")
            return activities
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des activités : {e}")
            return []

    def get_activity_details(self, activity_id):
        """
        Récupère les détails d'une activité spécifique.
        :param activity_id: Identifiant unique de l'activité.
        :return: Détails de l'activité.
        """
        try:
            details = self.client.get_activity_details(activity_id)
            logging.info(f"Détails récupérés pour l'activité {activity_id}.")
            return details
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des détails pour l'activité {activity_id}: {e}")
            return {}

    def get_health_data(self, date):
        """
        Récupère les données de santé pour une date donnée.
        :param date: Date (au format YYYY-MM-DD) pour laquelle récupérer les données.
        :return: Données de santé pour la date spécifiée.
        """
        try:
            health_data = self.client.get_stats(date)
            logging.info(f"Données de santé récupérées pour le {date}.")
            return health_data
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des données de santé pour {date}: {e}")
            return {}

    def get_missing_dates_health(self, existing_dates, start_date, end_date):
        """
        Identifie les dates manquantes pour les données de santé.
        :param existing_dates: Ensemble des dates déjà disponibles.
        :param start_date: Date de début de la période.
        :param end_date: Date de fin de la période.
        :return: Liste des dates manquantes.
        """
        all_dates = {start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)}
        missing_dates = sorted(all_dates - existing_dates)
        logging.info(f"{len(missing_dates)} dates manquantes identifiées.")
        return missing_dates

    def update_health_data(self, existing_health_data, start_date, end_date):
        """
        Met à jour les données de santé pour combler les lacunes.
        :param existing_health_data: Liste des données de santé existantes.
        :param start_date: Date de début de la période.
        :param end_date: Date de fin de la période.
        :return: Liste des nouvelles données de santé ajoutées.
        """
        existing_dates = {entry['date'] for entry in existing_health_data}
        missing_dates = self.get_missing_dates_health(existing_dates, start_date, end_date)
        new_data = []

        for date in missing_dates:
            health_data = self.get_health_data(date.isoformat())
            if health_data:
                health_data['date'] = date.isoformat()
                new_data.append(health_data)
            time.sleep(SLEEP_TIME)  # Pause pour éviter les erreurs d'API

        logging.info(f"{len(new_data)} nouvelles entrées de santé ajoutées.")
        return new_data
