from __future__ import annotations

from typing import Callable


def register_auth_routes(
    app,
    *,
    login: Callable,
    register: Callable,
    external_login: Callable,
    oidc_login: Callable,
    oidc_callback: Callable,
    api_oidc_exchange: Callable,
    sso_login: Callable,
    logout: Callable,
    api_login: Callable,
    api_login_mfa_totp: Callable,
    api_setup: Callable,
    api_register: Callable,
    api_auth_config: Callable,
    api_sso_issue: Callable,
    api_logout: Callable,
    api_session: Callable,
    api_authz_nginx: Callable,
    api_profile: Callable,
    api_profile_password: Callable,
    api_profile_mfa_totp_start: Callable,
    api_profile_mfa_totp_verify: Callable,
    api_profile_mfa_totp_disable: Callable,
    api_profile_passkeys_register_options: Callable,
    api_profile_passkeys_register_verify: Callable,
    api_profile_passkey_delete: Callable,
    api_passkeys_authenticate_options: Callable,
    api_passkeys_authenticate_verify: Callable,
    api_users: Callable,
    api_user_password: Callable,
) -> None:
    """Register authentication/session/profile routes."""
    app.add_url_rule("/login", view_func=login, methods=["GET", "POST"])
    app.add_url_rule("/register", view_func=register, methods=["GET", "POST"])
    app.add_url_rule("/auth/external-login", view_func=external_login)
    app.add_url_rule("/oidc/login", view_func=oidc_login)
    app.add_url_rule("/oidc/callback", view_func=oidc_callback)
    app.add_url_rule("/api/oidc/exchange", view_func=api_oidc_exchange, methods=["POST"])
    app.add_url_rule("/sso", view_func=sso_login)
    app.add_url_rule("/logout", view_func=logout)
    app.add_url_rule("/api/login", view_func=api_login, methods=["POST"])
    app.add_url_rule("/api/login/mfa/totp", view_func=api_login_mfa_totp, methods=["POST"])
    app.add_url_rule("/api/setup", view_func=api_setup, methods=["POST"])
    app.add_url_rule("/api/register", view_func=api_register, methods=["POST"])
    app.add_url_rule("/api/auth/config", view_func=api_auth_config)
    app.add_url_rule("/api/sso/issue", view_func=api_sso_issue, methods=["POST"])
    app.add_url_rule("/api/logout", view_func=api_logout, methods=["POST"])
    app.add_url_rule("/api/session", view_func=api_session)
    app.add_url_rule("/api/authz/nginx", view_func=api_authz_nginx)
    app.add_url_rule("/api/profile", view_func=api_profile, methods=["GET", "POST"])
    app.add_url_rule("/api/profile/password", view_func=api_profile_password, methods=["POST"])
    app.add_url_rule("/api/profile/mfa/totp/start", view_func=api_profile_mfa_totp_start, methods=["POST"])
    app.add_url_rule("/api/profile/mfa/totp/verify", view_func=api_profile_mfa_totp_verify, methods=["POST"])
    app.add_url_rule("/api/profile/mfa/totp/disable", view_func=api_profile_mfa_totp_disable, methods=["POST"])
    app.add_url_rule(
        "/api/profile/passkeys/register/options",
        view_func=api_profile_passkeys_register_options,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/profile/passkeys/register/verify",
        view_func=api_profile_passkeys_register_verify,
        methods=["POST"],
    )
    app.add_url_rule("/api/profile/passkeys/<credential_id>", view_func=api_profile_passkey_delete, methods=["DELETE"])
    app.add_url_rule(
        "/api/passkeys/authenticate/options",
        view_func=api_passkeys_authenticate_options,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/passkeys/authenticate/verify",
        view_func=api_passkeys_authenticate_verify,
        methods=["POST"],
    )
    app.add_url_rule("/api/users", view_func=api_users, methods=["GET", "POST"])
    app.add_url_rule("/api/users/<username>/password", view_func=api_user_password, methods=["POST"])
