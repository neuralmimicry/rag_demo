import os
import time

from flask import Flask, render_template, send_from_directory, request

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


@app.route("/")
def index() -> str:
    return render_template("index.html", current_user=None, api_base=API_BASE)


@app.route("/playground")
def playground() -> str:
    return render_template("playground.html", current_user=None, api_base=API_BASE)


@app.route("/public/<path:filename>")
def public_asset(filename: str):
    return send_from_directory(PUBLIC_DIR, filename)


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(PUBLIC_DIR, "favicon.ico")


@app.route("/login")
def login() -> str:
    return render_template("login.html", error=None, api_base=API_BASE)


@app.route("/setup")
def setup() -> str:
    return render_template("setup.html", error=None, api_base=API_BASE)


@app.route("/health")
def health() -> dict:
    return {"status": "ok"}


@app.route(METRICS_PATH)
def metrics():
    if not METRICS_ENABLED:
        return {"error": "metrics_disabled"}, 404
    UPTIME.set(time.time() - APP_START_TIME)
    payload = generate_latest()
    return payload, 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.before_request
def before_request():
    if METRICS_ENABLED:
        request._metrics_start = time.time()
        INFLIGHT.inc()


@app.after_request
def after_request(response):
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
