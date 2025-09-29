from flask import Flask, render_template, redirect, url_for, request, flash
from garmin_activity_manager import GarminActivityManager
from garmin_health_manager import GarminHealthManager
from training_analysis import TrainingAnalysis
from garmin_client_manager import GarminClientHandler
from activity_page_manager import ActivityPageManager

import os
import json 
import time
import logging

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configuration utilisateur Garmin
user_id = "Greg"
EMAIL = "gregoire.macquet@gmail.com"
PASSWORD = "Coachdiwat40"
# Création des dossiers nécessaires s'ils n'existent pas
for folder in ["static/activity", "static/health", "static/training", "data"]:
    os.makedirs(folder, exist_ok=True)

# Initialisation des gestionnaires
activity_manager = GarminActivityManager(user_id)
health_manager = GarminHealthManager(user_id)
training_analysis = TrainingAnalysis(activity_manager, health_manager)
activity_page_manager = ActivityPageManager()
# garmin_handler = GarminClientHandler(EMAIL, PASSWORD,user_id)
# garmin_handler.login()


@app.route("/")
def home():
    """Page d'accueil."""
    return render_template("home.html")


@app.route("/activity")
def activity():
    """Affiche les graphiques d'activités et la liste des dernières activités."""
    activity_manager.plot_interactive_graphs("static/activity")
    activity_graphs = [f"/static/activity/{f}" for f in os.listdir("static/activity") if f.endswith(".html")]

    formatted_activities = []
    for activity in activity_manager.activities:
        activity_id = activity.get("activityId")  # Récupération de l'ID de l'activité
        date = activity.get("startTimeLocal", "Date inconnue")
        distance = round(activity.get("distance", 0) / 1000)  # Arrondi au km
        duration_minutes = round(activity.get("duration", 0) / 60)
        hours = duration_minutes // 60
        minutes = duration_minutes % 60
        avg_pace = "N/A"

        if distance > 0:
            total_minutes = (hours * 60) + minutes
            pace_minutes = total_minutes // distance
            pace_seconds = (total_minutes % distance) * 60 // distance
            avg_pace = f"{pace_minutes}m {pace_seconds:02}s/km"

        formatted_activities.append({
            "activityId": activity_id,  # Ajout de l'ID ici
            "date": date if date != "Date inconnue" else "Date inconnue",
            "distance": distance,
            "duration": f"{hours}h {minutes:02d}m",
            "avg_pace": avg_pace,
        })

    #print("Formatted activities:", formatted_activities)  # Debug pour vérifier l'ID
    return render_template("activity.html", graphs=activity_graphs, activities=formatted_activities)

@app.route("/update_activity")
def update_activity():
    """Met à jour les résumés et les détails des activités depuis Garmin Connect."""
    logging.info("Mise à jour des données d'activités en cours...")
    try:
        garmin_handler.update_activity_data()
        flash("Les données d'activités ont été mises à jour avec succès.", "success")
    except Exception as e:
        logging.error(f"Erreur lors de la mise à jour des données : {e}")
        flash("Une erreur s'est produite lors de la mise à jour des données.", "error")
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
    """Affiche le calendrier avec entraînements, compétitions et activités."""
    try:
        # Synchronisation automatique des entraînements
        manager = GarminActivityManager(user_id)
        trainings = manager.convert_activities_to_trainings()
        manager.save_to_trainings_file(trainings)
        logging.info("Les entraînements ont été synchronisés automatiquement.")
    except Exception as e:
        logging.error(f"Erreur lors de la synchronisation des entraînements : {e}")
        flash("Erreur lors de la synchronisation des entraînements.", "error")

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

    """Affiche le calendrier avec entraînements, compétitions et activités."""
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

    # Charger activités
    activities_file = os.path.join("data", f"{user_id}_activities.json")
    if os.path.exists(activities_file):
        with open(activities_file, "r") as f:
            activities = json.load(f)
    else:
        activities = []

    # Nettoyer les données pour s'assurer qu'elles sont JSON sérialisables
    activities = [
        {
            "name": activity.get("name", "Nom non spécifié"),
            "date": activity.get("startTimeLocal", "Date inconnue"),
            "distance": round(activity.get("distance", 0) / 1000, 2),  # Convertir en km
            "description": activity.get("description", "Aucune description disponible"),
        }
        for activity in activities
    ]

    return render_template(
        "training.html",
        trainings=trainings,
        competitions=competitions,
        activities=activities
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


@app.route("/tracking")
def tracking():
    """Affiche les graphiques de suivi."""
    activity_manager.plot_tracking_graphs("static/tracking")

    # Lister les graphiques disponibles
    tracking_graphs = [f"/static/tracking/{f}" for f in os.listdir("static/tracking") if f.endswith(".html")]

    return render_template("tracking.html", graphs=tracking_graphs)

@app.route("/activity/<activity_id>")
def activity_details(activity_id):
    """Affiche les détails d'une activité."""
    activity = next((a for a in activity_manager.activities if str(a.get("activityId")) == str(activity_id)), None)
    details = activity_manager.details.get("activities", {}).get(str(activity_id), {}).get("details", {})

    if not activity or not details:
        flash("Détails de l'activité introuvables.", "error")
        return redirect(url_for("activity"))

    # Générer la page statique
    page_file = activity_page_manager.generate_activity_page(activity, details)
    if page_file:
        print(f"Redirection vers : {page_file}")
        return redirect(page_file)  # Redirige vers le chemin généré
    else:
        flash("Erreur lors de la génération de la page.", "error")
        return redirect(url_for("activity"))
   
    
if __name__ == "__main__":
    app.run(debug=True)
