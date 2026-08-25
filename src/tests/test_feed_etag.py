"""Tests for ETag / Last-Modified / 304 short-circuit on feed routes."""

from __future__ import annotations

import datetime as dt
from unittest import mock

from app.extensions import db
from app.feeds import generate_feed_xml
from app.models import Feed, Post
from app.routes.feed_routes import feed_bp
from app.writer.actions.feeds import refresh_feed_action


def _make_feed_with_post(rss_url: str = "https://example.com/feed.xml") -> int:
    feed = Feed(title="Etag Feed", rss_url=rss_url)
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


def test_feed_has_default_last_changed_at(app):
    with app.app_context():
        feed = Feed(title="Feed A", rss_url="https://example.com/a.xml")
        db.session.add(feed)
        db.session.commit()
        db.session.refresh(feed)
        assert feed.last_changed_at is not None
        assert isinstance(feed.last_changed_at, dt.datetime)


def test_refresh_feed_action_bumps_last_changed_at_on_new_post(app):
    with app.app_context():
        feed = Feed(title="Feed B", rss_url="https://example.com/b.xml")
        db.session.add(feed)
        db.session.commit()
        feed.last_changed_at = feed.last_changed_at - dt.timedelta(hours=1)
        db.session.commit()
        old_changed = feed.last_changed_at

        refresh_feed_action(
            {
                "feed_id": feed.id,
                "new_posts": [
                    {
                        "guid": "new-guid",
                        "title": "New Ep",
                        "description": "",
                        "download_url": "https://example.com/b/new.mp3",
                        "release_date": "2026-04-01T12:00:00+00:00",
                        "duration": 60,
                        "image_url": None,
                        "whitelisted": False,
                        "feed_id": feed.id,
                    }
                ],
            }
        )
        db.session.commit()
        db.session.refresh(feed)
        assert feed.last_changed_at is not None
        assert feed.last_changed_at > old_changed


def test_refresh_feed_action_bumps_last_changed_at_on_existing_post_update(app):
    with app.app_context():
        feed = Feed(title="Feed C", rss_url="https://example.com/c.xml")
        db.session.add(feed)
        db.session.commit()
        post = Post(
            feed_id=feed.id,
            guid="g-c",
            download_url="https://example.com/c/ep.mp3",
            title="Old title",
        )
        db.session.add(post)
        db.session.commit()
        feed.last_changed_at = feed.last_changed_at - dt.timedelta(hours=1)
        db.session.commit()
        old_changed = feed.last_changed_at

        refresh_feed_action(
            {
                "feed_id": feed.id,
                "existing_post_updates": [
                    {"post_id": post.id, "title": "Newer title"},
                ],
            }
        )
        db.session.commit()
        db.session.refresh(feed)
        assert feed.last_changed_at > old_changed


def test_refresh_feed_action_does_not_bump_when_nothing_changes(app):
    with app.app_context():
        feed = Feed(title="Feed D", rss_url="https://example.com/d.xml")
        db.session.add(feed)
        db.session.commit()
        old_changed = feed.last_changed_at

        refresh_feed_action({"feed_id": feed.id})
        db.session.commit()
        db.session.refresh(feed)
        assert feed.last_changed_at == old_changed


def test_generate_feed_xml_uses_feed_last_changed_at(app):
    """`lastBuildDate` must come from `Feed.last_changed_at`, not now()."""
    with app.app_context():
        feed_id = _make_feed_with_post()
        feed = db.session.get(Feed, feed_id)
        assert feed is not None
        fixed = dt.datetime(2025, 6, 15, 10, 30, 0)
        feed.last_changed_at = fixed
        db.session.commit()

        with mock.patch("app.feeds._get_base_url", return_value="http://test"):
            xml_bytes = generate_feed_xml(feed)
        xml = xml_bytes.decode("utf-8") if isinstance(xml_bytes, bytes) else xml_bytes
        assert "15 Jun 2025 10:30:00" in xml


def test_get_feed_emits_etag_and_last_modified_headers(app):
    app.testing = True
    _register_feed_routes(app)

    with app.app_context():
        feed_id = _make_feed_with_post()

    client = app.test_client()
    with (
        mock.patch(
            "app.routes.feed_routes._should_kickoff_async_refresh", return_value=False
        ),
        mock.patch("app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"),
    ):
        resp = client.get(f"/feed/{feed_id}")

    assert resp.status_code == 200
    assert resp.headers.get("ETag")
    assert resp.headers.get("Last-Modified")
    assert resp.headers.get("Cache-Control") == "public, max-age=60"


def test_get_feed_returns_304_when_etag_matches(app):
    app.testing = True
    _register_feed_routes(app)

    with app.app_context():
        feed_id = _make_feed_with_post()

    client = app.test_client()
    with (
        mock.patch(
            "app.routes.feed_routes._should_kickoff_async_refresh", return_value=False
        ) as mock_refresh_gate,
        mock.patch("app.routes.feed_routes._spawn_async_refresh") as mock_spawn,
        mock.patch("app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"),
    ):
        first = client.get(f"/feed/{feed_id}")
        etag = first.headers["ETag"]

        second = client.get(f"/feed/{feed_id}", headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.get_data(as_text=True) == ""
    assert second.headers.get("ETag") == etag
    # First call may evaluate refresh gate; 304 path must not spawn refresh.
    assert mock_spawn.call_count == 0
    # Gate is only consulted on the full 200 path.
    assert mock_refresh_gate.call_count == 1


def test_get_feed_skips_xml_when_etag_matches(app):
    """The 304 path must skip XML rebuild."""
    app.testing = True
    _register_feed_routes(app)

    with app.app_context():
        feed_id = _make_feed_with_post()

    client = app.test_client()
    with (
        mock.patch(
            "app.routes.feed_routes._should_kickoff_async_refresh", return_value=False
        ),
        mock.patch(
            "app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"
        ) as mock_xml,
    ):
        first = client.get(f"/feed/{feed_id}")
        assert mock_xml.call_count == 1
        second = client.get(
            f"/feed/{feed_id}", headers={"If-None-Match": first.headers["ETag"]}
        )
        assert second.status_code == 304
        assert mock_xml.call_count == 1
