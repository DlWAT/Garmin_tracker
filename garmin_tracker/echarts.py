import json
import os
import re
from typing import List, Literal, Optional, TypedDict


ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"


class YBand(TypedDict):
    low: float
    high: float
    color: str
    label: str


def _safe_makedirs(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def write_timeseries_chart_html(
    output_path: str,
    *,
    title: str,
    x: List[str],
    y: List[Optional[float]],
    y_label: str,
    color: str,
    y_ma: Optional[List[Optional[float]]] = None,
    y_ci: Optional[List[Optional[float]]] = None,
    y_bands: Optional[List[YBand]] = None,
    initial_window_days: int = 180,
    interaction: Literal["zoom", "fit"] = "zoom",
    primary_series: Literal["scatter", "line", "bar"] = "scatter",
) -> None:
    """Write a standalone ECharts HTML file.

    Designed to be embedded in an iframe with a fixed height.

    - x: list of date strings (ISO or display-friendly)
    - y: raw series (nullable)
    - y_ma: moving average series (nullable)
    - y_ci: confidence interval half-width (nullable). If provided with y_ma,
            renders a shaded band around y_ma.
    """

    _safe_makedirs(output_path)

    def _is_date_like(s: str) -> bool:
        # YYYY-MM or YYYY-MM-DD (optionally with extra suffix)
        return bool(re.match(r"^[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?", str(s or "")))

    use_time_axis = bool(x) and all(_is_date_like(v) for v in x)

    def _pair_series(x_vals: List[str], y_vals: List[Optional[float]]) -> list[list[object]]:
        out: list[list[object]] = []
        for xv, yv in zip(x_vals, y_vals):
            out.append([xv, yv])
        return out

    # CI band via stacked area: base = lower, band = (upper - lower)
    lower = None
    band = None
    if y_ma is not None and y_ci is not None:
        lower = []
        band = []
        for ma, ci in zip(y_ma, y_ci):
            if ma is None or ci is None:
                lower.append(None)
                band.append(None)
            else:
                lo = float(ma) - float(ci)
                hi = float(ma) + float(ci)
                lower.append(lo)
                band.append(max(0.0, hi - lo))

    option = {
        "backgroundColor": "transparent",
        "textStyle": {"color": "#EAF1FF"},
        "title": {
            "text": title,
            "left": "center",
            "textStyle": {"color": "#EAF1FF", "fontWeight": "bold", "fontSize": 14},
        },
        "grid": {"left": 48, "right": 16, "top": 46, "bottom": 36},
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "time" if use_time_axis else "category",
            **({} if use_time_axis else {"data": x}),
            "axisLabel": {"color": "rgba(234, 241, 255, 0.72)", "hideOverlap": True},
            "axisLine": {"lineStyle": {"color": "rgba(234, 241, 255, 0.15)"}},
            "axisTick": {"show": False},
        },
        "yAxis": {
            "type": "value",
            "name": y_label,
            "nameTextStyle": {"color": "rgba(234, 241, 255, 0.72)"},
            "axisLabel": {"color": "rgba(234, 241, 255, 0.72)"},
            "splitLine": {"lineStyle": {"color": "rgba(234, 241, 255, 0.08)"}},
        },
        "series": [],
    }

    # Interaction mode:
    # - zoom: show a sliding window and pan/zoom via ECharts dataZoom (keeps Y axis visible)
    # - fit: no zoom controls, render full interval to the iframe width
    if interaction == "zoom":
        option["grid"]["bottom"] = 58
        option["dataZoom"] = [
            {
                "type": "inside",
                "xAxisIndex": 0,
                "filterMode": "none",
                "zoomOnMouseWheel": True,
                "moveOnMouseMove": True,
                "moveOnMouseWheel": True,
            },
            {
                "type": "slider",
                "xAxisIndex": 0,
                "height": 18,
                "bottom": 10,
                "borderColor": "rgba(234, 241, 255, 0.10)",
                "backgroundColor": "rgba(255, 255, 255, 0.02)",
                "fillerColor": "rgba(76, 201, 240, 0.18)",
                "handleStyle": {
                    "color": "rgba(255, 77, 141, 0.85)",
                    "borderColor": "rgba(255, 77, 141, 0.25)",
                },
                "textStyle": {"color": "rgba(234, 241, 255, 0.65)"},
                "showDetail": False,
            },
        ]

    def _primary_series_obj() -> dict:
        base = {
            "name": "Raw",
            "type": primary_series,
            "data": _pair_series(x, y) if use_time_axis else y,
        }
        if primary_series == "scatter":
            base.update(
                {
                    "symbolSize": 5,
                    "itemStyle": {"color": color, "opacity": 0.9},
                }
            )
        elif primary_series == "line":
            base.update(
                {
                    "showSymbol": False,
                    "smooth": True,
                    "lineStyle": {"width": 2, "color": color},
                }
            )
        else:  # bar
            base.update(
                {
                    "itemStyle": {"color": color, "opacity": 0.9},
                    "barMaxWidth": 18,
                }
            )
        return base

    raw_series = _primary_series_obj()
    if y_bands:
        raw_series["markArea"] = {
            "silent": True,
            "label": {"show": False},
            "data": [
                [
                    {"yAxis": float(b["low"]), "itemStyle": {"color": b["color"]}},
                    {"yAxis": float(b["high"])},
                ]
                for b in y_bands
            ],
        }
    option["series"].append(raw_series)

    if y_ma is not None:
        option["series"].append(
            {
                "name": "MA",
                "type": "line",
                "data": _pair_series(x, y_ma) if (use_time_axis and y_ma is not None) else y_ma,
                "showSymbol": False,
                "smooth": True,
                "lineStyle": {"width": 2, "color": color},
            }
        )

    if lower is not None and band is not None:
        option["series"].append(
            {
                "name": "CI base",
                "type": "line",
                "data": _pair_series(x, lower) if use_time_axis else lower,
                "showSymbol": False,
                "lineStyle": {"opacity": 0},
                "stack": "ci",
            }
        )
        option["series"].append(
            {
                "name": "CI",
                "type": "line",
                "data": _pair_series(x, band) if use_time_axis else band,
                "showSymbol": False,
                "lineStyle": {"opacity": 0},
                "stack": "ci",
                "areaStyle": {"color": "rgba(76, 201, 240, 0.18)"},
            }
        )

    # Hide legend (keeps it clean in small iframes)
    option["legend"] = {"show": False}

    html = f"""<!doctype html>
<html lang=\"fr\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <script src=\"{ECHARTS_CDN}\"></script>
    <style>
        html, body {{ height: 100%; margin: 0; background: transparent; overflow: hidden; }}
        #chart {{ width: 100%; height: 100%; }}
    </style>
</head>
<body>
    <div id=\"chart\"></div>
    <script>
        const el = document.getElementById('chart');
        const option = {json.dumps(option, ensure_ascii=False)};

        // The visible window is ~6 months by default.
        const VISIBLE_DAYS = Math.max(30, Number({int(initial_window_days)}) || 180);
        const MODE = {json.dumps(interaction)};

        function parseISODatePrefix(s) {{
            // Accept YYYY-MM-DD[...] or YYYY-MM
            if (!s || typeof s !== 'string') return null;
            const m = s.match(/^([0-9]{{4}})-([0-9]{{2}})(?:-([0-9]{{2}}))?/);
            if (!m) return null;
            const y = Number(m[1]);
            const mo = Number(m[2]);
            const d = m[3] ? Number(m[3]) : 1;
            if (!y || !mo || !d) return null;
            const dt = new Date(Date.UTC(y, mo - 1, d));
            return isNaN(dt.getTime()) ? null : dt;
        }}

        function extractXLabels(opt) {{
            // Category axis: xAxis.data
            if (opt && opt.xAxis && opt.xAxis.type === 'category' && Array.isArray(opt.xAxis.data)) {{
                return opt.xAxis.data;
            }}
            // Time axis: derive from first series data: [x, y]
            const s0 = (opt && Array.isArray(opt.series) && opt.series.length) ? opt.series[0] : null;
            const data = (s0 && Array.isArray(s0.data)) ? s0.data : [];
            const out = [];
            for (const pt of data) {{
                if (Array.isArray(pt) && pt.length >= 2) out.push(pt[0]);
            }}
            return out;
        }}

        function computeSpanDays(labels) {{
            let minT = null;
            let maxT = null;
            for (const v of (labels || [])) {{
                const d = parseISODatePrefix(v);
                if (!d) continue;
                const t = d.getTime();
                if (minT === null || t < minT) minT = t;
                if (maxT === null || t > maxT) maxT = t;
            }}
            if (minT === null || maxT === null) return null;
            const days = Math.round((maxT - minT) / (24 * 3600 * 1000)) + 1;
            return Math.max(1, days);
        }}

        const chart = echarts.init(el, null, {{ renderer: 'canvas' }});

        function setInitialWindow() {{
            if (MODE !== 'zoom') return;
            const labels = extractXLabels(option);
            const n = labels && labels.length ? labels.length : 0;
            if (n <= 1) return;

            const spanDays = computeSpanDays(labels);
            let startPct = 0;
            let endPct = 100;

            if (spanDays !== null) {{
                const windowDays = Math.min(spanDays, VISIBLE_DAYS);
                startPct = ((spanDays - windowDays) / spanDays) * 100;
                endPct = 100;
            }} else {{
                const windowPoints = Math.min(n, VISIBLE_DAYS);
                startPct = ((n - windowPoints) / n) * 100;
                endPct = 100;
            }}

            if (Array.isArray(option.dataZoom)) {{
                for (const dz of option.dataZoom) {{
                    dz.start = startPct;
                    dz.end = endPct;
                }}
            }}
        }}

        setInitialWindow();
        chart.setOption(option);
        window.addEventListener('resize', () => {{
            chart.resize();
        }});
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
