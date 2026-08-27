"""Tests for auto show-prompt research + generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from app.extensions import db
from app.models import Feed, Post, Tag
from podcast_processor import show_prompt_generator as spg


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


def test_is_safe_public_http_url_rejects_private_and_non_http():
    assert spg.is_safe_public_http_url("ftp://example.com") is False
    assert spg.is_safe_public_http_url("http://localhost/x") is False
    assert spg.is_safe_public_http_url("http://127.0.0.1/x") is False
    assert spg.is_safe_public_http_url("http://192.168.1.1/x") is False
    assert spg.is_safe_public_http_url("http://10.0.0.5/x") is False
    with mock.patch(
        "podcast_processor.show_prompt_generator.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 0))],
    ):
        assert spg.is_safe_public_http_url("https://example.com/about") is True
    with mock.patch(
        "podcast_processor.show_prompt_generator.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.0.0.8", 0))],
    ):
        assert spg.is_safe_public_http_url("https://evil.example/x") is False


def test_build_research_pack_includes_episodes_and_tag(app):
    with app.app_context():
        tag = Tag(name="network-x", prompt="NETWORK TAG RULES")
        db.session.add(tag)
        db.session.commit()
        feed = _make_feed(
            prompt_tag_id=tag.id,
            itunes_categories='[{"text": "Technology", "subs": []}]',
        )
        post = Post(
            feed_id=feed.id,
            guid="g1",
            title="Ep 1",
            description="<p>Hello world sponsor</p>",
            download_url="https://example.com/ep1.mp3",
        )
        db.session.add(post)
        db.session.commit()
        db.session.refresh(feed)

        pack = spg.build_research_pack(
            feed,
            channel_link="https://example.com",
            directory={"title": "Acme", "author": "Jane", "genres": ["Tech"]},
            website_text="About the show",
        )

        assert pack["title"] == "Acme Show"
        assert pack["categories"] == ["Technology"]
        assert pack["prompt_tag"] == "NETWORK TAG RULES"
        assert pack["episodes"][0]["title"] == "Ep 1"
        assert "Hello world" in pack["episodes"][0]["description"]
        assert pack["directory"]["genres"] == ["Tech"]
        assert "About the show" in pack["website_text"]


def test_heuristic_and_format_research_pack():
    pack = {
        "title": "Acme Show",
        "author": "Jane",
        "categories": ["Tech"],
        "description": "desc",
        "episodes": [{"title": "Ep", "description": "snip"}],
        "prompt_tag": "TAG",
        "channel_link": "https://example.com",
        "website_text": "site",
        "directory": {"title": "Acme", "author": "Jane", "genres": ["Tech"]},
    }
    text = spg.format_research_pack_for_prompt(pack)
    assert "Acme Show" in text
    assert "TAG" in text
    draft = spg.heuristic_show_prompt_draft(pack)
    assert "Jane" in draft
    assert "CONTENT" in draft
    assert "AD" in draft


def test_generate_skips_when_prompt_already_set(app):
    with app.app_context():
        feed = _make_feed(custom_llm_ad_prompt="existing rules")
        with mock.patch(
            "podcast_processor.show_prompt_generator.llm_is_configured",
            return_value=True,
        ):
            result = spg.generate_and_persist_show_prompt(feed.id, force=False)
        assert result is None


def test_generate_force_overwrites(app):
    with app.app_context():
        feed = _make_feed(custom_llm_ad_prompt="old")
        with (
            mock.patch(
                "podcast_processor.show_prompt_generator.llm_is_configured",
                return_value=True,
            ),
            mock.patch(
                "podcast_processor.show_prompt_generator.gather_research_for_feed",
                return_value={"title": "Acme Show", "author": "Jane", "categories": []},
            ),
            mock.patch(
                "podcast_processor.show_prompt_generator.draft_show_prompt_with_llm",
                return_value="NEW RULES",
            ),
            mock.patch(
                "podcast_processor.show_prompt_generator.writer_client"
            ) as mock_writer,
        ):
            mock_writer.update.return_value = SimpleNamespace(success=True)
            result = spg.generate_and_persist_show_prompt(feed.id, force=True)

        assert result == "NEW RULES"
        mock_writer.update.assert_called_once()
        args = mock_writer.update.call_args
        assert args.args[0] == "Feed"
        assert args.args[1] == feed.id
        assert args.args[2]["custom_llm_ad_prompt"] == "NEW RULES"


def test_generate_skips_without_llm_key(app):
    with app.app_context():
        feed = _make_feed()
        with mock.patch(
            "podcast_processor.show_prompt_generator.llm_is_configured",
            return_value=False,
        ):
            assert spg.generate_and_persist_show_prompt(feed.id) is None


def test_maybe_auto_generate_respects_config_flag(app):
    with app.app_context():
        with (
            mock.patch.object(spg.config, "auto_generate_show_prompt", False),
            mock.patch(
                "podcast_processor.show_prompt_generator.generate_and_persist_show_prompt"
            ) as mock_gen,
        ):
            spg.maybe_auto_generate_show_prompt(1)
        mock_gen.assert_not_called()


def test_fetch_directory_prefers_matching_feed_url():
    payload = {
        "results": [
            {
                "collectionName": "Other",
                "feedUrl": "https://other.com/rss",
                "artistName": "A",
                "genres": ["News"],
            },
            {
                "collectionName": "Acme Show",
                "feedUrl": "https://example.com/acme.rss",
                "artistName": "Jane",
                "genres": ["Tech"],
            },
        ]
    }
    response = mock.Mock()
    response.raise_for_status = mock.Mock()
    response.json.return_value = payload
    with mock.patch(
        "podcast_processor.show_prompt_generator.requests.get",
        return_value=response,
    ):
        match = spg.fetch_directory_match(
            "Acme Show", "https://www.example.com/acme.rss"
        )
    assert match is not None
    assert match["title"] == "Acme Show"
    assert match["genres"] == ["Tech"]


def test_fetch_website_text_blocks_unsafe_redirect():
    response = mock.MagicMock()
    response.url = "http://127.0.0.1/secret"
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with (
        mock.patch(
            "podcast_processor.show_prompt_generator.is_safe_public_http_url",
            side_effect=lambda url: "127.0.0.1" not in url,
        ),
        mock.patch(
            "podcast_processor.show_prompt_generator.requests.get",
            return_value=response,
        ),
    ):
        assert spg.fetch_website_text("https://example.com") is None


def test_generate_show_prompt_endpoint_409_without_force(app):
    from app.routes.feed_routes import feed_bp

    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    app.testing = True
    with app.app_context():
        feed = _make_feed(custom_llm_ad_prompt="already there")
        client = app.test_client()
        with mock.patch(
            "app.routes.feed_routes.require_admin",
            return_value=(SimpleNamespace(id=1), None),
        ):
            response = client.post(
                f"/api/feeds/{feed.id}/generate-show-prompt",
                json={},
            )
        assert response.status_code == 409
        assert "already set" in response.get_json()["error"].lower()


def test_generate_show_prompt_endpoint_force(app):
    from app.routes.feed_routes import feed_bp

    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    app.testing = True
    with app.app_context():
        feed = _make_feed(custom_llm_ad_prompt="old")
        client = app.test_client()
        with (
            mock.patch(
                "app.routes.feed_routes.require_admin",
                return_value=(SimpleNamespace(id=1), None),
            ),
            mock.patch(
                "podcast_processor.show_prompt_generator.llm_is_configured",
                return_value=True,
            ),
            mock.patch(
                "podcast_processor.show_prompt_generator.generate_and_persist_show_prompt",
                return_value="fresh draft",
            ),
        ):
            response = client.post(
                f"/api/feeds/{feed.id}/generate-show-prompt",
                json={"force": True},
            )
        assert response.status_code == 200
        assert response.get_json()["custom_llm_ad_prompt"] == "fresh draft"


def test_add_feed_starts_show_prompt_thread_only_when_created(app):
    from app.routes.feed_routes import feed_bp

    if "feed" not in app.blueprints:
        app.register_blueprint(feed_bp)
    # index redirect target
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
            isinstance(n, str) and n.startswith("show-prompt-gen-") for n in names
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
            isinstance(n, str) and n.startswith("show-prompt-gen-") for n in names2
        )


def test_draft_show_prompt_with_llm_falls_back_on_empty():
    pack = {
        "title": "Acme",
        "author": "Host",
        "categories": [],
        "description": "",
        "episodes": [],
    }
    empty_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
    )
    with (
        mock.patch.object(spg.config, "llm_api_key", "k"),
        mock.patch.object(spg.config, "llm_model", "gpt-4o"),
        mock.patch.object(spg.config, "openai_base_url", None),
        mock.patch.object(spg.config, "openai_timeout", 30),
        mock.patch.object(spg.config, "llm_max_concurrent_calls", 1),
        mock.patch(
            "podcast_processor.show_prompt_generator.litellm.completion",
            return_value=empty_response,
        ),
        mock.patch(
            "podcast_processor.show_prompt_generator.extract_litellm_content",
            return_value="",
        ),
    ):
        draft = spg.draft_show_prompt_with_llm(pack)
    assert "CONTENT" in draft
