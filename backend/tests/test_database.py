from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend import database


class InitializeDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_missing_subscription_columns_for_existing_database(self) -> None:
        connection = MagicMock()
        connection.run_sync = AsyncMock()
        connection.execute = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=connection)
        context.__aexit__ = AsyncMock(return_value=None)
        engine = MagicMock()
        engine.begin.return_value = context
        engine.dispose = AsyncMock()

        with (
            patch("backend.database._database_url", return_value="postgresql+asyncpg://test"),
            patch("backend.database.create_async_engine", return_value=engine),
        ):
            try:
                await database.initialize_database()
                connection.run_sync.assert_awaited_once_with(database.Base.metadata.create_all)
                self.assertEqual(connection.execute.await_count, 2)
                statements = [str(call.args[0]) for call in connection.execute.await_args_list]
                self.assertTrue(
                    any(
                        "ADD COLUMN IF NOT EXISTS approval_url VARCHAR(2048)" in statement
                        for statement in statements
                    )
                )
                self.assertTrue(
                    any(
                        "ADD COLUMN IF NOT EXISTS last_webhook_event_time "
                        "TIMESTAMP WITH TIME ZONE" in statement
                        for statement in statements
                    )
                )
            finally:
                await database.close_database()


if __name__ == "__main__":
    unittest.main()
