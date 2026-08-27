"""Tests for prompt tags and composition with per-feed custom prompts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

from flask import Blueprint

from app.extensions import db
from app.models import Feed, Tag
from app.routes.feed_routes import feed_bp
from app.routes.tag_routes import tag_bp
from podcast_processor.podcast_processor import PodcastProcessor


def _register_routes(app) -> None:
    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    if "tag" not in app.blueprints:
        app.register_blueprint(tag_bp)
    # add_feed redirects to main.index — stub it so url_for resolves.
    if "main" not in app.blueprints:
        main_bp = Blueprint("main", __name__)

        @main_bp.route("/", endpoint="index")
        def _index():
            return "ok"

        app.register_blueprint(main_bp)


class TestPromptComposition:
    def test_build_prompt_order_tag_then_custom(self):
        feed = SimpleNamespace(
            prompt_tag=SimpleNamespace(prompt="TAG PROMPT"),
            custom_llm_ad_prompt="FEED CUSTOM",
        )
        result = PodcastProcessor.build_ad_classification_system_prompt("BASE", feed)
        assert result == "BASE\n\nTAG PROMPT\n\nFEED CUSTOM"

    def test_build_prompt_tag_only(self):
        feed = SimpleNamespace(
            prompt_tag=SimpleNamespace(prompt="TAG ONLY"),
            custom_llm_ad_prompt=None,
        )
        result = PodcastProcessor.build_ad_classification_system_prompt("BASE", feed)
        assert result == "BASE\n\nTAG ONLY"

    def test_build_prompt_skips_blank_tag(self):
        feed = SimpleNamespace(
            prompt_tag=SimpleNamespace(prompt="   "),
            custom_llm_ad_prompt="FEED CUSTOM",
        )
        result = PodcastProcessor.build_ad_classification_system_prompt("BASE", feed)
        assert result == "BASE\n\nFEED CUSTOM"


class TestTagAPI:
    def test_create_list_update_delete_tag(self, app):
        app.testing = True
        _register_routes(app)
        client = app.test_client()

        def _create_side_effect(model_name, data, wait=True):
            assert model_name == "Tag"
            with app.app_context():
                tag = Tag(name=data["name"], prompt=data.get("prompt"))
                db.session.add(tag)
                db.session.commit()
                return SimpleNamespace(success=True, data={"id": tag.id})

        def _update_side_effect(model_name, model_id, updates, wait=True):
            assert model_name == "Tag"
            with app.app_context():
                Tag.query.filter_by(id=model_id).update(updates)
                db.session.commit()
                return SimpleNamespace(success=True)

        def _delete_side_effect(model_name, model_id, wait=True):
            assert model_name == "Tag"
            with app.app_context():
                tag = db.session.get(Tag, model_id)
                if tag is not None:
                    db.session.delete(tag)
                    db.session.commit()
                return SimpleNamespace(success=True)

        with mock.patch("app.routes.tag_routes.writer_client") as mock_writer:
            mock_writer.create.side_effect = _create_side_effect
            mock_writer.update.side_effect = _update_side_effect
            mock_writer.delete.side_effect = _delete_side_effect

            create_resp = client.post(
                "/api/tags",
                json={"name": "noiser", "prompt": "Noiser host-read ads"},
            )
            assert create_resp.status_code == 201
            tag_id = create_resp.get_json()["id"]

            list_resp = client.get("/api/tags")
            assert list_resp.status_code == 200
            names = [t["name"] for t in list_resp.get_json()]
            assert "noiser" in names

            patch_resp = client.patch(
                f"/api/tags/{tag_id}",
                json={"prompt": "Updated noiser prompt"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.get_json()["prompt"] == "Updated noiser prompt"

            delete_resp = client.delete(f"/api/tags/{tag_id}")
            assert delete_resp.status_code == 200


class TestFeedSettingsPromptTag:
    def test_assign_and_clear_prompt_tag(self, app):
        app.testing = True
        _register_routes(app)

        with app.app_context():
            tag = Tag(name="noiser", prompt="TAG")
            feed = Feed(title="Tagged Feed", rss_url="https://example.com/tagged.xml")
            db.session.add_all([tag, feed])
            db.session.commit()
            tag_id = tag.id
            feed_id = feed.id

        client = app.test_client()

        def _writer_update_side_effect(
            model_name: str, model_id: int, updates: dict[str, Any], wait: bool = True
        ):
            assert model_name == "Feed"
            Feed.query.filter_by(id=model_id).update(updates)
            db.session.commit()
            return SimpleNamespace(success=True)

        with mock.patch("app.routes.feed_routes.writer_client") as mock_writer:
            mock_writer.update.side_effect = _writer_update_side_effect
            assign = client.patch(
                f"/api/feeds/{feed_id}/settings",
                json={"prompt_tag_id": tag_id},
            )
            assert assign.status_code == 200
            assert assign.get_json()["prompt_tag_id"] == tag_id
            assert assign.get_json()["prompt_tag"]["name"] == "noiser"

            clear = client.patch(
                f"/api/feeds/{feed_id}/settings",
                json={"prompt_tag_id": None},
            )
            assert clear.status_code == 200
            assert clear.get_json()["prompt_tag_id"] is None

    def test_rejects_unknown_prompt_tag(self, app):
        app.testing = True
        _register_routes(app)

        with app.app_context():
            feed = Feed(title="Bad Tag Feed", rss_url="https://example.com/bad-tag.xml")
            db.session.add(feed)
            db.session.commit()
            feed_id = feed.id

        client = app.test_client()
        with mock.patch("app.routes.feed_routes.writer_client") as mock_writer:
            resp = client.patch(
                f"/api/feeds/{feed_id}/settings",
                json={"prompt_tag_id": 999999},
            )
            assert resp.status_code == 400
            mock_writer.update.assert_not_called()


class TestAddFeedPromptTag:
    def test_add_feed_with_prompt_tag_on_new_feed(self, app):
        app.testing = True
        _register_routes(app)

        with app.app_context():
            tag = Tag(name="noiser-add", prompt="TAG")
            db.session.add(tag)
            db.session.commit()
            tag_id = tag.id

        client = app.test_client()
        url = "http://example.com/add-with-tag.rss"

        def fake_add_or_refresh(_url, language=None, prompt_tag_id=None):
            assert prompt_tag_id == tag_id
            feed = Feed(
                title="New Tagged Feed",
                rss_url=_url,
                language=language,
                prompt_tag_id=prompt_tag_id,
            )
            db.session.add(feed)
            db.session.commit()
            return feed, True

        with (
            mock.patch(
                "app.routes.feed_routes.add_or_refresh_feed",
                side_effect=fake_add_or_refresh,
            ),
            mock.patch("app.routes.feed_routes.Thread"),
            mock.patch("app.routes.feed_routes.writer_client") as mock_writer,
        ):
            mock_writer.action.return_value = SimpleNamespace(success=True)
            mock_writer.update.return_value = SimpleNamespace(success=True)
            response = client.post(
                "/feed",
                data={"url": url, "prompt_tag_id": str(tag_id)},
                follow_redirects=False,
            )

        assert response.status_code in (200, 302), response.data
        with app.app_context():
            feed = Feed.query.filter_by(rss_url=url).first()
            assert feed is not None
            assert feed.prompt_tag_id == tag_id

    def test_add_feed_rejects_unknown_prompt_tag(self, app):
        app.testing = True
        _register_routes(app)
        client = app.test_client()

        with mock.patch("app.routes.feed_routes.add_or_refresh_feed") as mock_add:
            response = client.post(
                "/feed",
                data={
                    "url": "http://example.com/bad-tag-add.rss",
                    "prompt_tag_id": "999999",
                },
                follow_redirects=False,
            )

        assert response.status_code == 400
        mock_add.assert_not_called()

    def test_add_feed_sets_existing_feed_prompt_tag(self, app):
        app.testing = True
        _register_routes(app)

        with app.app_context():
            tag = Tag(name="noiser-existing", prompt="TAG")
            feed = Feed(
                title="Existing Feed",
                rss_url="http://example.com/retag.rss",
            )
            db.session.add_all([tag, feed])
            db.session.commit()
            tag_id = tag.id
            feed_id = feed.id

        client = app.test_client()

        def fake_add_or_refresh(_url, language=None, prompt_tag_id=None):
            return db.session.get(Feed, feed_id), False

        def writer_update_side_effect(model_name, model_id, updates, wait=True):
            assert model_name == "Feed"
            Feed.query.filter_by(id=model_id).update(updates)
            db.session.commit()
            return SimpleNamespace(success=True)

        with (
            mock.patch(
                "app.routes.feed_routes.add_or_refresh_feed",
                side_effect=fake_add_or_refresh,
            ),
            mock.patch("app.routes.feed_routes.Thread"),
            mock.patch("app.routes.feed_routes.writer_client") as mock_writer,
            mock.patch("app.feeds.writer_client") as mock_feeds_writer,
        ):
            mock_writer.action.return_value = SimpleNamespace(success=True)
            mock_writer.update.side_effect = writer_update_side_effect
            mock_feeds_writer.update.side_effect = writer_update_side_effect
            response = client.post(
                "/feed",
                data={
                    "url": "http://example.com/retag.rss",
                    "prompt_tag_id": str(tag_id),
                },
                follow_redirects=False,
            )

        assert response.status_code in (200, 302), response.data
        with app.app_context():
            feed = db.session.get(Feed, feed_id)
            assert feed is not None
            assert feed.prompt_tag_id == tag_id
