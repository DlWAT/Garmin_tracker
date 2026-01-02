"""Legacy entrypoint.

Le serveur Flask vit désormais dans garmin_tracker.webapp.
Ce fichier reste pour compatibilité (python main.py).
"""

from garmin_tracker.webapp import app


if __name__ == "__main__":
    app.run(debug=True)
