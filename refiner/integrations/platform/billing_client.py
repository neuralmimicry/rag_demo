"""HTTP client for the shared Billing service."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests


class BillingServiceError(RuntimeError):
    """Raised when the Billing service cannot satisfy a request."""


class BillingClient:
    def __init__(self, base_url: str, *, api_token: Optional[str] = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = (api_token or "").strip() or None
        self.timeout = max(1.0, float(timeout))
        self._session = requests.Session()

    @classmethod
    def from_env(cls) -> Optional["BillingClient"]:
        base_url = _env_first("REFINER_BILLING_API_BASE", "BILLING_API_BASE")
        if not base_url:
            return None
        timeout_raw = _env_first("REFINER_BILLING_TIMEOUT", default="10")
        try:
            timeout = float(timeout_raw)
        except Exception:
            timeout = 10.0
        return cls(
            base_url,
            api_token=_env_first("REFINER_BILLING_API_TOKEN"),
            timeout=timeout,
        )

    def account_snapshot(self, scope: str, account_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/api/internal/accounts/{quote(scope.strip())}/{quote(account_id.strip())}",
        )

    def ledger_entries(self, scope: str, account_id: str, *, limit: int = 50) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/api/internal/accounts/{quote(scope.strip())}/{quote(account_id.strip())}/ledger",
            params={"limit": max(1, min(int(limit), 500))},
        )

    def apply_token(
        self,
        scope: str,
        account_id: str,
        *,
        entry_type: str,
        delta: int,
        request_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/internal/accounts/{quote(scope.strip())}/{quote(account_id.strip())}/events",
            json_body={
                "entry_type": entry_type,
                "delta": int(delta),
                "request_id": request_id,
                "meta": meta or {},
            },
        )

    def capture_payment(
        self,
        user_id: str,
        *,
        tokens: int,
        amount_minor: Optional[int] = None,
        currency: Optional[str] = None,
        provider: Optional[str] = None,
        payment_id: Optional[str] = None,
        checkout_flow: Optional[str] = None,
        request_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/internal/payments",
            json_body={
                "user_id": user_id,
                "tokens": int(tokens),
                "amount_minor": amount_minor,
                "currency": currency,
                "provider": provider,
                "payment_id": payment_id,
                "checkout_flow": checkout_flow,
                "request_id": request_id,
                "meta": meta or {},
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.api_token:
            raise BillingServiceError("billing_app_token_missing")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BillingServiceError(f"billing request failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text or "invalid_json_response"}
        if not response.ok:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise BillingServiceError(str(message or f"billing returned {response.status_code}"))
        if not isinstance(payload, dict):
            raise BillingServiceError("billing response was not a JSON object")
        return payload


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        value = value.strip()
        if value:
            return value
    return default
