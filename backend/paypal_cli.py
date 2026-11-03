"""Create PayPal Sandbox products and subscription plans."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from backend.paypal import PayPalError, create_monthly_plan, create_product

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLANS = (
    (
        "pro",
        "Contract Analyst Pro",
        "For individuals reviewing contracts regularly.",
        "19.00",
    ),
    (
        "business",
        "Contract Analyst Business",
        "Higher monthly capacity for professional use.",
        "49.00",
    ),
)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create PayPal Sandbox catalog plans for frontend checkout. "
            "This command does not create subscriptions."
        )
    )
    parser.add_argument(
        "--product-id",
        help="Use an existing PayPal product instead of creating a new one.",
    )
    parser.add_argument(
        "--only-plan",
        choices=("pro", "business"),
        help="Create only this plan instead of both Pro and Business.",
    )
    return parser


async def _create_catalog(product_id: str | None, only_plan: str | None) -> None:
    if product_id is None:
        product = await create_product()
        created_product_id = product.get("id")
        if not isinstance(created_product_id, str) or not created_product_id:
            raise PayPalError("PayPal did not return a product ID.")
        product_id = created_product_id
        print(f"Product ID: {product_id}")

    plans = (plan for plan in PLANS if only_plan is None or plan[0] == only_plan)
    for key, name, description, monthly_price in plans:
        plan_id = await create_monthly_plan(
            product_id=product_id,
            name=name,
            description=description,
            monthly_price=monthly_price,
        )
        print(f"PAYPAL_{key.upper()}_PLAN_ID={plan_id}")


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = _argument_parser().parse_args(argv)

    mode = (os.getenv("PAYPAL_MODE") or "sandbox").strip().lower()
    if mode != "sandbox":
        print(
            "Error: this CLI only creates Sandbox catalog items; set PAYPAL_MODE=sandbox.",
            file=sys.stderr,
        )
        return 2

    product_id = args.product_id.strip() if args.product_id is not None else None
    if args.product_id is not None and not product_id:
        _argument_parser().error("--product-id cannot be empty.")

    try:
        asyncio.run(_create_catalog(product_id, args.only_plan))
    except PayPalError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("Add the plan IDs above to the project .env.")
    print("Users can subscribe to these plans through the frontend billing page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
