from flask import Flask, render_template, redirect, url_for, request, flash
from garmin_activity_manager import GarminActivityManager
from garmin_health_manager import GarminHealthManager
from training_analysis import TrainingAnalysis
import os
import json 

app = Flask(__name__)
app.secret_key = os.urandom(24)

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
@app.route("/training")
def training():
    """Affiche les graphiques d'entraînement, compétitions et entraînements à venir."""
    training_analysis = TrainingAnalysis(activity_manager, health_manager)

    # Charger compétitions
    competitions_file = os.path.join("data", "competitions.json")
    training_analysis.load_competitions(competitions_file)
    competitions = training_analysis.get_competitions()

    # Charger entraînements
    trainings_file = os.path.join("data", "trainings.json")
    if os.path.exists(trainings_file):
        with open(trainings_file, "r") as f:
            trainings = json.load(f)
    else:
        trainings = []

    return render_template(
        "training.html",
        trainings=trainings,
        competitions=competitions
    )


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

@app.route("/add_competition", methods=["POST"])
def add_competition():
    """Ajoute une compétition via le formulaire."""
    name = request.form.get("name")
    date = request.form.get("date")
    location = request.form.get("location")

    if not name or not date or not location:
        flash("Tous les champs sont obligatoires pour ajouter une compétition.", "error")
        return redirect(url_for("training"))

    training_analysis = TrainingAnalysis(activity_manager, health_manager)
    competitions_file = os.path.join("data", "competitions.json")
    training_analysis.load_competitions(competitions_file)
    training_analysis.add_competition(name, date, location)
    training_analysis.save_competitions(competitions_file)

    flash("Compétition ajoutée avec succès !", "success")
    return redirect(url_for("training"))

@app.route("/remove_competition/<string:name>")
def remove_competition(name):
    """Supprime une compétition."""
    training_analysis = TrainingAnalysis(activity_manager, health_manager)
    competitions_file = os.path.join("data", "competitions.json")
    training_analysis.load_competitions(competitions_file)
    training_analysis.remove_competition(name)
    training_analysis.save_competitions(competitions_file)

    flash(f"Compétition '{name}' supprimée.", "success")
    return redirect(url_for("training"))

@app.route("/add_training", methods=["POST"])
def add_training():
    """Ajoute un nouvel entraînement à venir."""
    name = request.form.get("name")
    date = request.form.get("date")
    if name and date:
        try:
            new_training = {"name": name, "date": date}
            file_path = os.path.join("data", "trainings.json")
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    trainings = json.load(f)
            else:
                trainings = []
            trainings.append(new_training)
            with open(file_path, "w") as f:
                json.dump(trainings, f, indent=4)
            flash(f"Entraînement '{name}' ajouté pour le {date}.", "success")
        except Exception as e:
            flash(f"Erreur lors de l'ajout de l'entraînement : {e}", "error")
    else:
        flash("Nom ou date manquant.", "error")
    return redirect(url_for("training"))


@app.route("/remove_training/<name>")
def remove_training(name):
    """Supprime un entraînement à venir."""
    try:
        file_path = os.path.join("data", "trainings.json")
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                trainings = json.load(f)
            trainings = [t for t in trainings if t["name"] != name]
            with open(file_path, "w") as f:
                json.dump(trainings, f, indent=4)
            flash(f"Entraînement '{name}' supprimé.", "success")
        else:
            flash("Aucun entraînement à supprimer.", "error")
    except Exception as e:
        flash(f"Erreur lors de la suppression de l'entraînement : {e}", "error")
    return redirect(url_for("training"))


if __name__ == "__main__":
    app.run(debug=True)
