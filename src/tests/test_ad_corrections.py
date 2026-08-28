from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from app.extensions import db
from app.models import (
    AdCorrection,
    Feed,
    Identification,
    ModelCall,
    Post,
    TranscriptSegment,
)
from app.routes.post_routes import post_bp
from app.routes.post_stats_utils import cut_eligible_identifications, final_cut_windows
from app.writer.actions.cleanup import (
    clear_post_processing_data_action,
    clear_post_processing_data_keep_transcript_action,
)
from app.writer.actions.processor import (
    insert_ad_correction_action,
    set_post_transcript_reviewed_action,
)
from podcast_processor.ad_corrections import (
    analyze_corrections_for_prompt,
    build_analyze_prompt_messages,
    format_correction_examples_prompt,
    format_corrections_for_prompt_analysis,
    heuristic_prompt_draft_from_corrections,
    processed_audio_needs_recut,
    retrieve_correction_examples,
    snap_range_to_words,
    suggested_prompt_snippet,
    suggested_prompt_status,
)
from podcast_processor.ad_spans import apply_corrections_to_windows
from podcast_processor.podcast_processor import PodcastProcessor


def _make_feed_post(app, *, guid: str, rss_url: str):
    feed = Feed(title=f"Feed {guid}", rss_url=rss_url)
    db.session.add(feed)
    db.session.commit()
    post = Post(
        feed_id=feed.id,
        guid=guid,
        download_url=f"https://example.com/{guid}.mp3",
        title="Episode",
        whitelisted=True,
    )
    db.session.add(post)
    db.session.commit()
    return feed, post


def test_apply_corrections_punches_content_and_inserts_ad() -> None:
    windows = [(0.0, 10.0)]
    corrections = [
        SimpleNamespace(label="content", start_time=3.0, end_time=5.0),
        SimpleNamespace(label="ad", start_time=12.0, end_time=14.0),
    ]
    assert apply_corrections_to_windows(windows, corrections) == [
        (0.0, 3.0),
        (5.0, 10.0),
        (12.0, 14.0),
    ]


def test_later_ad_correction_overrides_earlier_content_hole() -> None:
    windows: list[tuple[float, float]] = []
    corrections = [
        SimpleNamespace(id=39, label="content", start_time=0.2, end_time=58.7),
        SimpleNamespace(id=44, label="ad", start_time=0.2, end_time=58.7),
    ]
    assert apply_corrections_to_windows(windows, corrections) == [(0.2, 58.7)]


def test_snap_range_to_words() -> None:
    segment = SimpleNamespace(
        words=[
            {"word": "It", "start": 59.4, "end": 59.6},
            {"word": "is", "start": 59.6, "end": 59.8},
            {"word": "July", "start": 59.8, "end": 61.2},
        ]
    )
    start, end = snap_range_to_words(59.5, 61.0, [segment])
    assert start == 59.4
    assert end == 61.2


def test_insert_ad_correction_and_stats_payload(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-stats-guid", rss_url="https://example.com/corr-stats.xml"
        )
        segment = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=0.0,
            end_time=10.0,
            text="Hello from the sponsor, then the story.",
            words=[
                {"word": "Hello", "start": 0.0, "end": 1.0},
                {"word": "story", "start": 5.0, "end": 6.0},
            ],
        )
        db.session.add(segment)
        db.session.commit()
        model_call = ModelCall(
            post_id=post.id,
            first_segment_sequence_num=0,
            last_segment_sequence_num=0,
            model_name="test-model",
            prompt="classify",
            status="success",
        )
        db.session.add(model_call)
        db.session.commit()
        ident = Identification(
            transcript_segment_id=segment.id,
            model_call_id=model_call.id,
            label="ad",
            confidence=0.9,
            start_time=0.0,
            end_time=10.0,
        )
        db.session.add(ident)
        db.session.commit()
        guid = post.guid

        result = insert_ad_correction_action(
            {
                "post_id": post.id,
                "label": "content",
                "kind": "false_positive",
                "start_time": 5.0,
                "end_time": 10.0,
            }
        )
        db.session.commit()
        assert result["id"] > 0

        client = app.test_client()
        response = client.get(f"/api/posts/{guid}/stats")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["corrections"]
        assert payload["corrections"][0]["label"] == "content"
        assert payload["post"]["has_unprocessed_audio"] is False
        blocks = payload["processing_stats"]["ad_blocks"]
        assert blocks
        assert blocks[0]["start_time"] == 0.0
        assert blocks[0]["end_time"] <= 5.0


def test_save_correction_route_recuts(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-save-guid", rss_url="https://example.com/corr-save.xml"
        )
        segment = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=1.0,
            end_time=8.0,
            text="head to example.com for details",
        )
        db.session.add(segment)
        db.session.commit()
        guid = post.guid

    client = app.test_client()
    with mock.patch(
        "podcast_processor.ad_corrections.recut_post_audio",
        return_value={"post_id": 1, "recut": True},
    ):
        response = client.post(
            f"/api/posts/{guid}/ad-corrections",
            json={
                "label": "ad",
                "kind": "missed_ad",
                "start_time": 1.0,
                "end_time": 8.0,
                "apply": True,
            },
        )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["correction"]["id"]
    assert payload["apply"]["recut"] is True
    with app.app_context():
        assert (
            AdCorrection.query.filter_by(
                post_id=payload["correction"]["post_id"]
            ).count()
            == 1
        )


def test_save_correction_route_skips_recut_when_apply_false(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-no-recut-guid",
            rss_url="https://example.com/corr-no-recut.xml",
        )
        segment = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=1.0,
            end_time=8.0,
            text="head to example.com for details",
        )
        db.session.add(segment)
        db.session.commit()
        guid = post.guid

    client = app.test_client()
    with mock.patch(
        "podcast_processor.ad_corrections.recut_post_audio",
    ) as recut_mock:
        response = client.post(
            f"/api/posts/{guid}/ad-corrections",
            json={
                "label": "ad",
                "kind": "missed_ad",
                "start_time": 1.0,
                "end_time": 8.0,
                "apply": False,
            },
        )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["correction"]["id"]
    assert payload.get("apply") is None
    recut_mock.assert_not_called()
    with app.app_context():
        assert (
            AdCorrection.query.filter_by(
                post_id=payload["correction"]["post_id"]
            ).count()
            == 1
        )


def test_save_correction_route_persists_when_recut_fails(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-fail-guid",
            rss_url="https://example.com/corr-fail.xml",
        )
        segment = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=1.0,
            end_time=8.0,
            text="head to example.com for details",
        )
        db.session.add(segment)
        db.session.commit()
        guid = post.guid

    client = app.test_client()
    with mock.patch(
        "podcast_processor.ad_corrections.recut_post_audio",
        side_effect=ValueError("Could not locate or download source audio for recut"),
    ):
        response = client.post(
            f"/api/posts/{guid}/ad-corrections",
            json={
                "label": "ad",
                "kind": "missed_ad",
                "start_time": 1.0,
                "end_time": 8.0,
                "apply": True,
            },
        )
    assert response.status_code == 500
    payload = response.get_json()
    assert "Could not locate or download source audio" in payload["error"]
    assert payload["correction"]["id"]
    with app.app_context():
        assert (
            AdCorrection.query.filter_by(
                post_id=payload["correction"]["post_id"]
            ).count()
            == 1
        )


def test_cleanup_marks_corrections_stale_but_keeps_rows(app) -> None:
    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-clean-guid", rss_url="https://example.com/corr-clean.xml"
        )
        correction = AdCorrection(
            post_id=post.id,
            feed_id=post.feed_id,
            kind="missed_ad",
            label="ad",
            start_time=1.0,
            end_time=2.0,
            example_text="head to example.com",
            stale=False,
        )
        db.session.add(correction)
        db.session.commit()
        post_id = post.id
        correction_id = correction.id

        clear_post_processing_data_action({"post_id": post_id})
        db.session.commit()

        row = db.session.get(AdCorrection, correction_id)
        assert row is not None
        assert row.stale is True


def test_keep_transcript_cleanup_does_not_stale_corrections(app) -> None:
    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-keep-guid", rss_url="https://example.com/corr-keep.xml"
        )
        correction = AdCorrection(
            post_id=post.id,
            feed_id=post.feed_id,
            kind="false_positive",
            label="content",
            start_time=59.4,
            end_time=61.4,
            example_text="It is July the 1st, 1936.",
            stale=False,
        )
        db.session.add(correction)
        db.session.commit()
        correction_id = correction.id

        clear_post_processing_data_keep_transcript_action({"post_id": post.id})
        db.session.commit()

        row = db.session.get(AdCorrection, correction_id)
        assert row is not None
        assert row.stale is False


def test_stale_corrections_are_not_applied_to_stats(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-stale-guid", rss_url="https://example.com/corr-stale.xml"
        )
        db.session.add(
            AdCorrection(
                post_id=post.id,
                feed_id=post.feed_id,
                kind="missed_ad",
                label="ad",
                start_time=1.0,
                end_time=9.0,
                example_text="buy now",
                stale=True,
            )
        )
        db.session.commit()
        guid = post.guid

    client = app.test_client()
    response = client.get(f"/api/posts/{guid}/stats")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["corrections"] == []
    assert payload["processing_stats"]["ad_blocks"] == []


def test_retrieve_examples_same_feed_only(app) -> None:
    with app.app_context():
        feed_a, post_a = _make_feed_post(
            app, guid="corr-ex-a", rss_url="https://example.com/corr-ex-a.xml"
        )
        _feed_b, _post_b = _make_feed_post(
            app, guid="corr-ex-b", rss_url="https://example.com/corr-ex-b.xml"
        )
        db.session.add(
            AdCorrection(
                post_id=post_a.id,
                feed_id=feed_a.id,
                kind="false_positive",
                label="content",
                start_time=59.4,
                end_time=61.4,
                example_text="It is July the 1st, 1936.",
                stale=False,
            )
        )
        db.session.commit()

        same_feed = retrieve_correction_examples(
            feed_id=feed_a.id,
            prompt_tag_id=None,
            query_text="It is July the 1st, 1936. The story continues.",
        )
        other_feed = retrieve_correction_examples(
            feed_id=_feed_b.id,
            prompt_tag_id=None,
            query_text="It is July the 1st, 1936. The story continues.",
        )
        assert same_feed
        assert same_feed[0].example_text.startswith("It is July")
        assert other_feed == []

        prompt = format_correction_examples_prompt(same_feed)
        assert "Human-reviewed examples for this show:" in prompt
        assert "CONTENT" in prompt
        composed = PodcastProcessor.build_ad_classification_system_prompt(
            "BASE",
            SimpleNamespace(prompt_tag=None, custom_llm_ad_prompt=None),
            examples_prompt=prompt,
        )
        omitted = PodcastProcessor.build_ad_classification_system_prompt(
            "BASE",
            SimpleNamespace(prompt_tag=None, custom_llm_ad_prompt=None),
            examples_prompt=format_correction_examples_prompt(other_feed),
        )
        assert "Human-reviewed examples" in composed
        assert omitted == "BASE"


def test_apply_route_recuts(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-apply-guid", rss_url="https://example.com/corr-apply.xml"
        )
        guid = post.guid
        post_id = post.id

    client = app.test_client()
    with mock.patch(
        "podcast_processor.ad_corrections.recut_post_audio",
        return_value={"post_id": post_id, "recut": True},
    ):
        response = client.post(f"/api/posts/{guid}/ad-corrections/apply")
    assert response.status_code == 200
    assert response.get_json()["recut"] is True


def test_get_ad_segments_applies_content_punch(app) -> None:
    from podcast_processor.audio_processor import AudioProcessor
    from shared.test_utils import create_standard_test_config

    with app.app_context():
        _feed, post = _make_feed_post(
            app, guid="corr-audio-guid", rss_url="https://example.com/corr-audio.xml"
        )
        segment = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=0.0,
            end_time=10.0,
            text="This message comes from WISE. Visit wise.com.",
        )
        db.session.add(segment)
        db.session.commit()
        model_call = ModelCall(
            post_id=post.id,
            first_segment_sequence_num=0,
            last_segment_sequence_num=0,
            model_name="test-model",
            prompt="classify",
            status="success",
        )
        db.session.add(model_call)
        db.session.commit()
        db.session.add(
            Identification(
                transcript_segment_id=segment.id,
                model_call_id=model_call.id,
                label="ad",
                confidence=0.99,
                start_time=0.0,
                end_time=10.0,
            )
        )
        db.session.add(
            AdCorrection(
                post_id=post.id,
                feed_id=post.feed_id,
                kind="false_positive",
                label="content",
                start_time=6.0,
                end_time=10.0,
                example_text="Visit wise.com.",
                stale=False,
            )
        )
        db.session.commit()

        windows = AudioProcessor(config=create_standard_test_config()).get_ad_segments(
            post
        )
        assert windows
        assert windows[0][0] == 0.0
        assert windows[0][1] <= 6.0 + 0.05
        assert all(end <= 6.05 for _start, end in windows)


def test_suggested_prompt_snippet_requires_repeats(app) -> None:
    with app.app_context():
        feed, post = _make_feed_post(
            app, guid="corr-promo", rss_url="https://example.com/corr-promo.xml"
        )
        for index in range(3):
            db.session.add(
                AdCorrection(
                    post_id=post.id,
                    feed_id=feed.id,
                    kind="false_positive",
                    label="content",
                    start_time=59.4 + index,
                    end_time=61.4 + index,
                    example_text="It is July the 1st, 1936.",
                    stale=False,
                )
            )
        db.session.commit()
        snippet = suggested_prompt_snippet(feed_id=feed.id)
        assert snippet is not None
        assert "CONTENT" in snippet
        assert "It is July" in snippet


def test_suggested_prompt_status_progress(app) -> None:
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-promo-progress",
            rss_url="https://example.com/corr-promo-p.xml",
        )
        for index in range(2):
            db.session.add(
                AdCorrection(
                    post_id=post.id,
                    feed_id=feed.id,
                    kind="false_positive",
                    label="content",
                    start_time=59.4 + index,
                    end_time=61.4 + index,
                    example_text="It is July the 1st, 1936.",
                    stale=False,
                )
            )
        db.session.commit()
        status = suggested_prompt_status(feed_id=feed.id)
        assert status["repeat_count"] == 2
        assert status["min_repeats"] == 3
        assert status["snippet"] is None


def test_suggested_prompt_status_hides_when_already_appended(app) -> None:
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-promo-done",
            rss_url="https://example.com/corr-promo-done.xml",
        )
        for index in range(3):
            db.session.add(
                AdCorrection(
                    post_id=post.id,
                    feed_id=feed.id,
                    kind="false_positive",
                    label="content",
                    start_time=59.4 + index,
                    end_time=61.4 + index,
                    example_text="It is July the 1st, 1936.",
                    stale=False,
                )
            )
        db.session.commit()
        ready = suggested_prompt_status(feed_id=feed.id)
        assert ready["snippet"] is not None
        feed.custom_llm_ad_prompt = ready["snippet"]
        db.session.commit()
        hidden = suggested_prompt_status(
            feed_id=feed.id,
            existing_prompt=feed.custom_llm_ad_prompt,
        )
        assert hidden["repeat_count"] == 3
        assert hidden["snippet"] is None


def test_processed_audio_needs_recut_when_correction_is_newer(app, tmp_path) -> None:
    import os

    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-needs-recut",
            rss_url="https://example.com/corr-needs-recut.xml",
        )
        processed = tmp_path / "processed.mp3"
        processed.write_bytes(b"processed")
        old_mtime = 1_000_000_000.0
        os.utime(processed, (old_mtime, old_mtime))
        post.processed_audio_path = str(processed)
        db.session.add(post)
        db.session.add(
            AdCorrection(
                post_id=post.id,
                feed_id=feed.id,
                kind="missed_ad",
                label="ad",
                start_time=1.0,
                end_time=3.0,
                example_text="buy our sponsor",
                stale=False,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        db.session.commit()
        assert processed_audio_needs_recut(post) is True


def test_processed_audio_needs_recut_false_when_file_is_fresh(app, tmp_path) -> None:
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-fresh-recut",
            rss_url="https://example.com/corr-fresh-recut.xml",
        )
        processed = tmp_path / "processed.mp3"
        processed.write_bytes(b"processed")
        post.processed_audio_path = str(processed)
        db.session.add(post)
        db.session.add(
            AdCorrection(
                post_id=post.id,
                feed_id=feed.id,
                kind="missed_ad",
                label="ad",
                start_time=1.0,
                end_time=3.0,
                example_text="buy our sponsor",
                stale=False,
                created_at=datetime.fromtimestamp(1_000_000_000, tz=UTC).replace(
                    tzinfo=None
                ),
            )
        )
        db.session.commit()
        assert processed_audio_needs_recut(post) is False


def test_stats_includes_suggested_prompt_and_needs_recut(app, tmp_path) -> None:
    import os

    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-stats-prompt",
            rss_url="https://example.com/corr-stats-prompt.xml",
        )
        processed = tmp_path / "processed.mp3"
        processed.write_bytes(b"processed")
        old_mtime = 1_000_000_000.0
        os.utime(processed, (old_mtime, old_mtime))
        post.processed_audio_path = str(processed)
        for index in range(3):
            db.session.add(
                AdCorrection(
                    post_id=post.id,
                    feed_id=feed.id,
                    kind="false_positive",
                    label="content",
                    start_time=59.4 + index,
                    end_time=61.4 + index,
                    example_text="It is July the 1st, 1936.",
                    stale=False,
                )
            )
        db.session.commit()
        guid = post.guid

    client = app.test_client()
    response = client.get(f"/api/posts/{guid}/stats")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["suggested_prompt"]["repeat_count"] == 3
    assert payload["suggested_prompt"]["snippet"] is not None
    assert payload["post"]["needs_recut"] is True


def test_cut_eligible_identifications_excludes_low_confidence() -> None:
    success_call = SimpleNamespace(id=1, status="success")
    failed_call = SimpleNamespace(id=2, status="failed")
    high = SimpleNamespace(
        label="ad",
        model_call_id=1,
        confidence=0.95,
        transcript_segment=SimpleNamespace(
            start_time=0.0, end_time=5.0, text="sponsor read"
        ),
        start_time=0.0,
        end_time=5.0,
    )
    low = SimpleNamespace(
        label="ad",
        model_call_id=1,
        confidence=0.2,
        transcript_segment=SimpleNamespace(start_time=10.0, end_time=15.0),
        start_time=10.0,
        end_time=15.0,
    )
    failed = SimpleNamespace(
        label="ad",
        model_call_id=2,
        confidence=0.95,
        transcript_segment=SimpleNamespace(start_time=20.0, end_time=25.0),
        start_time=20.0,
        end_time=25.0,
    )
    eligible = cut_eligible_identifications(
        [high, low, failed],
        [success_call, failed_call],
        min_confidence=0.8,
    )
    assert eligible == [high]
    _labeled, effective = final_cut_windows(eligible, [])
    assert effective == [(0.0, 5.0)]


def test_build_analyze_prompt_messages_includes_existing_and_corrections() -> None:
    messages = build_analyze_prompt_messages(
        corrections_block='[1.0-2.0] CONTENT (false_positive) "It is July"',
        existing_prompt="Keep cold opens.",
    )
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "Keep cold opens." in user
    assert "false_positive" in user
    assert "It is July" in user


def test_format_corrections_for_prompt_analysis() -> None:
    correction = SimpleNamespace(
        start_time=10.0,
        end_time=12.5,
        label="ad",
        kind="missed_ad",
        example_text="brought to you by Acme",
        reason="midroll",
    )
    block = format_corrections_for_prompt_analysis([correction])
    assert (
        '[10.0-12.5] AD (missed_ad) "brought to you by Acme" — note: midroll' in block
    )


def test_analyze_corrections_for_prompt_raises_without_corrections(app) -> None:
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-analyze-empty",
            rss_url="https://example.com/corr-analyze-empty.xml",
        )
        try:
            analyze_corrections_for_prompt(post_id=post.id)
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "No saved corrections" in str(exc)


def test_heuristic_prompt_draft_from_corrections_content_and_ads() -> None:
    corrections = [
        SimpleNamespace(
            label="content",
            example_text="Copyright 2026 by Audible Originals, LLC.",
        ),
        SimpleNamespace(
            label="content",
            example_text="Sound recording copyright 2026 by Audible Originals, LLC.",
        ),
        SimpleNamespace(
            label="ad",
            example_text="brought to you by Acme Widgets",
        ),
    ]
    draft = heuristic_prompt_draft_from_corrections(corrections)
    assert "CONTENT" in draft
    assert "Copyright 2026" in draft
    assert "Cut sponsor" in draft
    assert "Acme Widgets" in draft


def test_analyze_corrections_falls_back_when_model_returns_empty(app) -> None:
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-analyze-fallback",
            rss_url="https://example.com/corr-analyze-fallback.xml",
        )
        db.session.add(
            AdCorrection(
                post_id=post.id,
                feed_id=feed.id,
                kind="false_positive",
                label="content",
                start_time=2581.4,
                end_time=2584.5,
                example_text="Copyright 2026 by Audible Originals, LLC.",
                stale=False,
            )
        )
        db.session.commit()
        post_id = post.id

    empty_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
    )
    config = SimpleNamespace(
        llm_model="openai/google/gemma-4-12b",
        llm_api_key="test-key",
        openai_base_url="http://127.0.0.1:1234/v1",
        openai_timeout=30,
    )
    with app.app_context():
        with mock.patch(
            "litellm.completion", return_value=empty_response
        ) as completion:
            result = analyze_corrections_for_prompt(
                post_id=post_id,
                existing_prompt=None,
                config=config,
            )

        assert result["correction_count"] == 1
        assert "CONTENT" in result["draft"]
        assert "Copyright 2026" in result["draft"]
        assert completion.call_args.kwargs.get("max_tokens") == 800
        assert "max_completion_tokens" not in completion.call_args.kwargs


def test_analyze_prompt_route_returns_draft_without_writing_feed(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-analyze-guid",
            rss_url="https://example.com/corr-analyze.xml",
        )
        feed.custom_llm_ad_prompt = "Existing show rule."
        db.session.add(
            AdCorrection(
                post_id=post.id,
                feed_id=feed.id,
                kind="false_positive",
                label="content",
                start_time=59.4,
                end_time=61.4,
                example_text="It is July the 1st, 1936.",
                stale=False,
            )
        )
        db.session.commit()
        guid = post.guid
        feed_id = feed.id

    mock_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='Treat dated cold-opens like "It is July…" as CONTENT.'
                )
            )
        ]
    )
    client = app.test_client()
    with mock.patch(
        "litellm.completion",
        return_value=mock_response,
    ) as completion_mock:
        response = client.post(f"/api/posts/{guid}/ad-corrections/analyze-prompt")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["correction_count"] == 1
    assert payload["existing_prompt"] == "Existing show rule."
    assert "CONTENT" in payload["draft"]
    completion_mock.assert_called_once()
    call_kwargs = completion_mock.call_args.kwargs
    assert "Existing show rule." in call_kwargs["messages"][1]["content"]
    assert "It is July" in call_kwargs["messages"][1]["content"]

    with app.app_context():
        unchanged = db.session.get(Feed, feed_id)
        assert unchanged is not None
        assert unchanged.custom_llm_ad_prompt == "Existing show rule."


def test_analyze_prompt_route_empty_corrections_returns_400(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-analyze-400",
            rss_url="https://example.com/corr-analyze-400.xml",
        )
        guid = post.guid

    client = app.test_client()
    response = client.post(f"/api/posts/{guid}/ad-corrections/analyze-prompt")
    assert response.status_code == 400
    assert "No saved corrections" in response.get_json()["error"]


def test_insert_ad_correction_sets_transcript_reviewed(app) -> None:
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-reviewed-insert",
            rss_url="https://example.com/corr-reviewed-insert.xml",
        )
        db.session.add(
            TranscriptSegment(
                post_id=post.id,
                sequence_num=0,
                start_time=0.0,
                end_time=5.0,
                text="hello world",
            )
        )
        db.session.commit()
        assert post.transcript_reviewed_at is None

        insert_ad_correction_action(
            {
                "post_id": post.id,
                "label": "content",
                "kind": "false_positive",
                "start_time": 0.0,
                "end_time": 5.0,
            }
        )
        db.session.commit()
        db.session.refresh(post)
        assert post.transcript_reviewed_at is not None


def test_set_transcript_reviewed_action_and_route(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-reviewed-manual",
            rss_url="https://example.com/corr-reviewed-manual.xml",
        )
        post_id = post.id
        guid = post.guid

        result = set_post_transcript_reviewed_action(
            {"post_id": post_id, "reviewed": True}
        )
        db.session.commit()
        assert result["transcript_reviewed"] is True
        db.session.refresh(post)
        assert post.transcript_reviewed_at is not None

    client = app.test_client()
    response = client.post(
        f"/api/posts/{guid}/transcript-reviewed",
        json={"reviewed": False},
    )
    assert response.status_code == 200
    assert response.get_json()["transcript_reviewed"] is False

    with app.app_context():
        cleared = db.session.get(Post, post_id)
        assert cleared is not None
        assert cleared.transcript_reviewed_at is None

    bad = client.post(f"/api/posts/{guid}/transcript-reviewed", json={})
    assert bad.status_code == 400


def test_recut_marks_transcript_reviewed(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-reviewed-recut",
            rss_url="https://example.com/corr-reviewed-recut.xml",
        )
        db.session.add(
            TranscriptSegment(
                post_id=post.id,
                sequence_num=0,
                start_time=1.0,
                end_time=8.0,
                text="sponsor pitch",
            )
        )
        db.session.commit()
        guid = post.guid
        post_id = post.id

    client = app.test_client()
    with mock.patch(
        "podcast_processor.ad_corrections.recut_post_audio",
        return_value={"post_id": post_id, "recut": True},
    ):
        response = client.post(
            f"/api/posts/{guid}/ad-corrections",
            json={
                "label": "ad",
                "kind": "missed_ad",
                "start_time": 1.0,
                "end_time": 8.0,
                "apply": True,
            },
        )
    assert response.status_code == 200
    assert response.get_json()["apply"]["transcript_reviewed"] is True

    with app.app_context():
        reviewed = db.session.get(Post, post_id)
        assert reviewed is not None
        assert reviewed.transcript_reviewed_at is not None


def test_clear_processing_clears_transcript_reviewed(app) -> None:
    with app.app_context():
        _feed, post = _make_feed_post(
            app,
            guid="corr-reviewed-clear",
            rss_url="https://example.com/corr-reviewed-clear.xml",
        )
        post.transcript_reviewed_at = datetime.now(UTC).replace(tzinfo=None)
        post.processed_audio_path = "/tmp/fake.mp3"
        db.session.commit()
        post_id = post.id

        clear_post_processing_data_action({"post_id": post_id})
        db.session.commit()
        cleared = db.session.get(Post, post_id)
        assert cleared is not None
        assert cleared.transcript_reviewed_at is None

        cleared.transcript_reviewed_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()
        clear_post_processing_data_keep_transcript_action({"post_id": post_id})
        db.session.commit()
        cleared_keep = db.session.get(Post, post_id)
        assert cleared_keep is not None
        assert cleared_keep.transcript_reviewed_at is None


def test_feed_posts_list_includes_transcript_reviewed(app) -> None:
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        feed, post = _make_feed_post(
            app,
            guid="corr-reviewed-list",
            rss_url="https://example.com/corr-reviewed-list.xml",
        )
        post.transcript_reviewed_at = datetime.now(UTC).replace(tzinfo=None)
        db.session.commit()
        feed_id = feed.id

    client = app.test_client()
    response = client.get(f"/api/feeds/{feed_id}/posts")
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert len(items) == 1
    assert items[0]["transcript_reviewed"] is True
    assert items[0]["guid"] == "corr-reviewed-list"
