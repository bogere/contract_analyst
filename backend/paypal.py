"""PayPal Subscriptions API client."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

import httpx


class PayPalError(RuntimeError):
    pass


def _configuration() -> tuple[str, str, str, str]:
    client_id = (os.getenv("PAYPAL_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("PAYPAL_CLIENT_SECRET") or "").strip()
    mode = (os.getenv("PAYPAL_MODE") or "sandbox").strip().lower()
    webhook_id = (os.getenv("PAYPAL_WEBHOOK_ID") or "").strip()
    if not client_id or not client_secret:
        raise PayPalError("PayPal is not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.")
    if mode not in {"sandbox", "live"}:
        raise PayPalError("PAYPAL_MODE must be either 'sandbox' or 'live'.")
    base_url = (
        "https://api-m.sandbox.paypal.com"
        if mode == "sandbox"
        else "https://api-m.paypal.com"
    )
    return base_url, client_id, client_secret, webhook_id


async def _request(
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
) -> dict[str, Any]:
    base_url, client_id, client_secret, _ = _configuration()
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            token_response = await client.post(
                urljoin(base_url, "/v1/oauth2/token"),
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
            )
            token_response.raise_for_status()
            try:
                token_payload = token_response.json()
            except ValueError as exc:
                raise PayPalError("PayPal returned an invalid access-token response.") from exc
            if not isinstance(token_payload, dict) or not isinstance(token_payload.get("access_token"), str):
                raise PayPalError("PayPal did not return an API access token.")

            response = await client.request(
                method,
                urljoin(base_url, path),
                headers={"Authorization": f"Bearer {token_payload['access_token']}"},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PayPalError(f"PayPal API request failed (HTTP {exc.response.status_code}).") from exc
        except httpx.HTTPError as exc:
            raise PayPalError("Could not connect to PayPal.") from exc

        if response.status_code == 204:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise PayPalError("PayPal returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise PayPalError("PayPal returned an unexpected response.")
        return payload


async def create_subscription(
    plan_id: str,
    user_id: str,
    return_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/v1/billing/subscriptions",
        body={
            "plan_id": plan_id,
            "custom_id": user_id,
            "application_context": {
                "brand_name": "Contract Analyst",
                "locale": "en-US",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        },
    )


async def get_subscription(subscription_id: str) -> dict[str, Any]:
    return await _request("GET", f"/v1/billing/subscriptions/{subscription_id}")


async def get_plan(plan_id: str) -> dict[str, Any]:
    return await _request("GET", f"/v1/billing/plans/{plan_id}")


def validate_monthly_plan(plan: dict[str, Any], monthly_price: str) -> None:
    cycles = plan.get("billing_cycles")
    regular_cycles = [
        cycle
        for cycle in cycles
        if isinstance(cycle, dict) and cycle.get("tenure_type") == "REGULAR"
    ] if isinstance(cycles, list) else []
    if plan.get("status") != "ACTIVE" or len(regular_cycles) != 1:
        raise PayPalError("The configured PayPal plan must be active and have one regular billing cycle.")

    cycle = regular_cycles[0]
    frequency = cycle.get("frequency")
    pricing = cycle.get("pricing_scheme")
    fixed_price = pricing.get("fixed_price") if isinstance(pricing, dict) else None
    if (
        not isinstance(frequency, dict)
        or frequency.get("interval_unit") != "MONTH"
        or frequency.get("interval_count") != 1
        or not isinstance(fixed_price, dict)
        or fixed_price.get("currency_code") != "USD"
    ):
        raise PayPalError("The configured PayPal plan must renew monthly in USD.")
    value = fixed_price.get("value")
    try:
        actual_price = Decimal(str(value))
        expected_price = Decimal(monthly_price)
    except (InvalidOperation, ValueError) as exc:
        raise PayPalError("The configured PayPal plan has an invalid monthly price.") from exc
    if actual_price != expected_price:
        raise PayPalError(
            f"The configured PayPal plan price must be USD {expected_price:.2f} per month."
        )


async def cancel_subscription(subscription_id: str, reason: str) -> None:
    await _request(
        "POST",
        f"/v1/billing/subscriptions/{subscription_id}/cancel",
        body={"reason": reason},
    )


async def verify_webhook(
    headers: dict[str, str],
    event: dict[str, object],
) -> bool:
    base_url, _, _, webhook_id = _configuration()
    if not webhook_id:
        raise PayPalError("PAYPAL_WEBHOOK_ID is required to verify PayPal webhooks.")

    header_names = {
        "auth_algo": "paypal-auth-algo",
        "cert_url": "paypal-cert-url",
        "transmission_id": "paypal-transmission-id",
        "transmission_sig": "paypal-transmission-sig",
        "transmission_time": "paypal-transmission-time",
    }
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    verification_payload: dict[str, object] = {
        field: normalized_headers[header_name]
        for field, header_name in header_names.items()
        if header_name in normalized_headers
    }
    if len(verification_payload) != len(header_names):
        return False
    verification_payload["webhook_id"] = webhook_id
    verification_payload["webhook_event"] = event
    result = await _request(
        "POST",
        "/v1/notifications/verify-webhook-signature",
        body=verification_payload,
    )
    return result.get("verification_status") == "SUCCESS"
