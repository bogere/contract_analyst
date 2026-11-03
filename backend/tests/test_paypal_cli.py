from __future__ import annotations

import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import AsyncMock, patch

from backend.paypal import PayPalError
from backend.paypal_cli import main


class PayPalCliTests(unittest.TestCase):
    @patch("backend.paypal_cli.load_dotenv")
    @patch("backend.paypal_cli.create_product", new_callable=AsyncMock)
    @patch("backend.paypal_cli.create_monthly_plan", new_callable=AsyncMock)
    @patch.dict(os.environ, {"PAYPAL_MODE": "sandbox"}, clear=True)
    def test_creates_product_and_active_plans_without_subscribing(
        self,
        create_monthly_plan: AsyncMock,
        create_product: AsyncMock,
        _load_dotenv: unittest.mock.Mock,
    ) -> None:
        create_product.return_value = {"id": "PROD-123"}
        create_monthly_plan.side_effect = ["P-PRO-123", "P-BUSINESS-123"]
        stdout = StringIO()

        with redirect_stdout(stdout):
            result = main([])

        self.assertEqual(result, 0)
        create_product.assert_awaited_once_with()
        self.assertEqual(
            create_monthly_plan.await_args_list,
            [
                unittest.mock.call(
                    product_id="PROD-123",
                    name="Contract Analyst Pro",
                    description="For individuals reviewing contracts regularly.",
                    monthly_price="19.00",
                ),
                unittest.mock.call(
                    product_id="PROD-123",
                    name="Contract Analyst Business",
                    description="Higher monthly capacity for professional use.",
                    monthly_price="49.00",
                ),
            ],
        )
        self.assertIn("PAYPAL_PRO_PLAN_ID=P-PRO-123", stdout.getvalue())
        self.assertIn("PAYPAL_BUSINESS_PLAN_ID=P-BUSINESS-123", stdout.getvalue())

    @patch("backend.paypal_cli.load_dotenv")
    @patch("backend.paypal_cli.create_product", new_callable=AsyncMock)
    @patch("backend.paypal_cli.create_monthly_plan", new_callable=AsyncMock)
    @patch.dict(os.environ, {"PAYPAL_MODE": "sandbox"}, clear=True)
    def test_reuses_product_when_product_id_is_provided(
        self,
        create_monthly_plan: AsyncMock,
        create_product: AsyncMock,
        _load_dotenv: unittest.mock.Mock,
    ) -> None:
        create_monthly_plan.side_effect = ["P-PRO-123", "P-BUSINESS-123"]

        result = main(["--product-id", "PROD-EXISTING"])

        self.assertEqual(result, 0)
        create_product.assert_not_awaited()
        self.assertEqual(
            [call.kwargs["product_id"] for call in create_monthly_plan.await_args_list],
            ["PROD-EXISTING", "PROD-EXISTING"],
        )

    @patch("backend.paypal_cli.load_dotenv")
    @patch("backend.paypal_cli.create_product", new_callable=AsyncMock)
    @patch("backend.paypal_cli.create_monthly_plan", new_callable=AsyncMock)
    @patch.dict(os.environ, {"PAYPAL_MODE": "sandbox"}, clear=True)
    def test_creates_only_selected_plan_for_existing_product(
        self,
        create_monthly_plan: AsyncMock,
        create_product: AsyncMock,
        _load_dotenv: unittest.mock.Mock,
    ) -> None:
        create_monthly_plan.return_value = "P-BUSINESS-123"

        result = main(
            ["--product-id", "PROD-EXISTING", "--only-plan", "business"]
        )

        self.assertEqual(result, 0)
        create_product.assert_not_awaited()
        create_monthly_plan.assert_awaited_once_with(
            product_id="PROD-EXISTING",
            name="Contract Analyst Business",
            description="Higher monthly capacity for professional use.",
            monthly_price="49.00",
        )

    @patch("backend.paypal_cli.load_dotenv")
    @patch("backend.paypal_cli.create_product", new_callable=AsyncMock)
    @patch("backend.paypal_cli.create_monthly_plan", new_callable=AsyncMock)
    @patch.dict(os.environ, {"PAYPAL_MODE": "live"}, clear=True)
    def test_refuses_live_mode(
        self,
        create_monthly_plan: AsyncMock,
        create_product: AsyncMock,
        _load_dotenv: unittest.mock.Mock,
    ) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            result = main([])

        self.assertEqual(result, 2)
        create_product.assert_not_awaited()
        create_monthly_plan.assert_not_awaited()
        self.assertIn("only creates Sandbox catalog items", stderr.getvalue())

    @patch("backend.paypal_cli.load_dotenv")
    @patch(
        "backend.paypal_cli.create_product",
        new_callable=AsyncMock,
        side_effect=PayPalError("PayPal API request failed (HTTP 400)."),
    )
    @patch.dict(os.environ, {"PAYPAL_MODE": "sandbox"}, clear=True)
    def test_reports_paypal_errors(
        self,
        _create_product: AsyncMock,
        _load_dotenv: unittest.mock.Mock,
    ) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            result = main([])

        self.assertEqual(result, 1)
        self.assertIn("HTTP 400", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
