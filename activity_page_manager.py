import os
from typing import Dict, List, Optional, Tuple

import folium
from jinja2 import Template
import plotly.graph_objects as go


class ActivityPageManager:
    def __init__(self, output_dir: str = "static/activity_pages"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs("static/graphs", exist_ok=True)

    # ---------- helpers métriques ----------
    @staticmethod
    def _index_of(descriptors: List[Dict], keys: List[str]) -> Optional[int]:
        """Retourne l'index de la première clé trouvée dans metricDescriptors."""
        for k in keys:
            for d in descriptors:
                if d.get("key") == k:
                    return d.get("metricsIndex")
        return None

    @staticmethod
    def _collect_series(activity_metrics: List[Dict], idx: Optional[int]) -> List[float]:
        if idx is None:
            return []
        out = []
        for pt in activity_metrics:
            metrics = pt.get("metrics", [])
            if idx < len(metrics):
                val = metrics[idx]
                if val is not None:
                    out.append(val)
        return out

    # ---------- carte ----------
    def generate_activity_map(self, activity_id: str, details: Dict) -> Optional[str]:
        """Génère une carte <iframe> si des lat/lon existent."""
        try:
            descriptors = details.get("metricDescriptors", []) or []
            metrics = details.get("activityDetailMetrics", []) or []

            lat_idx = self._index_of(descriptors, ["directLatitude", "latitude"])
            lon_idx = self._index_of(descriptors, ["directLongitude", "longitude"])

            if lat_idx is None or lon_idx is None:
                return None

            path: List[Tuple[float, float]] = []
            for row in metrics:
                vals = row.get("metrics", [])
                if max(lat_idx, lon_idx) < len(vals):
                    lat = vals[lat_idx]
                    lon = vals[lon_idx]
                    if lat is not None and lon is not None:
                        path.append((lat, lon))

            if not path:
                return None

            map_file = os.path.join(self.output_dir, f"activity_{activity_id}_map.html").replace("\\", "/")
            m = folium.Map(location=path[0], zoom_start=14)
            folium.PolyLine(path, color="blue", weight=2.5).add_to(m)
            m.save(map_file)
            return f"/{map_file}"
        except Exception as e:
            print(f"[MAP] Erreur: {e}")
            return None

    # ---------- graphes ----------
    def _hr_zones_graph(self, activity_id: str, hr_values: List[float]) -> Optional[str]:
        if not hr_values:
            return None
        # 4 zones simples (à adapter si besoin)
        thresholds = [(0, 120), (120, 140), (140, 160), (160, 300)]
        counts = [0, 0, 0, 0]
        for v in hr_values:
            for i, (a, b) in enumerate(thresholds):
                if a <= v < b:
                    counts[i] += 1
                    break

        fig = go.Figure()
        fig.add_trace(go.Bar(x=["Z1", "Z2", "Z3", "Z4"], y=counts))
        fig.update_layout(
            title="Temps dans les zones FC",
            xaxis_title="Zones",
            yaxis_title="Secondes (≈ points)",
            template="plotly_dark",
            height=420
        )
        out = f"static/graphs/{activity_id}_hr_zones.html"
        fig.write_html(out)
        return f"/{out}"

    def _line_graph(self, activity_id: str, y: List[float], title: str, ytitle: str, suffix: str) -> Optional[str]:
        if not y:
            return None
        fig = go.Figure()
        fig.add_trace(go.Scatter(y=y, mode="lines", name=title))
        fig.update_layout(title=title, xaxis_title="Temps", yaxis_title=ytitle, template="plotly_dark", height=420)
        out = f"static/graphs/{activity_id}_{suffix}.html"
        fig.write_html(out)
        return f"/{out}"

    def generate_graphs(self, activity: Dict, details: Dict) -> List[str]:
        """Sélectionne des graphes pertinents selon le sport."""
        descriptors = details.get("metricDescriptors", []) or []
        rows = details.get("activityDetailMetrics", []) or []
        sport = ((activity.get("activityType") or {}).get("typeKey") or "").lower()
        activity_id = str(activity.get("activityId"))

        # indices de métriques (selon sport on choisit lesquelles utiliser)
        hr_idx      = self._index_of(descriptors, ["directHeartRate", "heartRate"])
        speed_idx   = self._index_of(descriptors, ["directSpeed", "speed"])              # m/s
        pace_idx    = None  # on calcule depuis speed
        run_cad_idx = self._index_of(descriptors, ["directRunCadence"])
        bike_cad_idx= self._index_of(descriptors, ["directBikeCadence", "directCadence"])
        elev_idx    = self._index_of(descriptors, ["elevation", "directElevation"])
        power_idx   = self._index_of(descriptors, ["directBikePower", "directPower"])

        # séries
        hr = self._collect_series(rows, hr_idx)
        speed_ms = self._collect_series(rows, speed_idx)
        run_cad = self._collect_series(rows, run_cad_idx)
        bike_cad= self._collect_series(rows, bike_cad_idx)
        elev    = self._collect_series(rows, elev_idx)
        power   = self._collect_series(rows, power_idx)

        graphs: List[str] = []

        # ——— sport: running
        if "run" in sport:  # running, trail_running, etc.
            # allure min/km depuis m/s
            pace_min_per_km = []
            for v in speed_ms:
                if v and v > 0:
                    min_per_km = (1000.0 / v) / 60.0
                    pace_min_per_km.append(min_per_km)
                else:
                    pace_min_per_km.append(None)
            # Nettoyage None pour Plotly (remplacer par NaN-like : on filtre)
            pace_series = [p for p in pace_min_per_km if p is not None]

            g1 = self._line_graph(activity_id, hr,   "Fréquence cardiaque", "BPM", "hr")
            g2 = self._line_graph(activity_id, pace_series, "Allure (min/km)", "min/km", "pace")
            g3 = self._line_graph(activity_id, run_cad, "Cadence (pas/min)", "pas/min", "run_cad")
            g4 = self._line_graph(activity_id, elev,  "Dénivelé", "m", "elevation")
            g5 = self._hr_zones_graph(activity_id, hr)
            for g in [g1, g2, g3, g4, g5]:
                if g: graphs.append(g)

        # ——— sport: vélo
        elif "cycl" in sport or "bike" in sport:
            # vitesse km/h
            speed_kmh = [v * 3.6 for v in speed_ms if v is not None]
            g1 = self._line_graph(activity_id, hr,        "Fréquence cardiaque", "BPM", "hr")
            g2 = self._line_graph(activity_id, speed_kmh, "Vitesse (km/h)", "km/h", "speed")
            g3 = self._line_graph(activity_id, bike_cad,  "Cadence (rpm)", "rpm", "bike_cad")
            g4 = self._line_graph(activity_id, power,     "Puissance (W)", "W", "power")
            g5 = self._line_graph(activity_id, elev,      "Dénivelé", "m", "elevation")
            g6 = self._hr_zones_graph(activity_id, hr)
            for g in [g1, g2, g3, g4, g5, g6]:
                if g: graphs.append(g)

        # ——— sport: musculation
        elif "strength" in sport or "muscu" in sport or "weight" in sport:
            g1 = self._line_graph(activity_id, hr, "Fréquence cardiaque", "BPM", "hr")
            g2 = self._hr_zones_graph(activity_id, hr)
            for g in [g1, g2]:
                if g: graphs.append(g)

        # ——— sport: multisport (ex: triathlon)
        elif "multi" in sport or "tri" in sport:
            speed_kmh = [v * 3.6 for v in speed_ms if v is not None]
            g1 = self._line_graph(activity_id, hr,        "Fréquence cardiaque", "BPM", "hr")
            if speed_kmh:
                g2 = self._line_graph(activity_id, speed_kmh, "Vitesse (km/h)", "km/h", "speed")
                graphs.append(g2)
            g3 = self._line_graph(activity_id, elev, "Dénivelé", "m", "elevation")
            g4 = self._hr_zones_graph(activity_id, hr)
            for g in [g1, g3, g4]:
                if g: graphs.append(g)

        # ——— fallback (sport inconnu)
        else:
            g1 = self._line_graph(activity_id, hr, "Fréquence cardiaque", "BPM", "hr")
            g2 = self._line_graph(activity_id, elev, "Dénivelé", "m", "elevation")
            for g in [g1, g2]:
                if g: graphs.append(g)

        return graphs

    # ---------- page ----------
    def generate_activity_page(self, activity: Dict, details: Dict) -> Optional[str]:
        """Génère une page HTML complète : carte + résumé + graphes pertinents."""
        try:
            activity_id = str(activity.get("activityId"))
            sport = ((activity.get("activityType") or {}).get("typeKey") or "Activité").replace("_", " ").title()

            # carte (si GPS) + graphes
            map_file = self.generate_activity_map(activity_id, details)
            graph_files = self.generate_graphs(activity, details)

            # résumé
            distance_km = round((activity.get("distance", 0) or 0) / 1000.0, 2)
            duration_min = int(round((activity.get("duration", 0) or 0) / 60.0))
            avg_hr = activity.get("averageHR") or activity.get("avgHR") or "N/A"
            avg_pace = "N/A"
            if distance_km and duration_min:
                # allure moyenne min/km
                total_min = duration_min
                min_per_km = total_min / max(distance_km, 1e-9)
                mm = int(min_per_km)
                ss = int(round((min_per_km - mm) * 60))
                avg_pace = f"{mm}:{ss:02d} min/km"

            template_str = """
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>Activité {{ activityId }}</title>
  <style>
    body{background:#0c1118;color:#e5e7eb;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;}
    header{padding:16px 20px;border-bottom:1px solid #1f2430;background:#0d1117;}
    a{color:#93c5fd;text-decoration:none}
    h1{font-size:22px;margin:0}
    .container{max-width:1200px;margin:20px auto;padding:0 16px;}
    .grid{display:flex;gap:20px;flex-wrap:wrap;}
    .card{flex:1;min-width:320px;background:#121722;border:1px solid #1f2430;border-radius:10px;box-shadow:0 4px 6px rgba(0,0,0,.4);}
    .card h2{margin:0;padding:12px 14px;border-bottom:1px solid #1f2430;font-size:18px;}
    .card .content{padding:14px;}
    .stats ul{list-style:none;margin:0;padding:0}
    .stats li{margin-bottom:8px;color:#cbd5e1}
    .stats li strong{color:#fff}
    iframe{width:100%;height:420px;border:0;border-radius:8px;overflow:hidden}
    .graphs{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px;margin-top:16px}
    .back{display:inline-block;margin-bottom:12px}
  </style>
</head>
<body>
  <header>
    <div class="container">
      <a class="back" href="/training">← Retour calendrier</a>
      <h1>{{ sport }}</h1>
    </div>
  </header>

  <div class="container">
    <div class="grid">
      <div class="card">
        <h2>Carte du parcours</h2>
        <div class="content">
          {% if map_file %}
            <iframe src="{{ map_file }}"></iframe>
          {% else %}
            <p>Pas de GPS pour cette séance.</p>
          {% endif %}
        </div>
      </div>

      <div class="card stats">
        <h2>Résumé</h2>
        <div class="content">
          <ul>
            <li><strong>Distance :</strong> {{ distance }} km</li>
            <li><strong>Durée :</strong> {{ duration }} min</li>
            <li><strong>Allure moy. :</strong> {{ avg_pace }}</li>
            <li><strong>FC moy. :</strong> {{ avg_hr }}</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:16px;">
      <h2>Graphiques</h2>
      <div class="content">
        <div class="graphs">
          {% for g in graphs %}
            <iframe src="{{ g }}"></iframe>
          {% endfor %}
          {% if graphs|length == 0 %}
            <p>Aucun graphique disponible pour cette séance.</p>
          {% endif %}
        </div>
      </div>
    </div>
  </div>
</body>
</html>
"""
            out = os.path.join(self.output_dir, f"activity_{activity_id}.html").replace("\\", "/")
            with open(out, "w", encoding="utf-8") as f:
                f.write(Template(template_str).render(
                    activityId=activity_id,
                    sport=sport,
                    map_file=map_file,
                    graphs=graph_files,
                    distance=distance_km,
                    duration=duration_min,
                    avg_pace=avg_pace,
                    avg_hr=avg_hr
                ))
            return f"/{out}"
        except Exception as e:
            print(f"[PAGE] Erreur: {e}")
            return None
