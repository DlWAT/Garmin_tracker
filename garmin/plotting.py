import matplotlib.pyplot as plt
import pandas as pd
import logging

def generate_graphs(data):
    # Vérifiez le type des données et affichez-les pour déboguer
    if not isinstance(data, list):
        logging.error("Les données reçues ne sont pas une liste. Voici leur contenu :")
        logging.error(data)  # Affiche les données brutes pour inspection
        raise ValueError("Les données fournies doivent être une liste de dictionnaires.")

    if not all(isinstance(item, dict) for item in data):
        logging.error("Les données ne contiennent pas uniquement des dictionnaires. Voici un échantillon :")
        for item in data[:5]:  # Affiche jusqu'à 5 premiers éléments
            logging.error(f"Type: {type(item)}, Valeur: {item}")
        raise ValueError("Les données fournies doivent être une liste de dictionnaires.")

    # Vérifiez la présence des clés requises
    required_keys = ['calendarDate', 'totalKilocalories']
    for index, item in enumerate(data):
        for key in required_keys:
            if key not in item:
                logging.warning(f"Clé manquante : '{key}' dans l'élément {index + 1}. Élément : {item}")

    # Filtrer les données valides
    valid_data = [item for item in data if all(key in item for key in required_keys)]

    if not valid_data:
        logging.error("Aucune donnée valide disponible après filtrage. Voici les données initiales :")
        for index, item in enumerate(data[:5]):  # Affiche jusqu'à 5 premiers éléments
            logging.error(f"Élément {index + 1}: {item}")
        raise KeyError("Les données valides manquent les clés requises.")

    # Convertir les données en DataFrame
    df = pd.DataFrame(valid_data)

    # Convertir 'calendarDate' en datetime
    try:
        df['calendarDate'] = pd.to_datetime(df['calendarDate'])
    except Exception as e:
        raise ValueError(f"Erreur lors de la conversion de 'calendarDate' en datetime : {e}")

    # Trier les données par 'calendarDate'
    df = df.sort_values('calendarDate')

    # Sélectionner les dernières données
    latest_data = df.iloc[-1].to_dict()

    # Définir les graphiques à générer
    graphs = [
        {
            'title': 'Calories brûlées sur les 12 derniers mois',
            'ylabel': 'Calories',
            'x': 'calendarDate',
            'y': 'totalKilocalories',
            'file': 'calories_graph.png'
        },
        # Ajoutez d'autres graphiques ici...
    ]

    # Créer les graphiques
    for graph in graphs:
        if graph['y'] not in df.columns:
            logging.warning(f"La colonne '{graph['y']}' est manquante. Graphique '{graph['title']}' ignoré.")
            continue

        plt.figure(figsize=(10, 5))
        plt.plot(
            df[graph['x']], df[graph['y']],
            marker='o', color='teal', linewidth=0.8, markersize=3
        )
        plt.title(graph['title'])
        plt.xlabel('Date')
        plt.ylabel(graph['ylabel'])
        plt.xticks(rotation=45)
        plt.grid(True, which='both', linestyle='--', linewidth=0.5)
        # Sauvegarder le graphique
        file_path = f"static/graphs/{graph['file']}"
        plt.savefig(file_path)
        plt.close()

    return latest_data, graphs
