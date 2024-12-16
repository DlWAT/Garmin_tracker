import logging
import pandas as pd
from datetime import datetime, timedelta
import os
import plotly.graph_objects as go
import json


class TrainingAnalysis:
    def __init__(self, activity_manager, health_manager):
        """Initialise l'analyse d'entraînement."""
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
            with open(file_path, "r") as f:
                data = json.load(f)
                if isinstance(data, list):  # Vérifie que le JSON contient bien une liste
                    self.competitions = data
                    logging.info(f"{len(data)} compétitions chargées depuis {file_path}.")
                else:
                    logging.error(f"Le fichier JSON ne contient pas une liste valide : {file_path}")
                    self.competitions = []
        except json.JSONDecodeError as e:
            logging.error(f"Erreur de décodeur JSON dans {file_path} : {e}")
            self.competitions = []
        except Exception as e:
            logging.error(f"Erreur lors du chargement des compétitions : {e}")
            self.competitions = []

    def get_competitions(self):
        """Retourne la liste des compétitions chargées."""
        if not hasattr(self, "competitions"):
            logging.error("L'attribut 'competitions' est introuvable. Assurez-vous d'avoir chargé les compétitions.")
            return []
        return self.competitions
