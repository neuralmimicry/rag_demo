from __future__ import annotations

from typing import Callable


def register_auth_routes(
    app,
    *,
    login: Callable,
    oidc_login: Callable,
    oidc_callback: Callable,
    api_oidc_exchange: Callable,
    sso_login: Callable,
    logout: Callable,
    api_login: Callable,
    api_setup: Callable,
    api_sso_issue: Callable,
    api_logout: Callable,
    api_session: Callable,
    api_profile: Callable,
) -> None:
    """Register authentication/session/profile routes."""
    app.add_url_rule("/login", view_func=login, methods=["GET", "POST"])
    app.add_url_rule("/oidc/login", view_func=oidc_login)
    app.add_url_rule("/oidc/callback", view_func=oidc_callback)
    app.add_url_rule("/api/oidc/exchange", view_func=api_oidc_exchange, methods=["POST"])
    app.add_url_rule("/sso", view_func=sso_login)
    app.add_url_rule("/logout", view_func=logout)
    app.add_url_rule("/api/login", view_func=api_login, methods=["POST"])
    app.add_url_rule("/api/setup", view_func=api_setup, methods=["POST"])
    app.add_url_rule("/api/sso/issue", view_func=api_sso_issue, methods=["POST"])
    app.add_url_rule("/api/logout", view_func=api_logout, methods=["POST"])
    app.add_url_rule("/api/session", view_func=api_session)
    app.add_url_rule("/api/profile", view_func=api_profile, methods=["GET", "POST"])

