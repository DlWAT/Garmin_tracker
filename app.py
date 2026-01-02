"""Entry point (recommended).

Run locally (dev):
  python app.py

Run in deployment mode (prod):
  $env:GARMIN_TRACKER_MODE='prod'; python app.py

Alternatively (recommended for prod):
  waitress-serve --host 0.0.0.0 --port 5000 garmin_tracker.webapp:app
"""

from __future__ import annotations

import os

from garmin_tracker.webapp import app


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


if __name__ == "__main__":
    # Two modes:
    # - local (default): binds to 127.0.0.1 with debug enabled
    # - prod: binds to 0.0.0.0 with debug disabled (public IP)
    mode = (os.getenv("GARMIN_TRACKER_MODE") or "local").strip().lower()
    is_prod = mode in {"prod", "production", "deploy", "deployment"}

    host_default = "0.0.0.0" if is_prod else "127.0.0.1"
    host = (os.getenv("GARMIN_TRACKER_HOST") or host_default).strip()
    port = _int_env("GARMIN_TRACKER_PORT", 5000)

    debug_default = False if is_prod else True
    debug = _truthy(os.getenv("GARMIN_TRACKER_DEBUG")) if os.getenv("GARMIN_TRACKER_DEBUG") is not None else debug_default

    # The reloader spawns a child process and exits the parent. When running
    # via VS Code tasks/terminals (or background scripts), this can make the
    # server appear to stop.
    use_reloader = False

    if is_prod:
        try:
            from waitress import serve

            threads = _int_env("GARMIN_TRACKER_THREADS", 8)
            serve(app, host=host, port=port, threads=threads)
        except Exception:
            # Fallback to Flask dev server if waitress isn't installed.
            app.run(host=host, port=port, debug=debug, use_reloader=use_reloader)
    else:
        app.run(host=host, port=port, debug=debug, use_reloader=use_reloader)
