import os
import json
import logging
import time
from datetime import datetime, timedelta
from garminconnect import Garmin

# Configuration des logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Temps d'attente entre les requêtes pour éviter les erreurs dues aux limites de l'API
SLEEP_TIME = 5

class GarminClientHandler:
    def __init__(self, email, password, user_id, output_dir="data"):
        self.email = email
        self.password = password
        self.user_id = user_id
        self.output_file = os.path.join(output_dir, f"{user_id}_activity_details.json")
        self.client = None
        os.makedirs(output_dir, exist_ok=True)
        self._initialize_json()

    def _initialize_json(self):
        """Crée ou charge le fichier JSON pour stocker les activités."""
        if not os.path.exists(self.output_file):
            with open(self.output_file, "w") as f:
                json.dump({"activities": {}}, f, indent=4)
            logging.info(f"Fichier JSON initialisé : {self.output_file}")

    def _load_json(self):
        """Charge les données existantes depuis le fichier JSON."""
        try:
            with open(self.output_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Erreur lors du chargement du fichier JSON : {e}")
            return {"activities": {}}

    def _save_json(self, data):
        """Sauvegarde les données dans le fichier JSON."""
        try:
            with open(self.output_file, "w") as f:
                json.dump(data, f, indent=4)
            logging.info(f"Données sauvegardées dans {self.output_file}.")
        except Exception as e:
            logging.error(f"Erreur lors de la sauvegarde du fichier JSON : {e}")

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

    def update_activity_data(self):
        """
        Met à jour les résumés et les détails des activités dans un fichier JSON unique.
        """
        logging.info("Début de la mise à jour des données d'activités...")
        
        # Charger les données existantes
        data = self._load_json()

        # Récupérer les résumés d'activités
        activities = self.get_activities(0, 50)  # Limité à 50 pour cet exemple
        for activity in activities:
            activity_id = activity.get("activityId")
            if not activity_id:
                logging.warning("Activité sans ID, ignorée.")
                continue

            # Si l'activité est déjà dans le fichier, ignorer
            if activity_id in data["activities"]:
                logging.info(f"Activité {activity_id} déjà enregistrée, ignorée.")
                continue

            # Récupérer les détails de l'activité
            details = self.get_activity_details(activity_id)
            if not details:
                logging.warning(f"Impossible de récupérer les détails pour l'activité {activity_id}.")
                continue

            # Sauvegarder l'activité dans le JSON
            data["activities"][activity_id] = {
                "summary": activity,
                "details": details,
            }
            logging.info(f"Activité {activity_id} ajoutée.")

            # Pause pour éviter de surcharger l'API
            time.sleep(SLEEP_TIME)

        # Sauvegarder les données mises à jour
        self._save_json(data)
        logging.info("Mise à jour des données d'activités terminée.")
