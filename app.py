import os
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, redirect, render_template
from garmin.client import GarminClient
from garmin.data_handler import load_data, save_data, fetch_health_data, fetch_activity_data
from garmin.plotting import generate_graphs

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST', 'GET'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        return redirect(f'/fetch_data?email={email}&password={password}')
    return render_template('login.html')

@app.route('/fetch_data')
def fetch_data():
    email = request.args.get('email')
    password = request.args.get('password')
    months = int(request.args.get('months', 1))

    today = datetime.today().date()
    date_n_months_ago = today - timedelta(days=months*30)

    garmin_client = GarminClient(email, password)
    
    existing_data = load_data('health_data.json')
    new_data = fetch_health_data(garmin_client, date_n_months_ago, today, existing_data)
    
    if new_data:
        save_data('health_data.json', existing_data + new_data)
    
    return redirect('/health_data')

@app.route('/health_data', methods=['GET'])
def health_data():
    # Charger les données (remplacez `load_health_data_somehow` par votre méthode)
    data = load_data()  # Exemple : chargement des données
    
    # Afficher les données pour diagnostic
    # logging.info("Données chargées pour health_data :")
    # logging.info(data)

    try:
        latest_data, graphs = generate_graphs(data)
    except Exception as e:
        logging.error(f"Erreur lors de la génération des graphiques : {e}")
        return "Erreur lors de la génération des graphiques.", 500

    # Passer les données à un template HTML
    return render_template("health.html", latest_data=latest_data, graphs=graphs)

@app.route('/fetch_activities')
def fetch_activities():
    email = request.args.get('email')
    password = request.args.get('password')
    months = int(request.args.get('months', 18))

    today = datetime.today().date()
    date_n_months_ago = today - timedelta(days=months*30)

    garmin_client = GarminClient(email, password)
    
    existing_data = load_data('activities_data.json')
    new_data = fetch_activity_data(garmin_client, date_n_months_ago, existing_data)
    
    if new_data:
        save_data('activities_data.json', existing_data + new_data)
    
    return redirect('/activities')

@app.route('/activities')
def activities():
    data = load_data('activities_data.json')
    
    if not data:
        return "No activities available", 500
    
    latest_data, stats = generate_graphs(data)
    
    return render_template('activities.html', stats=stats)


if __name__ == '__main__':
    app.run(debug=True)
