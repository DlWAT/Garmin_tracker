import json
import matplotlib.pyplot as plt
import pandas as pd
from shapely.geometry import Point, LineString
import geopandas as gpd
import contextily as ctx
import os


def load_activity_details(file_path):
    """Charge les données d'activité depuis un fichier JSON."""
    with open(file_path, "r") as f:
        data = json.load(f)
    return data


def map_metrics_to_dataframe(activity_data):
    """
    Crée un DataFrame en mappant les métriques (indices) aux valeurs.
    """
    metric_descriptors = activity_data.get("metricDescriptors", [])
    metric_names = {desc["metricsIndex"]: desc["key"] for desc in metric_descriptors}

    detail_metrics = activity_data.get("activityDetailMetrics", [])
    metrics_data = [entry["metrics"] for entry in detail_metrics]

    df = pd.DataFrame(metrics_data, columns=[metric_names.get(i, f"unknown_{i}") for i in range(len(metrics_data[0]))])
    return df


def create_map(activity_data, df, output_file="map.png"):
    """Crée une carte avec le parcours tracé en rouge."""
    # Extraire les coordonnées
    points = [
        Point(lon, lat)
        for lon, lat in zip(df["directLongitude"], df["directLatitude"])
        if pd.notnull(lon) and pd.notnull(lat)
    ]

    if not points:
        print("Aucun point valide trouvé pour tracer le parcours. Vérifiez les données.")
        return

    # Créer une ligne à partir des points
    line = LineString(points)

    # Créer un GeoDataFrame pour afficher la carte
    gdf = gpd.GeoDataFrame([{"geometry": line}], crs="EPSG:4326")
    gdf = gdf.to_crs(epsg=3857)  # Convertir au système de coordonnées Web Mercator

    # Tracer la carte avec un fond OpenStreetMap
    fig, ax = plt.subplots(figsize=(10, 6))
    gdf.plot(ax=ax, color="red", linewidth=2)
    ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)  # Ajouter le fond de carte
    ax.axis("off")  # Retirer les axes
    plt.tight_layout()

    # Enregistrer la carte
    plt.savefig(output_file, bbox_inches="tight")
    plt.close()
    print(f"Carte enregistrée sous {output_file}")


def create_graphs(df, activity_data, output_folder="graphs/activity_details"):
    """Crée des graphiques pour chaque métrique disponible."""
    os.makedirs(output_folder, exist_ok=True)

    # Extraire les descriptions des métriques
    metric_descriptors = activity_data.get("metricDescriptors", [])

    for descriptor in metric_descriptors:
        key = descriptor["key"]
        unit = descriptor["unit"]["key"]

        if key in df.columns:
            plt.figure(figsize=(12, 6))
            plt.plot(df.index, df[key], marker="o", linestyle="-", linewidth=2, alpha=0.8)
            plt.title(f"{key} ({unit})", fontsize=16, fontweight="bold")
            plt.xlabel("Index (temps)", fontsize=14)
            plt.ylabel(f"{key} ({unit})", fontsize=14)
            plt.grid(visible=True, linestyle="--", alpha=0.6)
            plt.xticks(fontsize=12)
            plt.yticks(fontsize=12)

            # Enregistrer le graphique
            output_file = f"{output_folder}/{key}.png"
            plt.savefig(output_file, bbox_inches="tight")
            plt.close()
            print(f"Graphique pour {key} enregistré sous {output_file}")
        else:
            print(f"Clé {key} absente dans les données.")


def main():
    # Charger les données d'activité
    activity_details_file = "garmin_data/activity_details.json"
    activity_data = load_activity_details(activity_details_file)

    # Mapper les métriques dans un DataFrame
    df = map_metrics_to_dataframe(activity_data)

    # Aperçu des données
    print("\n--- Aperçu des données mappées ---")
    print(df.head())

    # Créer une carte
    create_map(activity_data, df, output_file="map.png")

    # Créer des graphiques
    create_graphs(df, activity_data, output_folder="graphs")


if __name__ == "__main__":
    main()
