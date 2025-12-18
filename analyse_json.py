# analyse_json.py
# Parcourt les détails d'activités Garmin (tous formats), extrait des métriques utiles,
# produit un catalogue CSV + des agrégats (semaine, zones FC), et génère des graphes Plotly.

import os
import re
import json
import math
import argparse
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------
DEFAULT_USER = os.getenv("GARMIN_USER_ID", "Adri")
DATA_DIR     = "data"
OUT_DIR      = os.path.join("static", "analysis")
AGG_PATH_TMPL         = os.path.join(DATA_DIR, "{user}_activities.json")         # {"activities": {id: {"summary":..., "details":...}}} ou liste de résumés
DETAILS_AGG_PATH_TMPL = os.path.join(DATA_DIR, "{user}_activity_details.json")   # {id: details} OU {"activities": {...}}
UNIT_FILE_RE          = re.compile(r"^activity_(\d+)_details\.json$")

os.makedirs(OUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ---------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_csv(df: pd.DataFrame, path: str):
    df.to_csv(path, index=False, encoding="utf-8")
    logging.info("CSV écrit : %s", path)

def to_datetime_ms(ms):
    try:
        return datetime.fromtimestamp(float(ms)/1000.0, tz=timezone.utc)
    except Exception:
        return None

def hms(seconds: float) -> str:
    s = int(round(seconds or 0))
    h, r = divmod(s, 3600)
    m, s2 = divmod(r, 60)
    if h > 0:
        return f"{h}h{m:02d}m{s2:02d}s"
    return f"{m}m{s2:02d}s"

def safe_div(a, b):
    try:
        if b and float(b) != 0:
            return float(a)/float(b)
    except Exception:
        pass
    return np.nan

def looks_like_details(d: dict) -> bool:
    return isinstance(d, dict) and ("metricDescriptors" in d or "activityDetailMetrics" in d)

# ---------------------------------------------------------------------
# Chargement multi-sources (corrigé et tolérant)
# ---------------------------------------------------------------------
def load_all(user: str):
    """
    Retourne:
      summaries_by_id: {str(id): summary}
      details_by_id:   {str(id): details}

    Sources combinées :
      - {user}_activities.json :
          * forme moderne: {"activities": {id: {"summary":..., "details":...}}}
          * forme "liste": [ {activityId:.., ...}, ... ]
      - {user}_activity_details.json :
          * {id: details}
          * {"activities": {id: {"summary":..., "details":...} OU détails bruts}}
      - fichiers unitaires data/activity_<id>_details.json
    """
    summaries_by_id, details_by_id = {}, {}

    agg_path   = AGG_PATH_TMPL.format(user=user)
    d_agg_path = DETAILS_AGG_PATH_TMPL.format(user=user)

    def _is_id(s):
        try:
            return str(s).isdigit()
        except Exception:
            return False

    # 1) Agrégé moderne / liste dans {user}_activities.json
    agg = load_json(agg_path)
    if isinstance(agg, dict) and isinstance(agg.get("activities"), dict):
        for aid, pack in agg["activities"].items():
            if not _is_id(aid) or not isinstance(pack, dict):
                continue
            if isinstance(pack.get("summary"), dict):
                summaries_by_id[str(aid)] = pack["summary"]
            if looks_like_details(pack.get("details")):
                details_by_id[str(aid)] = pack["details"]
    elif isinstance(agg, list):
        for a in agg:
            if isinstance(a, dict) and _is_id(a.get("activityId")):
                summaries_by_id[str(a["activityId"])] = a

    # 2) Ancien agrégé détails (ou moderne stocké par erreur sous ce nom)
    d_agg = load_json(d_agg_path)
    if isinstance(d_agg, dict):
        if isinstance(d_agg.get("activities"), dict):
            for aid, pack in d_agg["activities"].items():
                if not _is_id(aid):
                    continue
                det = None
                if isinstance(pack, dict):
                    if looks_like_details(pack.get("details")):
                        det = pack["details"]
                    elif looks_like_details(pack):
                        det = pack
                if det:
                    details_by_id[str(aid)] = det
        else:
            for aid, det in d_agg.items():
                if _is_id(aid) and looks_like_details(det):
                    details_by_id[str(aid)] = det

    # 3) Fichiers unitaires de détails
    for fname in os.listdir(DATA_DIR):
        m = UNIT_FILE_RE.match(fname)
        if not m:
            continue
        aid = m.group(1)
        if aid in details_by_id:
            continue
        det = load_json(os.path.join(DATA_DIR, fname))
        if looks_like_details(det):
            details_by_id[aid] = det

    return summaries_by_id, details_by_id

# ---------------------------------------------------------------------
# Reconstruction séries temporelles
# ---------------------------------------------------------------------
KEY_RENAMES = {
    "directHeartRate": "hr",
    "directSpeed": "speed_mps",
    "sumDistance": "dist_m",
    "sumDuration": "time_s",
    "sumMovingDuration": "moving_s",
    "sumElapsedDuration": "elapsed_s",
    "directLatitude": "lat",
    "directLongitude": "lon",
    "directVerticalSpeed": "vert_mps",
    "directDoubleCadence": "cadence_spm",
    "directFractionalCadence": "cadence_spm_frac",
    "directTimestamp": "timestamp_gmt_ms",
    "directElevation": "elev_m",
    "directBodyBattery": "body_battery",
}

def parse_timeseries_from_details(details: dict) -> pd.DataFrame:
    if not isinstance(details, dict):
        return pd.DataFrame()

    mdesc = details.get("metricDescriptors")
    mrows = details.get("activityDetailMetrics")

    if not isinstance(mdesc, list) or not isinstance(mrows, list) or len(mdesc) == 0:
        return pd.DataFrame()

    idx_to_key = {}
    for d in mdesc:
        try:
            idx = d["metricsIndex"]
            key = d["key"]
            idx_to_key[idx] = key
        except Exception:
            continue

    records = []
    for row in mrows:
        metrics = row.get("metrics")
        if not isinstance(metrics, list):
            continue
        rec = {}
        for i, val in enumerate(metrics):
            key = idx_to_key.get(i)
            if key:
                rec[key] = val
        records.append(rec)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame.from_records(records)
    df = df.rename(columns={k: KEY_RENAMES.get(k, k) for k in df.columns})

    if "timestamp_gmt_ms" in df.columns:
        df["timestamp_utc"] = df["timestamp_gmt_ms"].apply(to_datetime_ms)
        df = df.set_index("timestamp_utc", drop=False)

    for c in ["hr", "speed_mps", "dist_m", "time_s", "moving_s", "elapsed_s",
              "lat", "lon", "vert_mps", "cadence_spm", "cadence_spm_frac",
              "elev_m", "body_battery"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df

# ---------------------------------------------------------------------
# Résumé activité
# ---------------------------------------------------------------------
def summarize_activity(aid: str, summary: dict, ts: pd.DataFrame) -> dict:
    date = summary.get("startTimeLocal") or summary.get("startTimeGMT")
    name = summary.get("activityName") or (summary.get("activityType") or {}).get("typeKey") or "Activité"
    sport = (summary.get("activityType") or {}).get("typeKey")
    location = summary.get("locationName")

    distance_m = float(summary.get("distance") or 0.0)
    duration_s = float(summary.get("duration") or 0.0)
    avg_hr = summary.get("averageHR")
    max_hr = summary.get("maxHR")
    elev_gain = summary.get("elevationGain")
    elev_loss = summary.get("elevationLoss")
    te_aero = summary.get("aerobicTrainingEffect")
    te_ana  = summary.get("anaerobicTrainingEffect")
    vo2     = summary.get("vO2MaxValue")

    pace_s_per_km = safe_div(duration_s, distance_m/1000.0) if distance_m > 0 else np.nan
    if isinstance(pace_s_per_km, float) and not math.isnan(pace_s_per_km):
        pace_min = int(pace_s_per_km // 60)
        pace_sec = int(pace_s_per_km % 60)
        pace_str = f"{pace_min}:{pace_sec:02d}/km"
    else:
        pace_str = None

    hr_zones = {
        "hr_z1_s": summary.get("hrTimeInZone_1"),
        "hr_z2_s": summary.get("hrTimeInZone_2"),
        "hr_z3_s": summary.get("hrTimeInZone_3"),
        "hr_z4_s": summary.get("hrTimeInZone_4"),
        "hr_z5_s": summary.get("hrTimeInZone_5"),
    }

    avg_speed_mps = ts["speed_mps"].mean() if "speed_mps" in ts.columns else np.nan
    max_speed_mps = ts["speed_mps"].max()  if "speed_mps" in ts.columns else np.nan

    sets_info = None
    if sport == "strength_training":
        sets_info = summary.get("summarizedExerciseSets")

    return {
        "activity_id": aid,
        "date_local": date,
        "name": name,
        "sport": sport,
        "location": location,
        "distance_km": round(distance_m/1000.0, 3) if distance_m else 0.0,
        "duration_s": round(duration_s, 2),
        "duration_hms": hms(duration_s),
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "elev_gain_m": elev_gain,
        "elev_loss_m": elev_loss,
        "aero_TE": te_aero,
        "ana_TE": te_ana,
        "vo2max": vo2,
        "avg_speed_mps": None if math.isnan(avg_speed_mps) else round(avg_speed_mps, 3),
        "max_speed_mps": None if math.isnan(max_speed_mps) else round(max_speed_mps, 3),
        "pace_min_per_km": pace_str,
        **hr_zones,
        "is_parent": bool(summary.get("parent", False)),
        "has_polyline": bool(summary.get("hasPolyline", False)),
        "calories": summary.get("calories"),
        "sets_info": sets_info
    }

# ---------------------------------------------------------------------
# Graphes
# ---------------------------------------------------------------------
def plot_weekly_volume(catalog: pd.DataFrame, out_html: str):
    if catalog.empty:
        return
    df = catalog.copy()
    df["date"] = pd.to_datetime(df["date_local"].str[:19], errors="coerce")
    df = df.dropna(subset=["date"])
    df["week"] = df["date"].dt.to_period("W").apply(lambda p: p.start_time.date())
    grp = df.groupby(["week", "sport"], as_index=False)["distance_km"].sum()

    fig = go.Figure()
    for sport, sub in grp.groupby("sport"):
        fig.add_trace(go.Bar(x=sub["week"], y=sub["distance_km"], name=sport))
    fig.update_layout(
        barmode="stack", title="Volume hebdomadaire par sport (km)",
        xaxis_title="Semaine", yaxis_title="Distance (km)", template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        height=500, width=1200
    )
    fig.write_html(out_html)
    logging.info("Graph écrit : %s", out_html)

def plot_running_scatter(catalog: pd.DataFrame, out_html: str):
    if catalog.empty:
        return
    run = catalog[catalog["sport"] == "running"].copy()
    if run.empty:
        return
    def pace_to_seconds(p):
        if not isinstance(p, str) or "/" not in p:
            return np.nan
        m, s = p.split("/")[0].split(":")
        return int(m)*60 + int(s)
    run["pace_s_per_km"] = run["pace_min_per_km"].apply(pace_to_seconds)
    run = run.dropna(subset=["pace_s_per_km", "distance_km"])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=run["distance_km"], y=run["pace_s_per_km"],
        mode="markers", text=run["name"],
        hovertemplate="Dist: %{x:.2f} km<br>Pace: %{y:.0f} s/km<br>%{text}"
    ))
    fig.update_layout(
        title="Course à pied — Distance vs Allure",
        xaxis_title="Distance (km)",
        yaxis_title="Allure (s/km) — plus bas = plus rapide",
        template="plotly_dark", height=500, width=1000
    )
    fig.write_html(out_html)
    logging.info("Graph écrit : %s", out_html)

def plot_hr_zones_weekly(catalog: pd.DataFrame, out_html: str):
    if catalog.empty:
        return
    df = catalog.copy()
    df["date"] = pd.to_datetime(df["date_local"].str[:19], errors="coerce")
    df = df.dropna(subset=["date"])

    zones = ["hr_z1_s", "hr_z2_s", "hr_z3_s", "hr_z4_s", "hr_z5_s"]
    present_cols = [z for z in zones if z in df.columns]
    if not present_cols:
        return

    df["week"] = df["date"].dt.to_period("W").apply(lambda p: p.start_time.date())
    agg = df.groupby("week", as_index=False)[present_cols].sum()

    fig = go.Figure()
    for z in present_cols:
        fig.add_trace(go.Bar(x=agg["week"], y=agg[z], name=z))
    fig.update_layout(
        barmode="stack",
        title="Temps passé par zones de FC (hebdomadaire, secondes cumulées)",
        xaxis_title="Semaine",
        yaxis_title="Secondes",
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        height=500, width=1200
    )
    fig.write_html(out_html)
    logging.info("Graph écrit : %s", out_html)

# ---------------------------------------------------------------------
# Routine principale (corrigée: filtre IDs numériques)
# ---------------------------------------------------------------------
def main(user: str, only_sport: str = None, limit: int = None):
    summaries_by_id, details_by_id = load_all(user)
    if not summaries_by_id:
        logging.warning("Aucun résumé trouvé pour l’utilisateur %s", user)
    if not details_by_id:
        logging.warning("Aucun détail trouvé pour l’utilisateur %s", user)

    raw_ids = set(list(summaries_by_id.keys()) + list(details_by_id.keys()))
    num_ids = [s for s in raw_ids if str(s).isdigit()]  # filtre clé non numérique (ex: 'activities')
    all_ids = sorted(num_ids, key=lambda x: int(x))
    if limit:
        all_ids = all_ids[-limit:]  # derniers N

    rows = []
    ts_samples = []

    for aid in all_ids:
        summary = summaries_by_id.get(aid, {})
        sport = (summary.get("activityType") or {}).get("typeKey")
        if only_sport and sport != only_sport:
            continue

        details = details_by_id.get(aid)
        ts = parse_timeseries_from_details(details) if details else pd.DataFrame()

        row = summarize_activity(aid, summary, ts)
        rows.append(row)

        if not ts.empty and sport and not any(x[0] == sport for x in ts_samples):
            ts_samples.append((sport, aid, ts))

    catalog = pd.DataFrame(rows)
    if not catalog.empty:
        catalog["date_sort"] = pd.to_datetime(catalog["date_local"].str[:19], errors="coerce")
        catalog = catalog.sort_values("date_sort", ascending=False).drop(columns=["date_sort"])

        save_csv(catalog, os.path.join(OUT_DIR, "activity_catalog.csv"))

        plot_weekly_volume(catalog, os.path.join(OUT_DIR, "weekly_volume.html"))
        plot_running_scatter(catalog, os.path.join(OUT_DIR, "running_scatter.html"))
        plot_hr_zones_weekly(catalog, os.path.join(OUT_DIR, "hr_zones_weekly.html"))

        per_sport = catalog.groupby("sport", as_index=False)[["distance_km", "duration_s", "calories"]].sum()
        save_csv(per_sport, os.path.join(OUT_DIR, "agg_per_sport.csv"))

        hr_cols = [c for c in ["hr_z1_s", "hr_z2_s", "hr_z3_s", "hr_z4_s", "hr_z5_s"] if c in catalog.columns]
        if hr_cols:
            hr_agg = catalog[hr_cols].sum().to_frame(name="seconds").reset_index(names="zone")
            save_csv(hr_agg, os.path.join(OUT_DIR, "agg_hr_zones.csv"))

        for sport, aid, ts in ts_samples:
            outp = os.path.join(OUT_DIR, f"ts_sample_{sport}_{aid}.csv")
            ts.to_csv(outp, index=False)
            logging.info("Échantillon TS (%s, id=%s) écrit : %s", sport, aid, outp)

        index_html = os.path.join(OUT_DIR, "summary.html")
        with open(index_html, "w", encoding="utf-8") as f:
            f.write(f"""
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<title>Analyse Garmin – {user}</title>
<link rel="stylesheet" href="../style.css"/>
</head>
<body style="background:#111;color:#eee;font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:1.5rem">
  <h1>Analyse Garmin – {user}</h1>
  <p>Exports :</p>
  <ul>
    <li><a href="activity_catalog.csv">activity_catalog.csv</a></li>
    <li><a href="agg_per_sport.csv">agg_per_sport.csv</a></li>
    <li><a href="agg_hr_zones.csv">agg_hr_zones.csv</a></li>
  </ul>
  <p>Graphiques :</p>
  <ul>
    <li><a href="weekly_volume.html">Volume hebdomadaire</a></li>
    <li><a href="running_scatter.html">Course à pied — Distance vs Allure</a></li>
    <li><a href="hr_zones_weekly.html">Zones FC (hebdo)</a></li>
  </ul>
</body>
</html>
""")
        logging.info("Résumé HTML : %s", index_html)
    else:
        logging.warning("Catalogue vide : aucune activité exploitable.")

# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyse tous les activity details Garmin et génère des exports/graphes.")
    parser.add_argument("--user", default=DEFAULT_USER, help="USER_ID (défaut: env GARMIN_USER_ID ou 'Adri')")
    parser.add_argument("--sport", default=None, help="Filtrer sur un sport (running, cycling, lap_swimming, strength_training, multi_sport, ...)")
    parser.add_argument("--limit", type=int, default=None, help="Ne traiter que les N dernières activités (par id trié)")
    args = parser.parse_args()

    logging.info("Démarrage analyse pour user=%s", args.user)
    main(args.user, only_sport=args.sport, limit=args.limit)
    logging.info("Terminé.")
