import json
import time
import os
import logging
from garminconnect import Garmin

# Configuration de votre email et mot de passe
EMAIL = "duwat.adrien@gmail.com"
PASSWORD = "Duwat9897."

# Délai entre les requêtes pour éviter de surcharger l'API
SLEEP_TIME = 5  # En secondes

# Dossier de sortie
OUTPUT_DIR = "garmin_data"

# Création du dossier de sortie si nécessaire
os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_to_file(filename, data):
    """Enregistre les données dans un fichier JSON."""
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)
    print(f"Données enregistrées dans le fichier : {filename}")


def load_from_file(filename):
    """Charge les données depuis un fichier JSON, si elles existent."""
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return None


def fetch_data_if_not_cached(filename, fetch_function):
    """
    Récupère les données via fetch_function si elles ne sont pas déjà dans filename.
    """
    data = load_from_file(filename)
    if data is not None:
        print(f"Données chargées depuis le cache : {filename}")
        return data
    print(f"Récupération des données via l'API...")
    data = fetch_function()
    save_to_file(filename, data)
    return data


def fetch_all_garmin_data():
    try:
        # Initialiser le client Garmin
        client = Garmin(EMAIL, PASSWORD)
        client.login()
        print("Connexion réussie à Garmin Connect.")

        # Récupérer les données disponibles
        all_data = {}

        # Profil utilisateur
        profile_file = os.path.join(OUTPUT_DIR, "profile.json")
        all_data["profile"] = fetch_data_if_not_cached(
            profile_file, client.get_user_profile
        )
        time.sleep(SLEEP_TIME)

        # Activités récentes
        activities_file = os.path.join(OUTPUT_DIR, "activities.json")
        all_data["activities"] = fetch_data_if_not_cached(
            activities_file, lambda: client.get_activities(0, 10)
        )
        time.sleep(SLEEP_TIME)

        # Détails de la première activité
        if all_data["activities"]:
            activity_id = all_data["activities"][0]["activityId"]
            activity_details_file = os.path.join(
                OUTPUT_DIR, f"activity_details_{activity_id}.json"
            )
            all_data["activity_details"] = fetch_data_if_not_cached(
                activity_details_file,
                lambda: client.get_activity_details(activity_id),
            )
            time.sleep(SLEEP_TIME)

        # Données de bien-être (stats et corps)
        today = time.strftime("%Y-%m-%d")
        stats_file = os.path.join(OUTPUT_DIR, f"stats_{today}.json")
        all_data["stats"] = fetch_data_if_not_cached(
            stats_file, lambda: client.get_stats(today)
        )
        time.sleep(SLEEP_TIME)

        # Données de sommeil
        sleep_file = os.path.join(OUTPUT_DIR, f"sleep_{today}.json")
        all_data["sleep"] = fetch_data_if_not_cached(
            sleep_file, lambda: client.get_sleep_data(today)
        )
        time.sleep(SLEEP_TIME)

        # Données de poids
        weight_file = os.path.join(OUTPUT_DIR, f"weight_{today}.json")
        all_data["weight"] = fetch_data_if_not_cached(
            weight_file, lambda: client.get_body_composition(today)
        )
        time.sleep(SLEEP_TIME)

        # Données de stress
        stress_file = os.path.join(OUTPUT_DIR, f"stress_{today}.json")
        all_data["stress"] = fetch_data_if_not_cached(
            stress_file, lambda: client.get_stress_data(today)
        )
        time.sleep(SLEEP_TIME)

        # Données de pas
        steps_file = os.path.join(OUTPUT_DIR, f"steps_{today}.json")
        all_data["steps"] = fetch_data_if_not_cached(
            steps_file, lambda: client.get_daily_steps(today)
        )
        time.sleep(SLEEP_TIME)

        # Données d'hydratation
        hydration_file = os.path.join(OUTPUT_DIR, f"hydration_{today}.json")
        all_data["hydration"] = fetch_data_if_not_cached(
            hydration_file, lambda: client.get_hydration_data(today)
        )
        time.sleep(SLEEP_TIME)

        # Données de Spo2
        spo2_file = os.path.join(OUTPUT_DIR, f"spo2_{today}.json")
        all_data["spo2"] = fetch_data_if_not_cached(
            spo2_file, lambda: client.get_spo2_data(today)
        )
        time.sleep(SLEEP_TIME)

        print("\nToutes les données ont été récupérées et enregistrées.")
        return all_data

    except Exception as e:
        logging.error(f"Erreur lors de la récupération des données : {e}")
        print(f"Erreur : {e}")




if __name__ == "__main__":
    fetch_all_garmin_data()
