"""Tests for recording podcast-client polls on GET /feed/<id>."""

from __future__ import annotations

import datetime as dt
from unittest import mock

from app.extensions import db
from app.models import Feed, Post
from app.routes import feed_routes
from app.routes.feed_routes import feed_bp
from app.writer.actions.feeds import record_feed_client_poll_action


def _make_feed_with_post(rss_url: str = "https://example.com/client-poll.xml") -> int:
    feed = Feed(title="Client Poll Feed", rss_url=rss_url)
    db.session.add(feed)
    db.session.commit()

    post = Post(
        feed_id=feed.id,
        guid=f"post-guid-{feed.id}",
        download_url=f"{rss_url}/ep1.mp3",
        title="Episode 1",
        release_date=dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC),
    )
    db.session.add(post)
    db.session.commit()
    return feed.id


def _register_feed_routes(app) -> None:
    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)


def _reset_client_poll_state() -> None:
    with feed_routes._CLIENT_POLL_LOCK:
        feed_routes._CLIENT_POLL_LAST_STAMP.clear()


def test_record_feed_client_poll_action(app):
    with app.app_context():
        feed_id = _make_feed_with_post()
        result = record_feed_client_poll_action(
            {"feed_id": feed_id, "client_name": "Apple Podcasts"}
        )
        db.session.commit()
        feed = db.session.get(Feed, feed_id)
        assert feed is not None
        assert feed.last_client_name == "Apple Podcasts"
        assert feed.last_client_polled_at is not None
        assert result["last_client_name"] == "Apple Podcasts"


def test_get_feed_records_client_poll(app):
    app.testing = True
    _register_feed_routes(app)
    _reset_client_poll_state()

    with app.app_context():
        feed_id = _make_feed_with_post()

    client = app.test_client()
    with (
        mock.patch("app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"),
        mock.patch("app.routes.feed_routes._spawn_async_refresh"),
        mock.patch("app.routes.feed_routes.writer_client") as mock_writer,
    ):
        resp = client.get(
            f"/feed/{feed_id}",
            headers={"User-Agent": "Overcast/2024.1"},
        )

    assert resp.status_code == 200
    mock_writer.action.assert_called_once()
    args, kwargs = mock_writer.action.call_args
    assert args[0] == "record_feed_client_poll"
    assert args[1]["feed_id"] == feed_id
    assert args[1]["client_name"] == "Overcast"
    assert kwargs.get("wait") is False


def test_get_feed_debounces_client_poll_stamps(app):
    app.testing = True
    _register_feed_routes(app)
    _reset_client_poll_state()

    with app.app_context():
        feed_id = _make_feed_with_post()

    client = app.test_client()
    with (
        mock.patch("app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"),
        mock.patch("app.routes.feed_routes._spawn_async_refresh"),
        mock.patch("app.routes.feed_routes.writer_client") as mock_writer,
    ):
        first = client.get(
            f"/feed/{feed_id}",
            headers={"User-Agent": "Overcast/2024.1"},
        )
        second = client.get(
            f"/feed/{feed_id}",
            headers={"User-Agent": "Overcast/2024.1"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_writer.action.call_count == 1


def test_serialize_feed_includes_client_poll_fields(app):
    with app.app_context():
        feed = Feed(
            title="Serialize Client",
            rss_url="https://example.com/serialize-client.xml",
            last_client_polled_at=dt.datetime(2026, 3, 15, 12, 0, 0),
            last_client_name="Apple Podcasts",
        )
        db.session.add(feed)
        db.session.commit()

        from app.routes.feed_routes import _serialize_feed

        payload = _serialize_feed(feed)
        assert payload["last_client_polled_at"] == "2026-03-15T12:00:00Z"
        assert payload["last_client_name"] == "Apple Podcasts"
