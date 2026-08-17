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
from app.writer.actions.processor import insert_ad_correction_action
from podcast_processor.ad_corrections import (
    format_correction_examples_prompt,
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
