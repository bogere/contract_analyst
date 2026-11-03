from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, call, patch

import httpx

from backend.paypal import (
    PayPalError,
    _http_error_message,
    create_monthly_plan,
    create_product,
    validate_monthly_plan,
)


def monthly_plan(
    *,
    status: str = "ACTIVE",
    interval_unit: str = "MONTH",
    interval_count: int = 1,
    currency_code: str = "USD",
    price: str = "19.00",
) -> dict[str, object]:
    return {
        "status": status,
        "billing_cycles": [
            {
                "tenure_type": "REGULAR",
                "frequency": {
                    "interval_unit": interval_unit,
                    "interval_count": interval_count,
                },
                "pricing_scheme": {
                    "fixed_price": {
                        "currency_code": currency_code,
                        "value": price,
                    },
                },
            },
        ],
    }


class ValidateMonthlyPlanTests(unittest.TestCase):
    def test_accepts_active_plan_with_matching_monthly_usd_price(self) -> None:
        validate_monthly_plan(monthly_plan(), "19.00")

    def test_rejects_inactive_plan(self) -> None:
        with self.assertRaises(PayPalError):
            validate_monthly_plan(monthly_plan(status="INACTIVE"), "19.00")

    def test_rejects_non_monthly_billing(self) -> None:
        with self.assertRaises(PayPalError):
            validate_monthly_plan(monthly_plan(interval_unit="YEAR"), "19.00")

    def test_rejects_mismatched_price_or_currency(self) -> None:
        with self.assertRaises(PayPalError):
            validate_monthly_plan(monthly_plan(price="20.00"), "19.00")
        with self.assertRaises(PayPalError):
            validate_monthly_plan(monthly_plan(currency_code="EUR"), "19.00")


class PayPalCatalogTests(unittest.IsolatedAsyncioTestCase):
    @patch("backend.paypal._request", new_callable=AsyncMock)
    async def test_create_product_uses_service_catalog(self, request: AsyncMock) -> None:
        request.return_value = {"id": "PROD-123"}

        product = await create_product()

        self.assertEqual(product["id"], "PROD-123")
        request.assert_awaited_once_with(
            "POST",
            "/v1/catalogs/products",
            body={
                "name": "Contract Analyst",
                "description": "Contract analysis subscription plans.",
                "type": "SERVICE",
                "category": "SOFTWARE",
            },
        )

    @patch("backend.paypal._request", new_callable=AsyncMock)
    async def test_create_monthly_plan_activates_created_plan(
        self,
        request: AsyncMock,
    ) -> None:
        request.side_effect = [{"id": "P-PLAN-123", "status": "CREATED"}, {}]

        plan_id = await create_monthly_plan(
            "PROD-123",
            "Contract Analyst Pro",
            "Pro plan",
            "19.00",
        )

        self.assertEqual(plan_id, "P-PLAN-123")
        self.assertEqual(
            request.await_args_list,
            [
                call(
                    "POST",
                    "/v1/billing/plans",
                    body={
                        "product_id": "PROD-123",
                        "name": "Contract Analyst Pro",
                        "description": "Pro plan",
                        "billing_cycles": [
                            {
                                "frequency": {
                                    "interval_unit": "MONTH",
                                    "interval_count": 1,
                                },
                                "tenure_type": "REGULAR",
                                "sequence": 1,
                                "total_cycles": 0,
                                "pricing_scheme": {
                                    "fixed_price": {
                                        "value": "19.00",
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
                ),
                call("POST", "/v1/billing/plans/P-PLAN-123/activate"),
            ],
        )

    @patch("backend.paypal._request", new_callable=AsyncMock)
    async def test_create_monthly_plan_does_not_reactivate_active_plan(
        self,
        request: AsyncMock,
    ) -> None:
        request.return_value = {"id": "P-PLAN-123", "status": "ACTIVE"}

        plan_id = await create_monthly_plan(
            "PROD-123",
            "Contract Analyst Pro",
            "Pro plan",
            "19.00",
        )

        self.assertEqual(plan_id, "P-PLAN-123")
        request.assert_awaited_once()

    @patch("backend.paypal._request", new_callable=AsyncMock)
    async def test_create_monthly_plan_identifies_plan_creation_failure(
        self,
        request: AsyncMock,
    ) -> None:
        request.side_effect = PayPalError(
            "PayPal API request failed (HTTP 422): INVALID_REQUEST."
        )

        with self.assertRaisesRegex(PayPalError, "Could not create the 'Contract Analyst Pro'"):
            await create_monthly_plan("PROD-123", "Contract Analyst Pro", "Pro plan", "19.00")

    def test_http_error_message_includes_paypal_validation_details(self) -> None:
        response = httpx.Response(
            422,
            json={
                "name": "INVALID_REQUEST",
                "message": "Request is not well-formed.",
                "details": [
                    {
                        "field": "/billing_cycles/0/total_cycles",
                        "issue": "INVALID_PARAMETER_SYNTAX",
                        "description": "Value is invalid.",
                    },
                ],
                "debug_id": "DEBUG-123",
            },
        )

        message = _http_error_message(response)

        self.assertIn("HTTP 422", message)
        self.assertIn("INVALID_PARAMETER_SYNTAX", message)
        self.assertIn("/billing_cycles/0/total_cycles", message)
        self.assertIn("DEBUG-123", message)


if __name__ == "__main__":
    unittest.main()
