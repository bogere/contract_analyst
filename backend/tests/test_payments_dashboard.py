from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from backend.main import payments_analytics


class PaymentsAnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_deduplicated_aggregate_metrics_without_account_identifiers(self) -> None:
        now = datetime.now(timezone.utc)
        pro_user_id = uuid.uuid4()
        business_user_id = uuid.uuid4()
        subscriptions = [
            SimpleNamespace(
                user_id=pro_user_id,
                plan_key="pro",
                next_billing_time=now + timedelta(days=5),
            ),
            SimpleNamespace(
                user_id=business_user_id,
                plan_key="business",
                next_billing_time=now + timedelta(days=20),
            ),
            SimpleNamespace(
                user_id=business_user_id,
                plan_key="pro",
                next_billing_time=now + timedelta(days=10),
            ),
        ]
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=4),
            scalars=AsyncMock(return_value=subscriptions),
        )

        result = await payments_analytics(SimpleNamespace(), session)

        self.assertEqual(result.accounts, 4)
        self.assertEqual(result.paid_active_subscribers, 2)
        self.assertEqual(result.estimated_mrr, 68)
        self.assertEqual(result.upcoming_renewal_count, 2)
        self.assertEqual(
            [(plan.plan_name, plan.subscriber_count, plan.active_paid_subscribers) for plan in result.plans],
            [("Free", 2, 0), ("Pro", 1, 1), ("Business", 1, 1)],
        )
        self.assertEqual(
            [(renewal.plan_name, renewal.renewal_count) for renewal in result.upcoming_renewals],
            [("Pro", 1), ("Business", 1)],
        )
        response_data = result.model_dump()
        self.assertNotIn(str(pro_user_id), str(response_data))
        self.assertNotIn(str(business_user_id), str(response_data))

    async def test_rejects_unrecognized_active_plan(self) -> None:
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=1),
            scalars=AsyncMock(return_value=[
                SimpleNamespace(user_id=uuid.uuid4(), plan_key="unknown", next_billing_time=None),
            ]),
        )

        with self.assertRaises(HTTPException) as raised:
            await payments_analytics(SimpleNamespace(), session)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("unsupported plan", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
