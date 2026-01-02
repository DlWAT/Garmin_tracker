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
- `GARMIN_TRACKER_MODE`: `local` (défaut) ou `prod` (déploiement/public IP)
- `GARMIN_TRACKER_HOST`: host d'écoute (ex: `127.0.0.1` en local, `0.0.0.0` en déploiement)
- `GARMIN_TRACKER_PORT`: port d'écoute (défaut `5000`)
- `GARMIN_TRACKER_URL_PREFIX`: préfixe d'URL (défaut `/mytrainer`)

## Deux modes : local vs déploiement

### Mode local (PC)

Par défaut, `python app.py` démarre en mode **local**:
- bind sur `127.0.0.1:5000`
- debug activé

### Mode déploiement (serveur avec IP publique)

Objectif: rendre le site accessible depuis l'extérieur.

1) Préparer le serveur (Linux typiquement)
- ouvrir le port choisi (ex: `5000`) dans le firewall / security group
- cloner le repo, créer un venv, installer les deps

2) Lancer en prod (recommandé): Waitress (WSGI)

```bash
pip install -r requirements.txt
export FLASK_SECRET_KEY='change_me_long_random'
# recommandé si tu passes par nginx (cohabitation /mytrainer):
waitress-serve --host 127.0.0.1 --port 5001 wsgi:app
```

3) Alternative (simple): via `app.py` en mode `prod`

```bash
export GARMIN_TRACKER_MODE=prod
export FLASK_SECRET_KEY='change_me_long_random'
python app.py
```

Option "direct" (sans nginx):

```bash
export FLASK_SECRET_KEY='change_me_long_random'
waitress-serve --host 0.0.0.0 --port 5000 wsgi:app
```

Notes:
- Pour un vrai déploiement "propre", l'idéal est d'ajouter un reverse proxy (Nginx/Caddy) devant (HTTPS + port 80/443), puis de garder Waitress sur un port interne.

### Déploiement auto-start (Linux) avec systemd

Fichiers fournis:
- `deploy/garmin-tracker.service`
- `deploy/garmin-tracker.env.example`

Exemple d'installation (à adapter aux chemins):

1) Installer l'app dans `/opt/garmin-tracker/app`

```bash
sudo mkdir -p /opt/garmin-tracker
sudo chown -R $USER:$USER /opt/garmin-tracker
cd /opt/garmin-tracker
git clone <ton_repo_git> app
cd app
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

2) Créer le fichier d'env (secrets + config)

```bash
sudo cp deploy/garmin-tracker.env.example /etc/garmin-tracker.env
sudo nano /etc/garmin-tracker.env
```

3) Installer et activer le service

```bash
sudo cp deploy/garmin-tracker.service /etc/systemd/system/garmin-tracker.service
sudo systemctl daemon-reload
sudo systemctl enable --now garmin-tracker
sudo systemctl status garmin-tracker
```

Logs:

```bash
sudo journalctl -u garmin-tracker -f
```

Mise à jour:

```bash
cd /opt/garmin-tracker/app
git pull
./.venv/bin/pip install -r requirements.txt
sudo systemctl restart garmin-tracker
```

Ou (recommandé) en une commande via le script fourni (à exécuter sur le VPS):

```bash
bash deploy/deploy_vps.sh
```

### Coexister avec PolyTalk (même IP) via Nginx

But: garder PolyTalk et Garmin Tracker accessibles en même temps sur la même IP, chacun sous son préfixe.

1) Fais tourner Garmin Tracker en **interne** (localhost) sur un port libre (ex: `5001`).
	Exemple dans `/etc/garmin-tracker.env` (voir aussi `deploy/garmin-tracker.env.example`):

```bash
GARMIN_TRACKER_HOST=127.0.0.1
GARMIN_TRACKER_PORT=5001
GARMIN_TRACKER_URL_PREFIX=/mytrainer
```

2) Ajoute un `location` Nginx pour `/mytrainer/` dans le même `server {}` que PolyTalk.
	Un snippet prêt à copier est fourni: `deploy/nginx_mytrainer_location.conf`.

Snippet (à mettre dans ton `server {}`):

```nginx
location = /mytrainer { return 301 /mytrainer/; }

location /mytrainer/ {
	 proxy_pass http://127.0.0.1:5001;
	 proxy_http_version 1.1;

	 proxy_set_header Host $host;
	 proxy_set_header X-Forwarded-Proto $scheme;
	 proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

3) Reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Ensuite Garmin Tracker est accessible sur `http://IP/mytrainer/`.

## Fichiers importants

- `app.py`: point d’entrée recommandé
- `garmin_tracker/webapp.py`: application Flask (routes + login)
- `garmin_client_manager.py`: récupération Garmin + sauvegarde des JSON dans `data/`

## Données locales (non versionnées)

Pour éviter de pousser des données personnelles, ces éléments sont ignorés par git (voir `.gitignore`) :
- `data/` (exports Garmin, trainings/competitions locaux)
- `garmin_data/`, `output_graphs_*` et les HTML générés dans `static/activity/`, `static/health/`, `static/dashboard/`
- `instance/` (dont `instance/secret_key`)

