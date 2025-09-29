import os
import folium
from jinja2 import Template
import plotly.graph_objects as go


class ActivityPageManager:
    def __init__(self, output_dir="static/activity_pages"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs("static/graphs", exist_ok=True)

    def generate_activity_map(self, activity_id, details):
        """Génère une carte interactive à partir des coordonnées GPS."""
        try:
            metric_descriptors = details.get("metricDescriptors", [])
            activity_metrics = details.get("activityDetailMetrics", [])

            lat_index = lon_index = None
            for metric in metric_descriptors:
                if metric.get("key") == "directLatitude":
                    lat_index = metric["metricsIndex"]
                elif metric.get("key") == "directLongitude":
                    lon_index = metric["metricsIndex"]

            path = []
            for point in activity_metrics:
                metrics = point.get("metrics", [])
                if len(metrics) > max(lat_index, lon_index):
                    latitude = metrics[lat_index]
                    longitude = metrics[lon_index]
                    if latitude and longitude:
                        path.append((latitude, longitude))

            if not path:
                return None

            map_file = os.path.join(self.output_dir, f"activity_{activity_id}_map.html").replace("\\", "/")
            m = folium.Map(location=path[0], zoom_start=14)
            folium.PolyLine(path, color="blue", weight=2.5).add_to(m)
            m.save(map_file)
            return f"/{map_file}"
        except Exception as e:
            print(f"Erreur lors de la création de la carte : {e}")
            return None

    def generate_graphs(self, activity_id, details):
        """Génère les graphiques pour allure, fréquence cardiaque et cadence."""
        graph_files = []

        # Extraire les indices pour chaque métrique
        metric_descriptors = details.get("metricDescriptors", [])
        activity_metrics = details.get("activityDetailMetrics", [])

        hr_index = speed_index = cadence_index = None
        for metric in metric_descriptors:
            if metric.get("key") == "directHeartRate":
                hr_index = metric["metricsIndex"]
            elif metric.get("key") == "directSpeed":
                speed_index = metric["metricsIndex"]
            elif metric.get("key") == "directRunCadence":
                cadence_index = metric["metricsIndex"]

        hr_values, pace_values, cadence_values = [], [], []
        hr_zones = {"Zone 1": 0, "Zone 2": 0, "Zone 3": 0, "Zone 4": 0}

        for point in activity_metrics:
            metrics = point.get("metrics", [])
            # Fréquence cardiaque
            if hr_index is not None and len(metrics) > hr_index:
                hr = metrics[hr_index]
                if hr:
                    hr_values.append(hr)
                    if hr < 120:
                        hr_zones["Zone 1"] += 1
                    elif 120 <= hr < 140:
                        hr_zones["Zone 2"] += 1
                    elif 140 <= hr < 160:
                        hr_zones["Zone 3"] += 1
                    else:
                        hr_zones["Zone 4"] += 1

            # Allure
            if speed_index is not None and len(metrics) > speed_index:
                speed = metrics[speed_index]
                if speed > 0:
                    pace = 1000 / speed / 60  # Conversion en min/km
                    pace_values.append(round(pace, 2))

            # Cadence
            if cadence_index is not None and len(metrics) > cadence_index:
                cadence = metrics[cadence_index]
                if cadence:
                    cadence_values.append(cadence*2)

        # Graphe des zones FC
        fig_zones = go.Figure()
        fig_zones.add_trace(go.Bar(
            x=list(hr_zones.keys()),
            y=list(hr_zones.values()),
            marker=dict(color=['darkblue', 'darkgreen', 'darkgoldenrod', 'darkred'])
        ))
        fig_zones.update_layout(
            title="Temps passé dans chaque zone de FC",
            xaxis_title="Zones FC",
            yaxis_title="Temps (secondes)",
            template="plotly_dark",
            height=500
        )
        zones_file = f"static/graphs/{activity_id}_hr_zones.html"
        fig_zones.write_html(zones_file)
        graph_files.append(f"/{zones_file}")

        # Graphe FC avec zones
        if hr_values:
            fig_hr = go.Figure()
            fig_hr.add_trace(go.Scatter(y=hr_values, mode="lines", line=dict(color="red"), name="FC"))
            for y0, y1, color, label in [(0, 120, "blue", "Zone 1"), (120, 140, "green", "Zone 2"),
                                         (140, 160, "yellow", "Zone 3"), (160, 200, "red", "Zone 4")]:
                fig_hr.add_shape(
                    type="rect", x0=0, x1=len(hr_values), y0=y0, y1=y1,
                    fillcolor=color, opacity=0.2, line_width=0
                )
            fig_hr.update_layout(
                title="Fréquence Cardiaque avec Zones",
                xaxis_title="Temps",
                yaxis_title="BPM",
                template="plotly_dark",
                height=500
            )
            hr_file = f"static/graphs/{activity_id}_hr.html"
            fig_hr.write_html(hr_file)
            graph_files.append(f"/{hr_file}")

        # Graphe Allure
        if pace_values:
            fig_pace = go.Figure()
            fig_pace.add_trace(go.Scatter(y=pace_values, mode="lines", line=dict(color="blue"), name="Allure (min/km)"))
            fig_pace.update_layout(
                title="Allure au fil du temps",
                xaxis_title="Temps",
                yaxis_title="Allure (min/km)",
                template="plotly_dark",
                height=500
            )
            pace_file = f"static/graphs/{activity_id}_pace.html"
            fig_pace.write_html(pace_file)
            graph_files.append(f"/{pace_file}")

        # Graphe Cadence
        if cadence_values:
            fig_cadence = go.Figure()
            fig_cadence.add_trace(go.Scatter(y=cadence_values, mode="lines", line=dict(color="purple"), name="Cadence (steps/min)"))
            fig_cadence.update_layout(
                title="Cadence au fil du temps",
                xaxis_title="Temps",
                yaxis_title="Steps/min",
                template="plotly_dark",
                height=500
            )
            cadence_file = f"static/graphs/{activity_id}_cadence.html"
            fig_cadence.write_html(cadence_file)
            graph_files.append(f"/{cadence_file}")

        return graph_files

    def generate_activity_page(self, activity, details):
        """Génère une page HTML complète avec la carte, le résumé et les graphiques."""
        activity_id = activity.get("activityId")
        map_file = self.generate_activity_map(activity_id, details)
        graph_files = self.generate_graphs(activity_id, details)

        # Données du résumé
        distance = round(activity.get("distance", 0) / 1000, 2)
        duration = round(activity.get("duration", 0) / 60, 2)
        avg_hr = sum(activity.get("avgHR", [0])) // len(activity.get("avgHR", [1]))
        avg_pace = "N/A"
        if distance > 0 and duration > 0:
            avg_pace = f"{int(duration // distance)}:{int((duration % distance) * 60 // distance):02d} min/km"

        template_str = """
        <!DOCTYPE html>
        <html lang="fr">
        <head>
            <meta charset="UTF-8">
            <title>Analyse de l'Activité {{ activityId }}</title>
            <style>
                /* Styles généraux */
                body {
                    font-family: Arial, sans-serif;
                    background: #1E1E2F;
                    color: #FFF;
                    margin: 0;
                    line-height: 1.6;
                }
                h1, h2 {
                    text-align: center;
                    color: #61DAFB;
                    margin-bottom: 20px;
                }
                /* Conteneurs principaux */
                .container {
                    display: flex;
                    max-width: 1200px;
                    margin: 20px auto;
                    gap: 20px;
                }
                .map {
                    flex: 1;
                    background-color: #2C313C;
                    padding: 15px;
                    border-radius: 8px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.5);
                }
                .stats {
                    flex: 1;
                    background-color: #2C313C;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.5);
                }
                .stats ul {
                    list-style: none;
                    padding: 0;
                }
                .stats li {
                    margin-bottom: 10px;
                    font-size: 1rem;
                    color: #BBB;
                }
                .stats li strong {
                    color: #FFF;
                }
                /* Graphiques */
                .graphs {
                    max-width: 1200px;
                    margin: auto;
                }
                .graphs h2 {
                    text-align: center;
                }
                .grid-container {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 20px;
                }
                .grid-container iframe {
                    width: 100%;
                    height: 500px;
                    border: none;
                    border-radius: 8px;
                    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.6);
                }
                /* Bouton retour */
                a.button {
                    display: block;
                    text-align: center;
                    background: #61DAFB;
                    color: #1E1E2F;
                    font-weight: bold;
                    padding: 10px 20px;
                    border-radius: 5px;
                    text-decoration: none;
                    margin: 20px auto;
                    width: 200px;
                }
                a.button:hover {
                    background: #529EC4;
                }
            </style>
        </head>
        <body>
            <h1>Analyse de l'Activité {{ activityId }}</h1>
            <a href="/activity" class="button">Retour aux Activités</a>

            <!-- Conteneur principal : Carte et Résumé -->
            <div class="container">
                <div class="map">
                    <h2>Carte du Parcours</h2>
                    {% if map_file %}
                        <iframe src="{{ map_file }}"></iframe>
                    {% else %}
                        <p>Aucune carte disponible</p>
                    {% endif %}
                </div>
                <div class="stats">
                    <h2>Résumé de la Séance</h2>
                    <ul>
                        <li><strong>Distance :</strong> {{ distance }} km</li>
                        <li><strong>Durée :</strong> {{ duration }} minutes</li>
                        <li><strong>Allure Moyenne :</strong> {{ avg_pace }}</li>
                        <li><strong>FC Moyenne :</strong> {{ avg_hr }} BPM</li>
                    </ul>
                </div>
            </div>

            <!-- Graphiques -->
            <div class="graphs">
                <h2>Graphiques</h2>
                <div class="grid-container">
                    {% for graph in graphs %}
                        <iframe src="{{ graph }}"></iframe>
                    {% endfor %}
                </div>
            </div>
        </body>
        </html>
        """

        output_file = os.path.join(self.output_dir, f"activity_{activity_id}.html").replace("\\", "/")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(Template(template_str).render(
                activityId=activity_id,
                map_file=map_file,
                graphs=graph_files,
                distance=distance,
                duration=duration,
                avg_pace=avg_pace,
                avg_hr="N/A" #if not hr_values else round(sum(hr_values) / len(hr_values))
            ))
        return f"/{output_file}"
