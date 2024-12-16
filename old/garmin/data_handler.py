import json
import logging
from datetime import timedelta

def load_data(file_path):
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            if isinstance(data, str):
                # Si le fichier contient une chaîne, nous devons l'analyser
                try:
                    data = json.loads(data)
                except json.JSONDecodeError as e:
                    logging.error(f"Error decoding JSON string: {e}")
                    return []
            # Vérifier que les données sont bien une liste
            if not isinstance(data, list):
                logging.error(f"Data is not a list: {data}")
                return []
            return data
    except FileNotFoundError:
        logging.warning(f"File not found: {file_path}, initializing with empty list.")
        # Si le fichier n'existe pas, créer un fichier JSON vide
        with open(file_path, 'w') as f:
            json.dump([], f)  # Initialiser avec une liste vide
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error loading data from {file_path}: {e}")
        # Si le fichier est vide ou corrompu, réinitialiser avec une liste vide
        with open(file_path, 'w') as f:
            json.dump([], f)
        return []



def save_data(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f)

# Dans le fichier garmin/data_handler.py
def fetch_health_data(garmin_client, start_date, end_date, existing_data):
    new_data = []
    
    for single_date in (start_date + timedelta(n) for n in range((end_date - start_date).days + 1)):
        logging.info(f"Fetching data for {single_date.isoformat()}")
        try:
            data = garmin_client.get_health_data(single_date, single_date)
            logging.info(f"Data for {single_date.isoformat()}: {data}")
            if data:
                new_data.extend(data)
        except Exception as e:
            logging.error(f"Error fetching data for {single_date.isoformat()}: {e}")
    
    return new_data



def fetch_activity_data(garmin_client, start_date, existing_data):
    new_data = []
    existing_dates = set(item['startTimeLocal'] for item in existing_data)
    
    for start in range(0, 18*30, 50):
        try:
            activities = garmin_client.get_activities(start, 50)
            if activities:
                new_data += [activity for activity in activities if activity['startTimeLocal'] not in existing_dates]
        except Exception as e:
            logging.error(f"Error fetching activities: {e}")
    return new_data
