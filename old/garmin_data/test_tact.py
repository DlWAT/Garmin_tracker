import os
import json
import matplotlib.pyplot as plt
from datetime import datetime
import pandas as pd
from scipy.stats import norm
import numpy as np

# Charger les données
def load_activities(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [activity for activity in data if activity["activityType"]["typeKey"] in ["running", "treadmill_running"]]

# Formater la date pour l'affichage
def format_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")

# Calculer l'allure (pace) en min:sec/km
def calculate_pace(activity):
    if activity["distance"] > 0:
        pace = activity["duration"] / (activity["distance"] / 1000)
        minutes = int(pace // 60)
        seconds = int(pace % 60)
        return f"{minutes}:{seconds:02d}", pace / 60
    return None, None

# Estimer la distance pour les tapis roulants si nécessaire
def estimate_distance(activity):
    return activity["distance"] if activity["distance"] > 0 else activity["steps"] * 0.75  # Approximatif : 0.75m par pas

# Formater la durée en h:min:sec
def format_duration(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"

# Annoter les points des graphes
def annotate_points(ax, x, y, annotations):
    for i, txt in enumerate(annotations):
        if y[i] is not None:
            ax.annotate(
                txt, (x[i], y[i]),
                textcoords="offset points",
                xytext=(0, 10 if i % 2 == 0 else -10),  # Alterne entre au-dessus et en dessous
                ha="center",
                fontsize=10,
                color="black"
            )

# Créer les graphes
import matplotlib.dates as mdates
from datetime import datetime, timedelta

def rolling_average_with_confidence(x_dates, y_values, window_days=14):
    # Convertir les données en DataFrame pour utiliser le rolling
    df = pd.DataFrame({"date": x_dates, "value": y_values})
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)

    # Moyennage glissant
    rolling_mean = df["value"].rolling(f"{window_days}D").mean()
    rolling_std = df["value"].rolling(f"{window_days}D").std()

    # Intervalle de confiance à 95 % (z=1.96)
    ci_upper = rolling_mean + 1.96 * rolling_std
    ci_lower = rolling_mean - 1.96 * rolling_std

    return rolling_mean, ci_lower, ci_upper

def create_graphs(activities, output_folder):
    available_metrics = {
        "duration": {"label": "Time (h:min:sec)", "color": "darkgreen", "formatter": format_duration},
        "distance": {"label": "Distance (km)", "color": "darkblue"},
        "pace": {"label": "Pace (min:sec/km)", "color": "darkmagenta"},
        "heartRate": {"label": "Heart Rate (bpm)", "color": ["darkred", "black"]},
    }

    all_dates = [datetime.strptime(activity["startTimeLocal"], "%Y-%m-%d %H:%M:%S") for activity in activities]

    for key, details in available_metrics.items():
        x = []
        y = []
        y_secondary = []
        annotations = []

        for activity in activities:
            activity_date = datetime.strptime(activity["startTimeLocal"], "%Y-%m-%d %H:%M:%S")
            x.append(activity_date)

            if key == "pace":
                pace, pace_float = calculate_pace(activity)
                if pace:
                    y.append(pace_float)
                    annotations.append(pace)
                else:
                    y.append(None)
            elif key == "distance":
                y.append(estimate_distance(activity) / 1000)  # Distance en km
                annotations.append(f"{y[-1]:.2f} km")
            elif key == "duration":
                y.append(activity.get("duration", 0))
                annotations.append(format_duration(activity.get("duration", 0)))
            elif key == "heartRate":
                y.append(activity.get("averageHR", None))
                y_secondary.append(activity.get("maxHR", None))
                annotations.append(f"{y[-1] if y[-1] else ''} bpm")
            else:
                y.append(activity.get(key, None))
                annotations.append(f"{y[-1]}" if y[-1] is not None else "")

        if all(val is None for val in y):
            print(f"Skipping {key} (no data available).")
            continue

        sorted_data = sorted(
            zip(x, y, annotations, y_secondary if key == "heartRate" else [None] * len(x)),
            key=lambda d: d[0],
        )
        if not sorted_data:
            print(f"No valid data to plot for {key}. Skipping.")
            continue

        x, y, annotations, y_secondary = zip(*sorted_data)

        # Calculer la moyenne glissante et l'intervalle de confiance
        rolling_mean, ci_lower, ci_upper = rolling_average_with_confidence(x, y)

        plt.figure(figsize=(14, 8))
        ax = plt.gca()

        if key == "heartRate":
            ax.plot(x, y, marker="o", linestyle="-", color=details["color"][0], label="Average Heart Rate")
            ax.plot(x, y_secondary, marker="o", linestyle="--", color=details["color"][1], label="Max Heart Rate")
            plt.legend(fontsize=12)
            annotate_points(ax, x, y, [f"{v} bpm" for v in y])
            annotate_points(ax, x, y_secondary, [f"{v} bpm" for v in y_secondary])
        else:
            ax.plot(x, y, marker="o", linestyle="-", color=details["color"], label="Original Data")
            annotate_points(ax, x, y, annotations)

        # Ajouter la courbe lissée
        ax.plot(rolling_mean.index, rolling_mean.values, linestyle="--", color="blue", label="Rolling Mean (2 weeks)")
        ax.fill_between(
            rolling_mean.index,
            ci_lower,
            ci_upper,
            color="blue",
            alpha=0.2,
            label="95% Confidence Interval",
        )

        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        plt.xticks(rotation=45, fontsize=12)

        plt.ylabel(details["label"], fontsize=14, labelpad=10)
        plt.xlabel("Date", fontsize=14, labelpad=10)
        plt.title(f"{details['label']} Over Time (Running Activities)", fontsize=16, pad=20)
        plt.grid(visible=True, linestyle="--", alpha=0.7)

        if key == "pace":
            ax.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda v, _: f"{int(v // 1)}:{int((v % 1) * 60):02d}")
            )
            ax.invert_yaxis()

        if key == "duration":
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: format_duration(v)))

        plt.tight_layout()

        output_file = os.path.join(output_folder, f"{key}_graph_running.png")
        plt.savefig(output_file, dpi=300, bbox_inches="tight")
        plt.close()
        print(f"Graph saved: {output_file}")


# Main
if __name__ == "__main__":
    input_file = "garmin_data/activities.json"  # Chemin vers le fichier JSON
    output_folder = "graphs/activities"  # Dossier de sortie
    os.makedirs(output_folder, exist_ok=True)

    activities = load_activities(input_file)
    create_graphs(activities, output_folder)
