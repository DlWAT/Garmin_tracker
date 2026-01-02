# Garmin Tracker

Petit dashboard Flask pour visualiser activités, santé et calendrier d'entraînement à partir de Garmin Connect.

## Démarrage rapide (Windows)

1. Créer un venv

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Installer les dépendances

```powershell
pip install -r requirements.txt
```

3. Lancer l'app

```powershell
python app.py
```

Puis ouvrir http://127.0.0.1:5000/

## Lancer en tâche de fond (Windows)

L'app est un serveur Flask. Pour la lancer "en tâche de fond" (sans garder une fenêtre ouverte), le plus simple est d'utiliser les scripts PowerShell dans `tools/`.

### Démarrer en tâche de fond

```powershell
./tools/run_background.ps1
```

Ça démarre le serveur sur `http://127.0.0.1:5000` (fenêtre cachée).

### Stopper

```powershell
./tools/stop_background.ps1
```

### Redémarrer après une mise à jour

Quand tu fais une mise à jour (ex: `git pull`), tu peux simplement redémarrer le serveur :

```powershell
git pull
./tools/restart_background.ps1
```

Notes:
- En mode debug, Flask peut redémarrer tout seul quand des fichiers `.py`/templates changent, mais après un `git pull` (ou une mise à jour de dépendances) un restart manuel reste le plus fiable.
- Si tu changes `requirements.txt`, fais aussi `pip install -r requirements.txt` avant de redémarrer.

## Page de garde (login)

- La page d'accueil `/` demande vos identifiants Garmin.
- Les identifiants ne sont pas stockés dans le cookie: seul un token de session est conservé côté navigateur.
- Les identifiants sont gardés en mémoire côté serveur (perdus au redémarrage).

## Variables d'environnement

- `FLASK_SECRET_KEY`: clé Flask (recommandé en prod). Sinon une clé est créée dans `instance/secret_key`.

## Fichiers importants

- `app.py`: point d’entrée recommandé
- `garmin_tracker/webapp.py`: application Flask (routes + login)
- `garmin_client_manager.py`: récupération Garmin + sauvegarde des JSON dans `data/`

## Données locales (non versionnées)

Pour éviter de pousser des données personnelles, ces éléments sont ignorés par git (voir `.gitignore`) :
- `data/` (exports Garmin, trainings/competitions locaux)
- `garmin_data/`, `output_graphs_*` et les HTML générés dans `static/activity/`, `static/health/`, `static/dashboard/`
- `instance/` (dont `instance/secret_key`)

