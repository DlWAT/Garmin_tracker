"""Entry point (recommended).

Run:
  python app.py

or:
  flask --app garmin_tracker.webapp run
"""

from garmin_tracker.webapp import app


if __name__ == "__main__":
    # The reloader spawns a child process and exits the parent. When running
    # via VS Code tasks/terminals, this can make the server appear to stop.
    app.run(debug=True, use_reloader=False)
