from __future__ import annotations

import unittest

from backend.paypal import PayPalError, validate_monthly_plan


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


if __name__ == "__main__":
    unittest.main()
