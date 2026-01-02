"""WSGI entrypoint for production servers.

Example:
  waitress-serve --host 0.0.0.0 --port 5000 wsgi:app

(or)
  waitress-serve --host 0.0.0.0 --port 5000 garmin_tracker.webapp:app
"""

from garmin_tracker.webapp import app
