# Changelog - Amélioration des Graphes ECharts

**Date:** 26-27 janvier 2026  
**Objectif:** Optimiser les graphes d'activité avec des limites Y-axis personnalisées, coloration par zone HR, et distinctions par sport

---

## 📋 Objectifs Atteints

### ✅ Y-Axis Personnalisées par Métrique
- **HR graphs**: Y-max = FC max (zone 5 max)
- **Running pace**: 3.0-7.0 min/km (fixed range)
- **Swimming pace**: 1.0-3.0 min/100m (fixed range)
- **Cadence**: 0-200 bpm pour tous les sports

### ✅ Coloration des Points HR par Zone
- Points individuels colorés selon zone HR (Z1-Z5)
- Suppression des markArea (zone bands) au profit de per-point coloring
- Meilleure visibilité lors du zoom/pan

### ✅ Formatage Pace
- Format MM:SS sans décimales
- Ticks tous les 15 secondes pour meilleure lisibilité

### ✅ Coach Mode Styling
- Détection: `viewing_user_id != user_id`
- Gradient de fond pour distinguer visuellement

---

## 📝 Fichiers Modifiés

### 1. `garmin_tracker/echarts.py`
**Changements:**
- Ajout paramètres: `y_axis_min_override`, `y_axis_max_override`, `y_series_colors`, `is_pace_graph`
- Logique Y-axis: utilise overrides si fournis, sinon auto-computation
- Per-point coloring: construit itemStyle dictionnaires pour chaque point scatter
- Format pace: `_format_pace_label()` (déjà existant, utilisé par ticks)

**Lignes affectées:** ~25-36 (signature), ~180-202 (Y-axis), ~265-300 (per-point colors)

### 2. `garmin_tracker/activity_manager.py`
**Changements:**
- Import: `from typing import Optional`
- Fonction `_assign_zone_colors(hr_values, zones)`: assigne couleurs RGBA par zone HR
- `plot_interactive_graphs()`: 
  - Pace: 3.0-7.0 min/km + ticks
  - HR: zone colors + Y-max = FC max
- `plot_interactive_graphs_by_type()`:
  - `pace_min_km`: 3.0-7.0 min/km (running)
  - `pace_min_100m`: 1.0-3.0 min/100m (swimming)
  - `avg_hr`: zone colors + Y-max = FC max
  - `cadence`/`spm`: 0-200 range

**Lignes affectées:** Ligne 10 (import), ~52-73 (fonction), ~232-259 (synthèse), ~416-475 (par type)

### 3. `garmin_tracker/webapp.py`
**Changements (via agent):**
- Fonction `add_chart()`: nouveaux paramètres optionnels
  - `y_axis_min_override`, `y_axis_max_override`
  - `y_series_colors`, `is_pace_graph`
- Conversion zones HR: format API → format compatible pour `_assign_zone_colors()`
- Application des paramètres par sport:
  - **Running**: HR (zone colors + FC max), Pace (3-7 min/km), Cadence (0-200)
  - **Cycling**: HR (zone colors + FC max), Speed, Power
  - **Swimming**: Pace (1-3 min/100m), Cadence (0-200), Swolf, Speed, HR (zone colors + FC max)
  - **Strength**: HR (zone colors + FC max), Power
  - **Others**: HR (zone colors + FC max), Speed, Power

### 4. `templates/base.html`
**Changements:**
- Ligne 49: Conditional `data-coach-mode="true"` quand `viewing_user_id != user_id`

### 5. `static/common.css`
**Changements:**
- Lignes 313-315: `.page[data-coach-mode="true"]` avec gradient background
- Couleurs: gradient bleu/violet semi-transparent (0.8 opacity)

---

## 🎨 Détails Techniques

### Zone Colors (HR)
```python
Z1: rgba(76, 201, 240, 0.9)      # Light blue
Z2: rgba(72, 219, 251, 0.9)      # Lighter blue
Z3: rgba(255, 223, 0, 0.9)       # Yellow
Z4: rgba(255, 140, 0, 0.9)       # Orange
Z5: rgba(255, 77, 141, 0.9)      # Red/Pink
```

### Y-Axis Overrides
| Métrique | Min | Max | Notes |
|----------|-----|-----|-------|
| pace_min_km | 3.0 | 7.0 | Running |
| pace_min_100m | 1.0 | 3.0 | Swimming |
| avg_hr | auto | FC max | HR max = Zone 5 max |
| cadence/spm | 0.0 | 200.0 | Tous sports |

---

## 🧪 État de Test

### ✅ Complété
- Code syntaxe valide (pas d'erreurs Python)
- Serveur Flask démarre sans erreurs
- Graphes synthèse (activity_manager): régénérés avec paramètres
- Graphes détail (webapp.py): régénérés avec paramètres
- Distinction pace_min_km vs pace_min_100m
- Coach mode styling en place

### 🔄 À Valider en Navigateur
1. **HR Graphes**: Vérifier points colorés par zone, Y-max = FC max
2. **Running Pace**: 3-7 min/km, MM:SS format, ticks 15s
3. **Swimming Pace**: 1-3 min/100m, MM:SS format, ticks 15s
4. **Cadence**: 0-200 range, tous sports
5. **Coach Mode**: Background gradient visible quand viewing athlete data

---

## 📦 Cache Clearing

Tous les fichiers HTML en cache supprimés:
- `static/activity/by_type/*.html`
- `static/activity/detail/*.html`
- `static/dashboard/**/*.html`

**Résultat:** Graphes se régénèrent automatiquement au prochain page load avec nouveaux paramètres

---

## 🚀 Prêt pour Commit

**Fichiers à commiter:**
```
garmin_tracker/echarts.py
garmin_tracker/activity_manager.py
garmin_tracker/webapp.py
templates/base.html
static/common.css
CHANGELOG.md (ce fichier)
```

**Fichiers à NE PAS commiter:**
- `static/activity/by_type/*.html` (cache)
- `static/activity/detail/*.html` (cache)
- `static/dashboard/**/*.html` (cache)
- `.venv312/` (venv)
- `__pycache__/` (bytecode)

---

## 📝 Message Commit Recommandé

```
feat: Amélioration des graphes ECharts avec limites Y-axis personnalisées et coloration zones HR

- Ajouter paramètres Y-axis min/max override à echarts.py
- Implémenter per-point coloring pour scatter plots par zone HR
- Distinction pace_min_km (3-7 min/km) vs pace_min_100m (1-3 min/100m)
- Cadence fixé à 0-200 bpm
- HR Y-max = FC max pour tous les graphes HR
- Convertir zones HR API vers format compatible dans webapp.py
- Coach mode background styling (data-coach-mode attribute)
- Format pace MM:SS sans décimales avec ticks 15s

Fichiers modifiés:
- garmin_tracker/echarts.py: Y-axis overrides + per-point colors
- garmin_tracker/activity_manager.py: _assign_zone_colors() + metric-specific limits
- garmin_tracker/webapp.py: add_chart() enhancement + zone conversion
- templates/base.html: Coach mode detection
- static/common.css: Coach mode styling
```

---

## ⚠️ Notes Importantes

1. **Zones HR Compatibilité**: Format conversion nécessaire de `zoneHighBoundary` API vers `max` interne
2. **Per-Point Coloring**: Requiert ECharts scatter series avec itemStyle pour chaque point
3. **Pace Ticks**: Appel à `_generate_pace_ticks()` depuis `activity_manager`
4. **Cache Clearing**: Les graphes HTML devront être régénérés manuellement si besoin

---

## 🔗 Contexte du Projet

Cet ensemble de modifications fait suite à une série d'améliorations visuelles sur l'application de suivi d'activités Garmin. L'objectif était d'améliorer la lisibilité et l'utilité des graphes en:
- Normalisant les échelles Y selon le type de métrique
- Rendant visibles les zones d'intensité HR directement sur les graphes
- Distinguant visuellement les modes de coaching vs personnel

---

**Statut:** ✅ Code complet, en attente de test navigateur complet
