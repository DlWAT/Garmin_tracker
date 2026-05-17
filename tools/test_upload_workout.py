"""Script de debug : upload d'un entraînement test vers Garmin Connect.

Usage (depuis la racine du projet) :
    .venv312/Scripts/python.exe tools/test_upload_workout.py

Le script demande l'email et le mot de passe Garmin de manière interactive,
construit un workout de test, l'uploade et optionnellement le planifie.
"""
from __future__ import annotations

import getpass
import json
import logging
import sys
import os

# ── Ajoute la racine du projet au path pour importer garmin_tracker ──────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_upload_workout")

# ── Imports projet ────────────────────────────────────────────────────────────
try:
    from garminconnect import Garmin
except ImportError:
    sys.exit(
        "ERREUR : garminconnect n'est pas installé dans cet environnement.\n"
        "Lance le script avec : .venv312\\Scripts\\python.exe tools\\test_upload_workout.py"
    )

try:
    from garmin_tracker.garmin_workout_sender import (
        send_training_to_garmin,
        schedule_workout_on_garmin,
        training_to_workout_payload,
        GarminWorkoutSendError,
    )
except ImportError as e:
    sys.exit(f"ERREUR import garmin_tracker : {e}\nAssure-toi de lancer depuis la racine du projet.")


# ── Entraînement de test ──────────────────────────────────────────────────────
TEST_TRAINING = {
    "id": "test-debug-001",
    "title": "[TEST DEBUG] Fractionné 5x400m avec allures",
    "sport": "running",
    "date": "",          # rempli interactivement si besoin
    "duration_min": 45,
    "distance_km": 8.0,
    "description": "Workout de test uploadé par le script de debug.",
    "content": "- 15' EF @5:30/km\n- 5x400m (r=1'30) @4:00/km\n- 10' RAC @5:30/km",
}


def _ask(prompt: str, default: str = "") -> str:
    val = input(f"{prompt} [{default}]: ").strip() if default else input(f"{prompt}: ").strip()
    return val or default


def main() -> None:
    print("=" * 60)
    print("  Test upload workout → Garmin Connect")
    print("=" * 60)

    # ── Credentials ──────────────────────────────────────────────────────────
    email = _ask("Email Garmin Connect")
    password = getpass.getpass("Mot de passe Garmin Connect : ")

    if not email or not password:
        sys.exit("Email ou mot de passe manquant.")

    # ── Affiche le payload qui sera envoyé ───────────────────────────────────
    print("\n--- Payload workout (avant upload) ---")
    try:
        payload = training_to_workout_payload(TEST_TRAINING)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"ERREUR lors de la construction du payload : {e}")
        sys.exit(1)

    # ── Connexion Garmin ──────────────────────────────────────────────────────
    print("\n--- Connexion à Garmin Connect ---")
    try:
        client = Garmin(email, password)
        client.login()
        print("Connexion réussie.")
    except Exception as e:
        print(f"ERREUR de connexion : {type(e).__name__}: {e}")
        sys.exit(1)

    # ── Vérifie que upload_workout est disponible ─────────────────────────────
    print("\n--- Vérification des méthodes disponibles ---")
    workout_methods = [m for m in dir(client) if "workout" in m.lower()]
    print("Méthodes workout :", workout_methods)
    if not callable(getattr(client, "upload_workout", None)):
        print("ERREUR : upload_workout absent de cette version de garminconnect !")
        sys.exit(1)
    print("upload_workout : OK")

    # ── Upload ────────────────────────────────────────────────────────────────
    print("\n--- Upload du workout ---")
    try:
        workout_id = send_training_to_garmin(client, TEST_TRAINING)
        print(f"Workout uploadé avec succès. ID : {workout_id}")
    except GarminWorkoutSendError as e:
        print(f"ERREUR GarminWorkoutSendError : {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERREUR inattendue : {type(e).__name__}: {e}")
        sys.exit(1)

    # ── Planification optionnelle ─────────────────────────────────────────────
    print()
    date = _ask("Date de planification (AAAA-MM-JJ, laisser vide pour ignorer)", "")
    if date:
        print(f"\n--- Planification du workout {workout_id} le {date} ---")
        try:
            schedule_workout_on_garmin(client, workout_id, date)
            print(f"Workout planifié le {date}.")
        except GarminWorkoutSendError as e:
            print(f"ERREUR lors de la planification : {e}")
    else:
        print("Planification ignorée.")

    print("\n--- Résumé ---")
    print(f"  Workout ID : {workout_id}")
    print(f"  Titre      : {TEST_TRAINING['title']}")
    print(f"  Lien       : https://connect.garmin.com/modern/workout/{workout_id}")
    print("\nTerminé.")


if __name__ == "__main__":
    main()
