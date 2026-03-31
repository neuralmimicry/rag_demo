"""API documentation integration for Refiner web service.

Provides Swagger UI and OpenAPI specification endpoints.
"""

import os
from typing import Any, Callable, Dict, Optional
from flask import Flask, send_from_directory, jsonify
import yaml
import logging
from versioning import get_public_version_info

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPENAPI_SPEC_PATH = os.path.join(BASE_DIR, "openapi_refiner.yaml")


def load_openapi_spec() -> Dict[str, Any]:
    """Load and parse OpenAPI specification from YAML file."""
    version = get_public_version_info()
    try:
        with open(OPENAPI_SPEC_PATH, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        if not isinstance(spec, dict):
            spec = {}
    except Exception as e:
        logger.error(f"Failed to load OpenAPI spec: {e}")
        spec = {
            "openapi": "3.0.3",
            "info": {
                "title": "Refiner API",
                "version": version["version"],
                "description": "API documentation unavailable"
            },
            "paths": {}
        }
    info = spec.setdefault("info", {})
    if isinstance(info, dict):
        info["version"] = version["version"]
    return spec


def register_api_docs(app: Flask) -> None:
    """Register API documentation routes with Flask app.

    Provides:
    - /api/docs - Swagger UI interface
    - /api/docs/openapi.yaml - OpenAPI specification (YAML)
    - /api/docs/openapi.json - OpenAPI specification (JSON)
    """

    # Swagger UI HTML template (self-contained, no CDN required)
    SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Refiner API Documentation</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
    <style>
        body {
            margin: 0;
            padding: 0;
        }
        .swagger-ui .topbar {
            background-color: #1a1a2e;
        }
        .swagger-ui .topbar .download-url-wrapper {
            display: none;
        }
        .swagger-ui .info .title {
            color: #0f3460;
        }
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
    <script>
        window.onload = function() {
            const ui = SwaggerUIBundle({
                url: '/api/docs/openapi.json',
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "StandaloneLayout",
                defaultModelsExpandDepth: 1,
                defaultModelExpandDepth: 1,
                docExpansion: "list",
                filter: true,
                showExtensions: true,
                showCommonExtensions: true,
                tryItOutEnabled: true
            });
            window.ui = ui;
        };
    </script>
</body>
</html>"""

    @app.route("/api/docs", methods=["GET"])
    @app.route("/api/docs/", methods=["GET"])
    def api_docs_ui():
        """Serve Swagger UI interface."""
        return SWAGGER_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.route("/api/docs/openapi.yaml", methods=["GET"])
    def api_docs_yaml():
        """Serve OpenAPI specification in YAML format."""
        try:
            content = yaml.safe_dump(load_openapi_spec(), sort_keys=False)
            return content, 200, {"Content-Type": "text/yaml; charset=utf-8"}
        except Exception as e:
            logger.error(f"Failed to serve OpenAPI YAML: {e}")
            return {"error": "Specification not available"}, 500

    @app.route("/api/docs/openapi.json", methods=["GET"])
    def api_docs_json():
        """Serve OpenAPI specification in JSON format."""
        spec = load_openapi_spec()
        return jsonify(spec)

    logger.info("API documentation routes registered at /api/docs")


def register_health_endpoints(
    app: Flask,
    *,
    stt_server_url: str = "",
    redis_enabled: Optional[Callable[[], bool]] = None,
    continuum_enabled: Optional[Callable[[], bool]] = None,
) -> None:
    """Register health and version endpoints."""

    @app.route("/health", methods=["GET"])
    def health_check():
        """Service health check endpoint."""
        from datetime import datetime, timezone
        version = get_public_version_info()

        # Check optional service availability
        services = {}

        # Check STT service
        if stt_server_url:
            try:
                import requests
                resp = requests.get(f"{stt_server_url.rstrip('/')}/health", timeout=2)
                services["stt"] = "available" if resp.ok else "unavailable"
            except Exception:
                services["stt"] = "unavailable"

        # Check Redis
        try:
            import redis as _redis  # noqa: F401

            if redis_enabled and redis_enabled():
                services["redis"] = "connected"
            else:
                services["redis"] = "disabled"
        except Exception:
            services["redis"] = "unavailable"

        # Check Continuum
        try:
            services["continuum"] = "enabled" if continuum_enabled and continuum_enabled() else "disabled"
        except Exception:
            services["continuum"] = "unavailable"

        # Determine overall status
        status = "healthy"
        if services.get("stt") == "unavailable":
            status = "degraded"

        return jsonify({
            "status": status,
            "version": version["version"],
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "services": services
        })

    if "api_version" not in app.view_functions:
        @app.route("/api/version", methods=["GET"])
        def api_version():
            """API version information."""
            return jsonify(get_public_version_info())

    logger.info("Health and version endpoints registered")


def add_api_documentation_support(
    app: Flask,
    *,
    stt_server_url: str = "",
    redis_enabled: Optional[Callable[[], bool]] = None,
    continuum_enabled: Optional[Callable[[], bool]] = None,
) -> None:
    """Add complete API documentation support to Flask app.

    This is the main entry point for integrating API documentation.
    Call this after app initialization but before running the server.
    """
    register_api_docs(app)
    register_health_endpoints(
        app,
        stt_server_url=stt_server_url,
        redis_enabled=redis_enabled,
        continuum_enabled=continuum_enabled,
    )

    logger.info(
        "API documentation available at: http://localhost:5555/api/docs (or configured host/port)"
    )
