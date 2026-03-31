"""Lightweight frontend-only Flask server for Refiner static UI pages.

This service serves templates/static assets and exposes optional Prometheus
metrics. It is intentionally separate from the backend API process so UI
hosting can be deployed independently when needed.
"""

import os
import time

from flask import Flask, render_template, send_from_directory, request
from versioning import get_public_version_info, get_version_info

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "web", "public")
API_BASE = os.getenv("REFINER_API_BASE", "").strip().rstrip("/")
APP_START_TIME = time.time()

METRICS_PATH = (os.getenv("REFINER_METRICS_PATH", "/metrics") or "/metrics").strip()
if not METRICS_PATH.startswith("/"):
    METRICS_PATH = f"/{METRICS_PATH}"

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

    PROMETHEUS_AVAILABLE = True
except Exception:
    PROMETHEUS_AVAILABLE = False

METRICS_ENABLED = os.getenv("REFINER_METRICS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"} and PROMETHEUS_AVAILABLE

if METRICS_ENABLED:
    REQUEST_COUNT = Counter(
        "refiner_frontend_http_requests_total",
        "Total HTTP requests (frontend)",
        ["method", "path", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "refiner_frontend_http_request_duration_seconds",
        "HTTP request duration in seconds (frontend)",
        ["method", "path"],
    )
    INFLIGHT = Gauge("refiner_frontend_http_inflight_requests", "In-flight HTTP requests (frontend)")
    UPTIME = Gauge("refiner_frontend_uptime_seconds", "Frontend uptime in seconds")

app = Flask(__name__, static_folder="web/static", template_folder="web/templates")


@app.context_processor
def inject_template_globals():
    """Expose shared template metadata."""
    return {"app_version": get_version_info()}


@app.route("/")
def index() -> str:
    """Render the main landing page."""
    return render_template("index.html", current_user=None, api_base=API_BASE)


@app.route("/playground")
def playground() -> str:
    """Render the playground UI."""
    return render_template("playground.html", current_user=None, api_base=API_BASE)


@app.route("/public/<path:filename>")
def public_asset(filename: str):
    """Serve assets from ``web/public``."""
    return send_from_directory(PUBLIC_DIR, filename)


@app.route("/favicon.ico")
def favicon():
    """Serve the site favicon."""
    return send_from_directory(PUBLIC_DIR, "favicon.ico")


@app.route("/login")
def login() -> str:
    """Render login page shell."""
    return render_template("login.html", error=None, api_base=API_BASE)


@app.route("/setup")
def setup() -> str:
    """Render first-user setup page shell."""
    return render_template("setup.html", error=None, api_base=API_BASE)


@app.route("/health")
def health() -> dict:
    """Health endpoint used by container/runtime checks."""
    return {"status": "ok", "version": get_public_version_info()["version"]}


@app.route("/api/version")
def api_version() -> dict:
    """Version endpoint for standalone frontend deployments."""
    return get_public_version_info()


@app.route(METRICS_PATH)
def metrics():
    """Expose Prometheus metrics when enabled."""
    if not METRICS_ENABLED:
        return {"error": "metrics_disabled"}, 404
    UPTIME.set(time.time() - APP_START_TIME)
    payload = generate_latest()
    return payload, 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.before_request
def before_request():
    """Capture per-request start state for metrics."""
    if METRICS_ENABLED:
        request._metrics_start = time.time()
        INFLIGHT.inc()


@app.after_request
def after_request(response):
    """Record latency/count metrics and return response unchanged."""
    if METRICS_ENABLED:
        try:
            path_label = request.url_rule.rule if request.url_rule else request.path
            elapsed = time.time() - getattr(request, "_metrics_start", time.time())
            REQUEST_LATENCY.labels(request.method, path_label).observe(elapsed)
            REQUEST_COUNT.labels(request.method, path_label, response.status_code).inc()
        finally:
            INFLIGHT.dec()
    return response


if __name__ == "__main__":
    host = os.getenv("REFINER_FRONTEND_HOST") or os.getenv("REFINER_HOST", "0.0.0.0")
    port = int(os.getenv("REFINER_FRONTEND_PORT") or os.getenv("PORT", "8080"))
    debug = os.getenv("REFINER_DEBUG", "0") in {"1", "true", "yes"}
    app.run(host=host, port=port, debug=debug, threaded=True)
