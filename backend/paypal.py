"""PayPal Subscriptions API client."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

import httpx


class PayPalError(RuntimeError):
    pass


def _http_error_message(response: httpx.Response) -> str:
    message = f"PayPal API request failed (HTTP {response.status_code})"
    try:
        payload = response.json()
    except ValueError:
        return f"{message}."
    if not isinstance(payload, dict):
        return f"{message}."

    summary = [
        value
        for value in (payload.get("name"), payload.get("message"))
        if isinstance(value, str) and value
    ]
    details = payload.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            fields = [
                f"{key}: {detail[key]}"
                for key in ("field", "issue", "description")
                if isinstance(detail.get(key), str) and detail[key]
            ]
            if fields:
                summary.append(", ".join(fields))

    debug_id = payload.get("debug_id")
    if isinstance(debug_id, str) and debug_id:
        summary.append(f"debug ID: {debug_id}")
    return f"{message}: {'; '.join(summary)}." if summary else f"{message}."


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
            raise PayPalError(_http_error_message(exc.response)) from exc
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


async def create_product() -> dict[str, Any]:
    result = await _request(
        "POST",
        "/v1/catalogs/products",
        body={
            "name": "Contract Analyst",
            "description": "Contract analysis subscription plans.",
            "type": "SERVICE",
            "category": "SOFTWARE",
        },
    )
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise PayPalError("PayPal did not return a product ID.")
    return result


async def create_monthly_plan(
    product_id: str,
    name: str,
    description: str,
    monthly_price: str,
) -> str:
    try:
        result = await _request(
            "POST",
            "/v1/billing/plans",
            body={
                "product_id": product_id,
                "name": name,
                "description": description,
                "billing_cycles": [
                    {
                        "frequency": {"interval_unit": "MONTH", "interval_count": 1},
                        "tenure_type": "REGULAR",
                        "sequence": 1,
                        "total_cycles": 0,
                        "pricing_scheme": {
                            "fixed_price": {
                                "value": monthly_price,
                                "currency_code": "USD",
                            },
                        },
                    },
                ],
                "payment_preferences": {
                    "auto_bill_outstanding": True,
                    "setup_fee_failure_action": "CONTINUE",
                    "payment_failure_threshold": 3,
                },
            },
        )
    except PayPalError as exc:
        raise PayPalError(f"Could not create the '{name}' billing plan: {exc}") from exc
    plan_id = result.get("id")
    if not isinstance(plan_id, str) or not plan_id:
        raise PayPalError(f"PayPal did not return a plan ID for {name}.")

    status = result.get("status")
    if status == "ACTIVE":
        return plan_id
    if status not in {"CREATED", "INACTIVE"}:
        raise PayPalError(
            f"PayPal created the '{name}' billing plan {plan_id} with unexpected status "
            f"{status!r}; expected ACTIVE, CREATED, or INACTIVE."
        )

    try:
        await _request("POST", f"/v1/billing/plans/{plan_id}/activate")
    except PayPalError as exc:
        raise PayPalError(
            f"Could not activate the '{name}' billing plan {plan_id}: {exc}"
        ) from exc
    return plan_id


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
