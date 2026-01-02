import os
import json
import time
import logging
import pandas as pd
from datetime import datetime, timedelta
import numpy as np

from typing import Any

from .echarts import write_timeseries_chart_html


def _graph_cache_path(output_dir: str) -> str:
    return os.path.join(output_dir, ".graph_cache.json")


def _read_graph_cache(output_dir: str) -> dict[str, Any] | None:
    path = _graph_cache_path(output_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_graph_cache(output_dir: str, payload: dict[str, Any]) -> None:
    path = _graph_cache_path(output_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _has_any_html(output_dir: str) -> bool:
    try:
        return any(name.endswith(".html") for name in os.listdir(output_dir))
    except Exception:
        return False


def _clean_html(output_dir: str) -> None:
    try:
        for name in os.listdir(output_dir):
            if name.endswith(".html"):
                try:
                    os.remove(os.path.join(output_dir, name))
                except OSError:
                    pass
    except Exception:
        pass


def _source_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


def _echarts_mtime() -> float:
    try:
        path = os.path.join(os.path.dirname(__file__), "echarts.py")
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


class GarminHealthManager:
    def __init__(self, user_id, *, health_data=None):
        self.user_id = user_id
        self.data_file = os.path.join("data", f"{self.user_id}_health.json")
        os.makedirs("data", exist_ok=True)
        self.health_data = health_data if isinstance(health_data, list) else self._load_data()

    def _load_data(self):
        """Charge les données de santé depuis un fichier JSON."""
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    logging.info(f"Données de santé chargées depuis {self.data_file}.")
                    return data
                except json.JSONDecodeError:
                    logging.warning(f"Fichier corrompu, réinitialisation : {self.data_file}.")
        return []

    def _save_data(self):
        """Sauvegarde les données de santé dans un fichier JSON."""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.health_data, f, indent=4)
        logging.info(f"Données de santé sauvegardées dans {self.data_file}.")

    def find_missing_dates(self):
        """Trouve les dates manquantes pour les six derniers mois."""
        start_date = datetime.now() - timedelta(days=1460)
        end_date = datetime.now()
        existing_dates = {datetime.fromisoformat(entry['date']).date() for entry in self.health_data}
        all_dates = {start_date.date() + timedelta(days=n) for n in range((end_date - start_date).days + 1)}
        return sorted(all_dates - existing_dates)

    def update_data(self, client_handler):
        """Met à jour les données de santé pour les dates manquantes."""
        missing_dates = self.find_missing_dates()
        new_data = []

        for single_date in missing_dates:
            logging.info(f"Fetching health data for {single_date}.")
            try:
                health_data = client_handler.client.get_stats(single_date)
                if health_data:
                    health_data['date'] = single_date.isoformat()
                    new_data.append(health_data)
            except Exception as e:
                logging.error(f"Erreur lors de la récupération des données de santé pour {single_date}: {e}")
            time.sleep(1)

        self.health_data.extend(new_data)
        if new_data:
            logging.info(f"{len(new_data)} nouvelles données de santé ajoutées.")
        else:
            logging.info("Aucune nouvelle donnée de santé ajoutée.")
        self._save_data()

    def plot_interactive_graphs(self, output_dir):
        """Trace les graphiques interactifs (ECharts) pour les données de santé."""
        os.makedirs(output_dir, exist_ok=True)

        src_mtime = _source_mtime(self.data_file)
        echarts_mtime = _echarts_mtime()
        cache = _read_graph_cache(output_dir)
        if (
            cache
            and cache.get("source") == self.data_file
            and float(cache.get("source_mtime") or 0.0) == src_mtime
            and float(cache.get("echarts_mtime") or 0.0) == echarts_mtime
            and _has_any_html(output_dir)
        ):
            return

        _clean_html(output_dir)

        six_months_ago = datetime.now() - timedelta(days=1460)
        filtered_health_data = [h for h in self.health_data if datetime.fromisoformat(h["date"]) >= six_months_ago]

        if not filtered_health_data:
            logging.warning("Aucune donnée de santé valide des 4 dernières années pour tracer les graphiques.")
            return

        dates = [datetime.fromisoformat(h["date"]) for h in filtered_health_data]
        metrics = {
            "Body Battery": [h.get("bodyBatteryMostRecentValue") for h in filtered_health_data],
            "Calories Total": [h.get("totalKilocalories") for h in filtered_health_data],
            "Calories Active": [h.get("activeKilocalories") for h in filtered_health_data],
            "Heart Rate Resting": [h.get("restingHeartRate") for h in filtered_health_data],
            "Respiration Average": [h.get("avgWakingRespirationValue") for h in filtered_health_data],
            "SpO2 Average": [h.get("averageSpo2") for h in filtered_health_data],
            "Steps": [h.get("totalSteps") for h in filtered_health_data],
            "Stress Total": [h.get("totalStressDuration") for h in filtered_health_data],
        }

        data = pd.DataFrame({"Date": dates, **metrics})
        data.set_index("Date", inplace=True)

        for col in data.columns:
            data[f"{col}_MA"] = data[col].rolling(window=14, min_periods=1).mean()
            data[f"{col}_Std"] = data[col].rolling(window=14, min_periods=1).std()
            data[f"{col}_CI"] = 1.96 * (data[f"{col}_Std"] / np.sqrt(14))

        # App theme accent color
        color = "#4CC9F0"

        x = [d.date().isoformat() for d in data.index]

        for metric in metrics.keys():
            y = [None if pd.isna(v) else float(v) for v in data[metric].tolist()]
            y_ma = [None if pd.isna(v) else float(v) for v in data[f"{metric}_MA"].tolist()]
            y_ci = [None if pd.isna(v) else float(v) for v in data[f"{metric}_CI"].tolist()]

            write_timeseries_chart_html(
                os.path.join(output_dir, f"{metric.replace(' ', '_').lower()}.html"),
                title=f"{metric} (4 dernières années)",
                x=x,
                y=y,
                y_label=metric,
                color=color,
                y_ma=y_ma,
                y_ci=y_ci,
            )

        _write_graph_cache(
            output_dir,
            {
                "engine": "echarts",
                "source": self.data_file,
                "source_mtime": src_mtime,
                "echarts_mtime": echarts_mtime,
            },
        )

        logging.info("Graphiques interactifs de santé générés avec succès.")
