import logging
from unittest.mock import MagicMock

from app.db_guard import refresh_read_snapshot


def test_refresh_read_snapshot_rolls_back_and_expires() -> None:
    session = MagicMock()
    logger = logging.getLogger("test_refresh")
    refresh_read_snapshot(session, logger, "unit")
    session.rollback.assert_called_once()
    session.expire_all.assert_called_once()


def test_refresh_read_snapshot_tolerates_rollback_failure() -> None:
    session = MagicMock()
    session.rollback.side_effect = RuntimeError("closed")
    refresh_read_snapshot(session, logging.getLogger("test_refresh"), "unit")
    session.expire_all.assert_called_once()
