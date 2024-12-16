from flask import Flask, render_template, redirect, url_for
from garmin_activity_manager import GarminActivityManager
from garmin_health_manager import GarminHealthManager
from training_analysis import TrainingAnalysis
import os

app = Flask(__name__)

# Configuration utilisateur Garmin
user_id = "Greg"

# Création des dossiers nécessaires s'ils n'existent pas
for folder in ["static/activity", "static/health", "static/training", "data"]:
    os.makedirs(folder, exist_ok=True)

# Initialisation des gestionnaires
activity_manager = GarminActivityManager(user_id)
health_manager = GarminHealthManager(user_id)
training_analysis = TrainingAnalysis(activity_manager, health_manager)

@app.route("/")
def home():
    """Page d'accueil."""
    return render_template("home.html")

@app.route("/activity")
def activity():
    """Affiche les graphiques d'activités."""
    # Générer les graphiques interactifs
    activity_manager.plot_interactive_graphs("static/activity")

    # Lister les graphiques disponibles
    activity_graphs = [f"/static/activity/{f}" for f in os.listdir("static/activity") if f.endswith(".html")]

    return render_template("activity.html", graphs=activity_graphs)

@app.route("/update_activity")
def update_activity():
    """Met à jour les données d'activités."""
    # Mise à jour via l'API Garmin (à intégrer si nécessaire)
    # client_handler = GarminClientHandler(email, password)
    # client_handler.login()
    # activity_manager.update_data(client_handler)
    return redirect(url_for("activity"))

@app.route("/health")
def health():
    """Affiche les graphiques de santé."""
    # Générer les graphiques interactifs
    health_manager.plot_interactive_graphs("static/health")

    # Lister les graphiques disponibles
    health_graphs = [f"/static/health/{f}" for f in os.listdir("static/health") if f.endswith(".html")]

    return render_template("health.html", graphs=health_graphs)

@app.route("/update_health")
def update_health():
    """Met à jour les données de santé."""
    # Mise à jour via l'API Garmin (à intégrer si nécessaire)
    # client_handler = GarminClientHandler(email, password)
    # client_handler.login()
    # health_manager.update_data(client_handler)
    return redirect(url_for("health"))

@app.route("/training")
def training():
    """Affiche les graphiques d'entraînement et les compétitions."""
    # Charger les compétitions depuis un fichier JSON
    competitions_file = os.path.join("data", "competitions.json")
    training_analysis.load_competitions(competitions_file)

    # Générer les graphiques interactifs (zones de fréquence cardiaque, etc.)
    # training_analysis.calculate_hr_limits()
    # training_analysis.calculate_zones()
    # df_zones = training_analysis.analyze_activity_zones()
    # training_analysis.plot_interactive_graphs("static/training", df_zones)

    # Lister les graphiques disponibles
    training_graphs = [f"/static/training/{f}" for f in os.listdir("static/training") if f.endswith(".html")]

    # Récupérer les compétitions pour la page
    competitions = training_analysis.get_competitions()

    return render_template("training.html", training_graphs=training_graphs, competitions=competitions)

@app.route("/update_training")
def update_training():
    """Met à jour les données d'entraînement."""
    # Mise à jour via l'API Garmin (à intégrer si nécessaire)
    # client_handler = GarminClientHandler(email, password)
    # client_handler.login()
    # activity_manager.update_data(client_handler)
    # health_manager.update_data(client_handler)

    # Générer les graphiques interactifs
    training_analysis.calculate_hr_limits()
    training_analysis.calculate_zones()
    df_zones = training_analysis.analyze_activity_zones()
    training_analysis.plot_interactive_graphs("static/training", df_zones)

    return redirect(url_for("training"))

if __name__ == "__main__":
    app.run(debug=True)
