"""Run: .venv/bin/gunicorn -c gunicorn.conf.py 'app:create_app()'"""
import os

bind = os.environ.get("BETTERASSPEN_BIND", "0.0.0.0:5173")
# Account stores and the refresh coordinator live in this single process.
workers = 1
worker_class = "gthread"
threads = 8
preload_app = False
timeout = 120
accesslog = None  # OAuth callback query strings contain authorization codes.
errorlog = "-"


def worker_exit(server, worker):
    app = worker.wsgi
    if app is not None and hasattr(app, "extensions"):
        app.extensions["refresh_runtime"].stop()
