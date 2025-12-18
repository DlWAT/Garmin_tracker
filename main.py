# main.py
import os
import re
import json
import logging
import unicodedata
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify

# ── config externe : app_settings.py (NE PAS versionner) ───────────────────────
try:
    import app_settings as cfg
except ImportError:
    cfg = None

USER_ID = os.getenv("GARMIN_USER_ID")  or (getattr(cfg, "USER_ID",  "Adri") if cfg else "Adri")
EMAIL   = os.getenv("GARMIN_EMAIL")    or (getattr(cfg, "EMAIL",    None)   if cfg else None)
PASSWORD= os.getenv("GARMIN_PASSWORD") or (getattr(cfg, "PASSWORD", None)   if cfg else None)

# ── imports projet ────────────────────────────────────────────────────────────
from garmin_activity_manager import GarminActivityManager
from garmin_health_manager   import GarminHealthManager
from training_analysis       import TrainingAnalysis
from garmin_client_manager   import GarminClientHandler
from activity_page_manager   import ActivityPageManager   # __init__(output_dir="...")

# ── app & logs ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.urandom(24)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ── dossiers requis ──────────────────────────────────────────────────────────
for folder in [
    "static/activity", "static/health", "static/training", "static/tracking",
    "static/activity_pages", "static/graphs", "static/maps", "data"
]:
    os.makedirs(folder, exist_ok=True)

# ── managers globaux ─────────────────────────────────────────────────────────
activity_manager  = GarminActivityManager(USER_ID)
health_manager    = GarminHealthManager(USER_ID)
activity_page_mgr = ActivityPageManager()   # pas d'arg → utilise static/activity_pages
# TrainingAnalysis est (ré)instancié quand nécessaire


# ── Helpers : index robuste des DÉTAILS (tous formats) ───────────────────────
AGG_PATH          = os.path.join("data", f"{USER_ID}_activities.json")          # {"activities": {id: {"summary":..., "details":...}}}
DETAILS_AGG_PATH  = os.path.join("data", f"{USER_ID}_activity_details.json")    # {id: details}
UNIT_FILE_PATTERN = re.compile(r"^activity_(\d+)_details\.json$")

def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def _build_details_index():
    """
    Construit un dict {id: details} en combinant :
    - agrégé moderne (AGG_PATH)
    - ancien agrégé détails (DETAILS_AGG_PATH)
    - activity_manager.details (si dict)
    - fichiers unitaires data/activity_<id>_details.json
    """
    by_id = {}

    # 1) agrégé moderne
    agg = _load_json(AGG_PATH)
    if isinstance(agg, dict) and isinstance(agg.get("activities"), dict):
        for aid, pack in agg["activities"].items():
            det = (pack or {}).get("details")
            if det:
                by_id[str(aid)] = det

    # 2) ancien agrégé détails
    d_agg = _load_json(DETAILS_AGG_PATH)
    if isinstance(d_agg, dict):
        for aid, det in d_agg.items():
            by_id[str(aid)] = det

    # 3) mémoire du manager
    det_store = getattr(activity_manager, "details", None)
    if isinstance(det_store, dict):
        for aid, det in det_store.items():
            by_id[str(aid)] = det

    # 4) fichiers unitaires
    for fname in os.listdir("data"):
        m = UNIT_FILE_PATTERN.match(fname)
        if not m:
            continue
        aid = m.group(1)
        if aid in by_id:
            continue
        det = _load_json(os.path.join("data", fname))
        if det:
            by_id[aid] = det

    return by_id

def _load_agg_summary_dict():
    """
    Retourne un mapping {id: summary} le plus complet possible :
    - d'abord depuis l'agrégé moderne
    - sinon depuis la liste historique (si ton GarminActivityManager lit encore une liste)
    """
    out = {}

    # agrégé moderne
    agg = _load_json(AGG_PATH)
    if isinstance(agg, dict) and isinstance(agg.get("activities"), dict):
        for aid, pack in agg["activities"].items():
            summ = (pack or {}).get("summary")
            if summ:
                out[str(aid)] = summ

    # liste historique : activity_manager.activities peut être une liste
    if not out:
        if isinstance(activity_manager.activities, list):
            for a in activity_manager.activities:
                aid = a.get("activityId")
                if aid is not None:
                    out[str(aid)] = a

    return out

DETAILS_BY_ID   = _build_details_index()
SUMMARY_BY_ID   = _load_agg_summary_dict()

def get_details_for(aid: str):
    """Retourne les details pour un ID si disponibles localement (toutes sources confondues)."""
    return DETAILS_BY_ID.get(str(aid))

def get_summary_for(aid: str):
    """Retourne le résumé (summary) pour un ID, depuis agrégé ou liste."""
    return SUMMARY_BY_ID.get(str(aid))

def refresh_indexes_from_disk():
    """A appeler après une mise à jour / écriture disque."""
    global DETAILS_BY_ID, SUMMARY_BY_ID
    DETAILS_BY_ID = _build_details_index()
    SUMMARY_BY_ID = _load_agg_summary_dict()


# ── utilitaires divers ───────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# ── ROUTES ───────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("home.html")


# ── ACTIVITÉS ────────────────────────────────────────────────────────────────
@app.route("/activity")
def activity():
    # génère distance.html / duration.html / pace.html / average_hr.html
    activity_manager.plot_interactive_graphs("static/activity")
    activity_graphs = [
        f"/static/activity/{f}"
        for f in os.listdir("static/activity")
        if f.endswith(".html")
    ]

    # tableau simple
    formatted = []
    # source : préférer agrégé (plus fiable), sinon liste manager
    src = list(SUMMARY_BY_ID.values()) or activity_manager.activities or []
    for a in src:
        if not a:
            continue
        aid = a.get("activityId")
        date = a.get("startTimeLocal", "Date inconnue")
        dist_km = round((a.get("distance", 0) or 0) / 1000.0, 2)
        dur_s   = int(a.get("duration", 0) or 0)
        h, r    = divmod(dur_s, 3600)
        m, _    = divmod(r, 60)

        avg_pace = "N/A"
        if dist_km and dist_km > 0:
            pace = dur_s / dist_km
            pm, ps = int(pace // 60), int(pace % 60)
            avg_pace = f"{pm}m {ps:02d}s/km"

        formatted.append({
            "activityId": aid,
            "date": date,
            "distance": dist_km,
            "duration": f"{h}h {m:02d}m",
            "avg_pace": avg_pace,
        })

    return render_template("activity.html", graphs=activity_graphs, activities=formatted)


@app.route("/update_activity", methods=["GET", "POST"])
def update_activity():
    if not EMAIL or not PASSWORD:
        flash("Identifiants Garmin manquants. Renseigne app_settings.py ou des variables d’environnement.", "error")
        return redirect(url_for("activity"))

    try:
        handler = GarminClientHandler(EMAIL, PASSWORD, USER_ID)
        handler.login()
        handler.update_activity_data()  # écrit data/<USER>_activities.json (summary + details)
        # recharger en mémoire ce que lit ton manager + nos index
        if hasattr(activity_manager, "refresh_from_disk"):
            activity_manager.refresh_from_disk()
        refresh_indexes_from_disk()
        flash("Données d’activités mises à jour ✅", "success")
    except Exception as e:
        logging.exception("Erreur update_activity")
        flash(f"Erreur lors de la mise à jour : {e}", "error")
    return redirect(url_for("activity"))


# ── PAGE DÉTAIL ACTIVITÉ ─────────────────────────────────────────────────────
@app.route("/activity/<activity_id>")
def activity_details(activity_id):
    aid = str(activity_id)

    # résumé (depuis agrégé ou liste)
    activity = get_summary_for(aid)
    if not activity:
        # fallback: passer par la source brute du manager
        activity = next((a for a in activity_manager.activities
                         if str(a.get("activityId")) == aid), None)
        if activity:
            SUMMARY_BY_ID[aid] = activity

    if not activity:
        flash("Activité introuvable (résumé absent). Mets à jour les activités.", "error")
        return redirect(url_for("training"))

    # détails déjà en local ?
    details = get_details_for(aid)

    # sinon : tenter un fetch à la volée (si identifiants dispo)
    if not details and EMAIL and PASSWORD:
        try:
            handler = GarminClientHandler(EMAIL, PASSWORD, USER_ID)
            handler.login()
            fetched = handler.get_activity_details(int(aid))
            if fetched:
                # persister unitaire
                unit_path = os.path.join("data", f"activity_{aid}_details.json")
                _save_json(unit_path, fetched)
                # insérer dans agrégé moderne si présent
                agg = _load_json(AGG_PATH) or {"activities": {}}
                if isinstance(agg, dict):
                    agg.setdefault("activities", {})
                    if aid not in agg["activities"]:
                        agg["activities"][aid] = {"summary": activity, "details": fetched}
                    else:
                        agg["activities"][aid]["details"] = fetched
                    _save_json(AGG_PATH, agg)
                # refresh index
                refresh_indexes_from_disk()
                details = get_details_for(aid)
        except Exception as e:
            logging.exception("Fetch à la volée impossible")
            flash(f"Impossible de récupérer les détails à la volée : {e}", "error")

    if not details:
        date_hint = (activity.get("startTimeLocal") or "")[:10]
        logging.warning("Détails introuvables pour id=%s (date=%s) — vérifie %s / %s / fichiers unitaires",
                        aid, date_hint, AGG_PATH, DETAILS_AGG_PATH)
        flash("Détails de l'activité introuvables (pas encore téléchargés).", "error")
        return redirect(url_for("training"))

    # ok → générer/ouvrir la page
    page_file = activity_page_mgr.generate_activity_page(activity, details)
    if page_file:
        return redirect(page_file)

    flash("Erreur lors de la génération de la page d’activité.", "error")
    return redirect(url_for("training"))


# ── DEBUG : état de présence des détails ─────────────────────────────────────
@app.route("/debug/details/<activity_id>")
def debug_details(activity_id):
    aid = str(activity_id)
    agg = _load_json(AGG_PATH) or {}
    activities = agg.get("activities") if isinstance(agg, dict) else {}
    present = {
        "agg_file_exists": os.path.exists(AGG_PATH),
        "in_agg_summary": bool(isinstance(activities, dict) and aid in activities and (activities[aid] or {}).get("summary") is not None),
        "in_agg_details": bool(isinstance(activities, dict) and aid in activities and (activities[aid] or {}).get("details") is not None),
        "in_details_agg_file": aid in ((_load_json(DETAILS_AGG_PATH) or {}).keys() if isinstance(_load_json(DETAILS_AGG_PATH), dict) else []),
        "unit_file_exists": os.path.exists(os.path.join("data", f"activity_{aid}_details.json")),
        "indexed_now": aid in DETAILS_BY_ID,
        "has_summary_cache": aid in SUMMARY_BY_ID,
    }
    return jsonify(present)


# ── SANTÉ ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    health_manager.plot_interactive_graphs("static/health")
    health_graphs = [
        f"/static/health/{f}"
        for f in os.listdir("static/health")
        if f.endswith(".html")
    ]
    return render_template("health.html", graphs=health_graphs)

@app.route("/update_health", methods=["GET", "POST"])
def update_health():
    flash("La mise à jour santé n’est pas encore branchée.", "info")
    return redirect(url_for("health"))


# ── ENTRAÎNEMENTS / COMPÉTITIONS / PLANS (Calendrier) ────────────────────────
@app.route("/training")
def training():
    # Compétitions
    ta = TrainingAnalysis(activity_manager, health_manager)
    competitions_file = os.path.join("data", "competitions.json")
    ta.load_competitions(competitions_file)
    competitions = ta.get_competitions()

    # Entraînements manuels
    trainings_file = os.path.join("data", "trainings.json")
    trainings = _load_json(trainings_file) or []

    # Tags d'activités
    tags_path = os.path.join("data", "activity_tags.json")
    activity_tags = _load_json(tags_path) or {}

    # Séances planifiées (bleu)
    plans_path = os.path.join("data", "plans.json")
    plans = _load_json(plans_path) or []

    # Activités Garmin : préférer l’agrégé (résumés plus complets)
    src = list(SUMMARY_BY_ID.values()) or activity_manager.activities or []
    activities = []
    for a in src:
        if not a:
            continue
        act_id = a.get("activityId")
        name = a.get("activityName") or (a.get("activityType", {}) or {}).get("typeKey") or "Activité"
        date = a.get("startTimeLocal", "Date inconnue")
        distance_km = round((a.get("distance", 0) or 0) / 1000.0, 2)
        duration_min = int(round((a.get("duration", 0) or 0) / 60))
        type_key = (a.get("activityType", {}) or {}).get("typeKey")
        label = activity_tags.get(str(act_id))

        activities.append({
            "id": act_id, "name": name, "date": date,
            "distance": distance_km, "durationMin": duration_min,
            "typeKey": type_key, "label": label
        })

    # anti-doublons côté serveur : si (nom+date) == activité garmin → retirer training manuel
    act_keys = {(_norm(a["name"]), (a["date"] or "")[:10]) for a in activities}
    trainings = [
        t for t in trainings
        if (_norm(t.get("name", "")), (t.get("date") or "")[:10]) not in act_keys
    ]

    return render_template(
        "training.html",
        trainings=trainings,
        competitions=competitions,
        activities=activities,
        plans=plans
    )

@app.route("/tag_activity", methods=["POST"])
def tag_activity():
    try:
        payload = request.get_json(force=True)
        act_id = str(payload.get("activityId"))
        tag = payload.get("tag")  # 'training' | 'competition' | 'none'
        if not act_id or tag not in ("training", "competition", "none"):
            return {"ok": False, "error": "Paramètres invalides"}, 400

        path = os.path.join("data", "activity_tags.json")
        tags = _load_json(path) or {}
        if tag == "none":
            tags.pop(act_id, None)
        else:
            tags[act_id] = tag
        _save_json(path, tags)
        return {"ok": True}
    except Exception as e:
        logging.exception("tag_activity error")
        return {"ok": False, "error": str(e)}, 500

@app.route("/add_plan", methods=["POST"])
def add_plan():
    try:
        name = request.form.get("name")
        date = request.form.get("date")  # YYYY-MM-DD
        ptype = request.form.get("ptype")
        distance = request.form.get("distance")
        duration = request.form.get("duration")
        if not name or not date:
            flash("Nom et date requis pour la séance planifiée.", "error")
            return redirect(url_for("training"))

        path = os.path.join("data", "plans.json")
        plans = _load_json(path) or []
        entry = {
            "name": name, "date": date,
            "ptype": ptype if ptype in ("training", "competition") else None,
            "distance": float(distance) if distance else None,
            "durationMin": int(duration) if duration else None
        }
        plans.append(entry)
        _save_json(path, plans)
        flash("Séance planifiée ajoutée.", "success")
    except Exception as e:
        logging.exception("add_plan error")
        flash(f"Erreur ajout séance planifiée : {e}", "error")
    return redirect(url_for("training"))

@app.route("/remove_plan/<int:index>")
def remove_plan(index):
    try:
        path = os.path.join("data", "plans.json")
        plans = _load_json(path) or []
        if 0 <= index < len(plans):
            plans.pop(index)
            _save_json(path, plans)
            flash("Séance planifiée supprimée.", "success")
        else:
            flash("Index de séance planifiée invalide.", "error")
    except Exception as e:
        logging.exception("remove_plan error")
        flash(f"Erreur suppression séance planifiée : {e}", "error")
    return redirect(url_for("training"))

@app.route("/add_competition", methods=["POST"])
def add_competition():
    name = request.form.get("name")
    date = request.form.get("date")
    location = request.form.get("location")
    if not name or not date or not location:
        flash("Tous les champs sont obligatoires pour ajouter une compétition.", "error")
        return redirect(url_for("training"))

    ta = TrainingAnalysis(activity_manager, health_manager)
    competitions_file = os.path.join("data", "competitions.json")
    ta.load_competitions(competitions_file)
    ta.add_competition(name, date, location)
    ta.save_competitions(competitions_file)
    flash("Compétition ajoutée avec succès !", "success")
    return redirect(url_for("training"))

@app.route("/remove_competition/<string:name>")
def remove_competition(name):
    ta = TrainingAnalysis(activity_manager, health_manager)
    competitions_file = os.path.join("data", "competitions.json")
    ta.load_competitions(competitions_file)
    ta.remove_competition(name)
    ta.save_competitions(competitions_file)
    flash(f"Compétition '{name}' supprimée.", "success")
    return redirect(url_for("training"))

@app.route("/add_training", methods=["POST"])
def add_training():
    name = request.form.get("name")
    date = request.form.get("date")
    if name and date:
        try:
            file_path = os.path.join("data", "trainings.json")
            trainings = _load_json(file_path) or []
            trainings.append({"name": name, "date": date})
            _save_json(file_path, trainings)
            flash(f"Entraînement '{name}' ajouté pour le {date}.", "success")
        except Exception as e:
            flash(f"Erreur lors de l'ajout de l'entraînement : {e}", "error")
    else:
        flash("Nom ou date manquant.", "error")
    return redirect(url_for("training"))

@app.route("/remove_training/<name>")
def remove_training(name):
    try:
        file_path = os.path.join("data", "trainings.json")
        trainings = _load_json(file_path) or []
        trainings = [t for t in trainings if t.get("name") != name]
        _save_json(file_path, trainings)
        flash(f"Entraînement '{name}' supprimé.", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression de l'entraînement : {e}", "error")
    return redirect(url_for("training"))


# ── TRACKING ─────────────────────────────────────────────────────────────────
@app.route("/tracking")
def tracking():
    activity_manager.plot_tracking_graphs("static/tracking")
    tracking_graphs = [
        f"/static/tracking/{f}"
        for f in os.listdir("static/tracking")
        if f.endswith(".html")
    ]
    return render_template("tracking.html", graphs=tracking_graphs)


# ── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
