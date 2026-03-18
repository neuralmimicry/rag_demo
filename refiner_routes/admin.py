from __future__ import annotations

from typing import Callable


def register_admin_routes(
    app,
    *,
    metrics_path: str,
    index: Callable,
    playground: Callable,
    admin_dashboard: Callable,
    public_asset: Callable,
    favicon: Callable,
    metrics: Callable,
    setup: Callable,
    health: Callable,
    capabilities_report: Callable,
    admin_stats: Callable,
    api_audit: Callable,
) -> None:
    """Register admin/system/public routes."""
    app.add_url_rule("/", view_func=index)
    app.add_url_rule("/playground", view_func=playground)
    app.add_url_rule("/admin", view_func=admin_dashboard)
    app.add_url_rule("/public/<path:filename>", view_func=public_asset)
    app.add_url_rule("/favicon.ico", view_func=favicon)
    app.add_url_rule(metrics_path, view_func=metrics)
    app.add_url_rule("/setup", view_func=setup, methods=["GET", "POST"])
    app.add_url_rule("/api/health", view_func=health)
    app.add_url_rule("/api/capabilities", view_func=capabilities_report)
    app.add_url_rule("/api/admin/stats", view_func=admin_stats)
    app.add_url_rule("/api/audit", view_func=api_audit)

