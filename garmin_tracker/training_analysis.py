import logging
import json
import os


class TrainingAnalysis:
    def __init__(self, activity_manager, health_manager):
        self.activity_manager = activity_manager
        self.health_manager = health_manager
        self.competitions = []

    def load_competitions(self, file_path):
        """Charge la liste des compétitions depuis un fichier JSON."""
        if not os.path.exists(file_path):
            logging.warning(f"Le fichier des compétitions est introuvable : {file_path}")
            self.competitions = []
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self.competitions = json.load(f)
            logging.info(f"Compétitions chargées depuis {file_path}.")
        except Exception as e:
            logging.error(f"Erreur lors du chargement des compétitions : {e}")
            self.competitions = []

    def save_competitions(self, file_path):
        """Sauvegarde les compétitions dans un fichier JSON."""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.competitions, f, indent=4)
            logging.info(f"Compétitions sauvegardées dans {file_path}.")
        except Exception as e:
            logging.error(f"Erreur lors de la sauvegarde des compétitions : {e}")

    def add_competition(self, name, date, location):
        """Ajoute une nouvelle compétition."""
        new_competition = {"name": name, "date": date, "location": location}
        self.competitions.append(new_competition)
        logging.info(f"Compétition ajoutée : {new_competition}")

    def remove_competition(self, name):
        """Supprime une compétition par son nom."""
        self.competitions = [comp for comp in self.competitions if comp["name"] != name]
        logging.info(f"Compétition supprimée : {name}")

    def get_competitions(self):
        """Retourne la liste des compétitions chargées."""
        return self.competitions if hasattr(self, "competitions") else []
