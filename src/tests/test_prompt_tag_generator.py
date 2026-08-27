"""Tests for auto prompt-tag research + create/assign."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from app.extensions import db
from app.models import Feed, Post, Tag
from podcast_processor import prompt_tag_generator as ptg


def _make_feed(**kwargs) -> Feed:
    defaults = {
        "title": "Acme Show",
        "description": "Interviews with builders. Sponsored by widgets.",
        "author": "Jane Host",
        "rss_url": "https://example.com/acme.rss",
        "image_url": "https://example.com/art.jpg",
    }
    defaults.update(kwargs)
    feed = Feed(**defaults)
    db.session.add(feed)
    db.session.commit()
    return feed


def test_slugify_and_heuristic_reuse_existing_name():
    pack = {
        "title": "Planet Money",
        "author": "NPR",
        "categories": ["Business"],
        "existing_tag_names": ["npr", "wondery"],
    }
    draft = ptg.heuristic_prompt_tag_draft(pack)
    assert draft["name"] == "npr"
    assert "CONTENT" in draft["prompt"]
    assert ptg.slugify_tag_name("Hello World!!") == "hello-world"


def test_parse_tag_json_accepts_fenced_or_raw():
    assert ptg._parse_tag_json('{"name":"npr","prompt":"RULES"}') == {
        "name": "npr",
        "prompt": "RULES",
    }
    fenced = '```json\n{"name": "x", "prompt": "y"}\n```'
    assert ptg._parse_tag_json(fenced) == {"name": "x", "prompt": "y"}
    assert ptg._parse_tag_json("not json") is None


def test_generate_skips_when_prompt_tag_already_set(app):
    with app.app_context():
        tag = Tag(name="npr", prompt="RULES")
        db.session.add(tag)
        db.session.commit()
        feed = _make_feed(prompt_tag_id=tag.id)
        with mock.patch(
            "podcast_processor.prompt_tag_generator.llm_is_configured",
            return_value=True,
        ):
            assert ptg.generate_and_persist_prompt_tag(feed.id, force=False) is None


def test_generate_creates_and_assigns_tag(app):
    with app.app_context():
        feed = _make_feed()
        created_tag = Tag(name="npr", prompt="RULES")
        db.session.add(created_tag)
        db.session.commit()

        with (
            mock.patch(
                "podcast_processor.prompt_tag_generator.llm_is_configured",
                return_value=True,
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.gather_research_for_feed",
                return_value={
                    "title": "Acme",
                    "author": "NPR",
                    "categories": [],
                    "existing_tag_names": [],
                },
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.draft_prompt_tag_with_llm",
                return_value={"name": "npr", "prompt": "NEW RULES"},
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.writer_client"
            ) as mock_writer,
        ):
            mock_writer.update.return_value = SimpleNamespace(success=True)
            # Force path updates existing tag prompt then assigns feed.
            result = ptg.generate_and_persist_prompt_tag(feed.id, force=True)

        assert result is not None
        assert result["name"] == "npr"
        assert mock_writer.update.call_count >= 1
        feed_updates = [
            c for c in mock_writer.update.call_args_list if c.args[0] == "Feed"
        ]
        assert feed_updates
        assert feed_updates[-1].args[2]["prompt_tag_id"] == created_tag.id


def test_generate_creates_new_tag_when_missing(app):
    with app.app_context():
        feed = _make_feed()
        with (
            mock.patch(
                "podcast_processor.prompt_tag_generator.llm_is_configured",
                return_value=True,
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.gather_research_for_feed",
                return_value={
                    "title": "Acme",
                    "author": "Jane",
                    "categories": [],
                    "existing_tag_names": [],
                },
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.draft_prompt_tag_with_llm",
                return_value={"name": "jane-host", "prompt": "RULES"},
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.writer_client"
            ) as mock_writer,
        ):

            def create_side_effect(model, data, wait=True):
                tag = Tag(name=data["name"], prompt=data["prompt"])
                db.session.add(tag)
                db.session.commit()
                return SimpleNamespace(success=True, data={"id": tag.id})

            mock_writer.create.side_effect = create_side_effect
            mock_writer.update.return_value = SimpleNamespace(success=True)
            result = ptg.generate_and_persist_prompt_tag(feed.id, force=False)

        assert result is not None
        assert result["name"] == "jane-host"
        mock_writer.create.assert_called_once()


def test_maybe_auto_generate_respects_config_flag(app):
    with app.app_context():
        with (
            mock.patch.object(ptg.config, "auto_generate_prompt_tag", False),
            mock.patch(
                "podcast_processor.prompt_tag_generator.generate_and_persist_prompt_tag"
            ) as mock_gen,
        ):
            ptg.maybe_auto_generate_prompt_tag(1)
        mock_gen.assert_not_called()


def test_generate_prompt_tag_endpoint_409_without_force(app):
    from app.routes.feed_routes import feed_bp

    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    app.testing = True
    with app.app_context():
        tag = Tag(name="npr", prompt="x")
        db.session.add(tag)
        db.session.commit()
        feed = _make_feed(prompt_tag_id=tag.id)
        client = app.test_client()
        response = client.post(
            f"/api/feeds/{feed.id}/generate-prompt-tag",
            json={},
        )
        assert response.status_code == 409


def test_generate_prompt_tag_endpoint_force(app):
    from app.routes.feed_routes import feed_bp

    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    app.testing = True
    with app.app_context():
        feed = _make_feed()
        client = app.test_client()
        with (
            mock.patch(
                "podcast_processor.prompt_tag_generator.llm_is_configured",
                return_value=True,
            ),
            mock.patch(
                "podcast_processor.prompt_tag_generator.generate_and_persist_prompt_tag",
                return_value={
                    "tag_id": 9,
                    "prompt_tag_id": 9,
                    "name": "npr",
                    "prompt": "RULES",
                },
            ),
        ):
            response = client.post(
                f"/api/feeds/{feed.id}/generate-prompt-tag",
                json={"force": True},
            )
        assert response.status_code == 200
        assert response.get_json()["name"] == "npr"


def test_add_feed_starts_prompt_tag_thread_only_when_created(app):
    from app.routes.feed_routes import feed_bp

    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    if "main.index" not in app.view_functions:
        app.add_url_rule("/", endpoint="main.index", view_func=lambda: ("ok", 200))
    app.testing = True

    with app.app_context():
        client = app.test_client()
        feed = _make_feed(rss_url="http://example.com/new-show.rss")

        with (
            mock.patch(
                "app.routes.feed_routes.add_or_refresh_feed",
                return_value=(feed, True),
            ),
            mock.patch("app.routes.feed_routes.Thread") as mock_thread,
            mock.patch("app.routes.feed_routes.is_auth_enabled", return_value=False),
            mock.patch("app.routes.feed_routes.whitelist_latest_for_first_member"),
        ):
            response = client.post(
                "/feed",
                data={"url": "http://example.com/new-show.rss"},
                follow_redirects=False,
            )

        assert response.status_code in (200, 302)
        names = [c.kwargs.get("name") for c in mock_thread.call_args_list]
        assert any(
            isinstance(n, str) and n.startswith("prompt-tag-gen-") for n in names
        )

        with (
            mock.patch(
                "app.routes.feed_routes.add_or_refresh_feed",
                return_value=(feed, False),
            ),
            mock.patch("app.routes.feed_routes.Thread") as mock_thread2,
            mock.patch("app.routes.feed_routes.is_auth_enabled", return_value=False),
            mock.patch("app.routes.feed_routes.whitelist_latest_for_first_member"),
        ):
            response = client.post(
                "/feed",
                data={"url": "http://example.com/new-show.rss"},
                follow_redirects=False,
            )

        assert response.status_code in (200, 302)
        names2 = [c.kwargs.get("name") for c in mock_thread2.call_args_list]
        assert not any(
            isinstance(n, str) and n.startswith("prompt-tag-gen-") for n in names2
        )


def test_build_research_pack_lists_existing_tags(app):
    with app.app_context():
        db.session.add(Tag(name="wondery", prompt="W"))
        db.session.commit()
        feed = _make_feed()
        post = Post(
            feed_id=feed.id,
            guid="g1",
            title="Ep 1",
            description="<p>Hello</p>",
            download_url="https://example.com/ep1.mp3",
        )
        db.session.add(post)
        db.session.commit()
        db.session.refresh(feed)
        pack = ptg.build_research_pack(
            feed,
            existing_tag_names=ptg.list_existing_tag_names(),
        )
        assert "wondery" in pack["existing_tag_names"]
        assert pack["episodes"][0]["title"] == "Ep 1"
