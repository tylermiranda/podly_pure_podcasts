"""Shared helpers to protect long-lived sessions in background threads."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.exc import OperationalError, PendingRollbackError
from sqlalchemy.orm import Session, scoped_session

SessionType = Session | scoped_session[Any]


def refresh_read_snapshot(
    session: SessionType,
    logger: logging.Logger | None = None,
    context: str = "refresh_read_snapshot",
) -> None:
    """End the current read transaction so other connections' commits are visible.

    SQLite WAL freezes a snapshot until rollback/commit. Processor jobs query
    identifications the writer process just inserted; ``expire_all()`` alone
    does not end that snapshot. Does not ``session.remove()`` — the job must
    keep its scoped session.
    """
    log = logger or logging.getLogger("global_logger")
    try:
        session.rollback()
    except Exception as rb_exc:  # noqa: BLE001
        log.warning(
            "[SESSION_REFRESH] rollback failed in context=%s: %s", context, rb_exc
        )
    expire = getattr(session, "expire_all", None)
    if callable(expire):
        try:
            expire()
        except Exception as exp_exc:  # noqa: BLE001
            log.warning(
                "[SESSION_REFRESH] expire_all failed in context=%s: %s",
                context,
                exp_exc,
            )


def reset_session(
    session: SessionType,
    logger: logging.Logger,
    context: str,
    exc: Exception | None = None,
) -> None:
    """
    Roll back and remove a session after a failure to avoid leaving it in a bad state.
    Safe to call even if the session is already closed/invalid.
    """
    if exc:
        logger.warning(
            "[SESSION_RESET] context=%s exc=%s; rolling back and removing session",
            context,
            exc,
        )
    try:
        session.rollback()
    except Exception as rb_exc:  # noqa: BLE001
        logger.warning(
            "[SESSION_RESET] rollback failed in context=%s: %s", context, rb_exc
        )
    try:
        remove_fn = getattr(session, "remove", None)
        if callable(remove_fn):
            remove_fn()
    except Exception as rm_exc:  # noqa: BLE001
        logger.warning(
            "[SESSION_RESET] remove failed in context=%s: %s", context, rm_exc
        )


@contextmanager
def db_guard(
    context: str, session: SessionType, logger: logging.Logger
) -> Iterator[None]:
    """
    Guard a block of DB work so lock/rollback errors always clean the session
    before propagating.
    """
    try:
        yield
    except (OperationalError, PendingRollbackError) as exc:
        reset_session(session, logger, context, exc)
        raise
