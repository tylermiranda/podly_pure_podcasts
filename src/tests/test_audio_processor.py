import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.extensions import db
from app.models import Feed, Identification, ModelCall, Post, TranscriptSegment
from app.routes.post_stats_utils import cut_eligible_identifications, final_cut_windows
from podcast_processor.ad_merger import AdGroup, AdMerger
from podcast_processor.audio_processor import AudioProcessor
from shared.config import Config
from shared.test_utils import create_standard_test_config
from tests.salvador_dali_fixture import (
    NARRATIVE_START_TIMES,
    SALVADOR_DALI_GOLD_WINDOWS,
    persist_salvador_dali_episode,
    windows_cover,
)


@pytest.fixture
def test_processor(
    test_config: Config,
    test_logger: logging.Logger,
) -> AudioProcessor:
    """Return an AudioProcessor instance with default dependencies for testing."""
    return AudioProcessor(config=test_config, logger=test_logger)


@pytest.fixture
def test_processor_with_mocks(
    test_config: Config,
    test_logger: logging.Logger,
    mock_db_session: MagicMock,
) -> AudioProcessor:
    """Return an AudioProcessor instance with mock dependencies for testing."""
    mock_identification_query = MagicMock()
    mock_transcript_segment_query = MagicMock()
    mock_model_call_query = MagicMock()

    return AudioProcessor(
        config=test_config,
        logger=test_logger,
        identification_query=mock_identification_query,
        transcript_segment_query=mock_transcript_segment_query,
        model_call_query=mock_model_call_query,
        db_session=mock_db_session,
    )


def test_get_ad_segments(app: Flask) -> None:
    """Test retrieving ad segments from the database"""
    # Create test data
    post = Post(id=1, title="Test Post")
    segment = TranscriptSegment(
        id=1,
        post_id=1,
        sequence_num=0,
        start_time=0.0,
        end_time=10.0,
        text="Test segment",
    )
    identification = Identification(
        transcript_segment_id=1, model_call_id=1, label="ad", confidence=0.9
    )

    with app.app_context():
        # Create mocks
        mock_identification_query = MagicMock()
        mock_query_chain = MagicMock()
        mock_identification_query.join.return_value = mock_query_chain
        mock_query_chain.join.return_value = mock_query_chain
        mock_query_chain.filter.return_value = mock_query_chain
        mock_query_chain.all.return_value = [identification]

        # Create processor with mocks
        test_processor = AudioProcessor(
            config=create_standard_test_config(),
            identification_query=mock_identification_query,
        )

        with patch.object(identification, "transcript_segment", segment):
            segments = test_processor.get_ad_segments(post)

            assert len(segments) == 1
            assert segments[0] == (0.0, 10.0)


def test_get_ad_segments_trims_mixed_whisper_chunk(app: Flask) -> None:
    post = Post(id=1, title="Test Post")
    segment = TranscriptSegment(
        id=1,
        post_id=1,
        sequence_num=0,
        start_time=158.5,
        end_time=174.1,
        text=(
            "ACAST.com Welcome to the Tupac Murder Trial from the history of the 90s. "
            "I'm your host, Kathy Kanzora."
        ),
    )
    identification = Identification(
        transcript_segment_id=1, model_call_id=1, label="ad", confidence=0.95
    )

    with app.app_context():
        mock_identification_query = MagicMock()
        mock_query_chain = MagicMock()
        mock_identification_query.join.return_value = mock_query_chain
        mock_query_chain.join.return_value = mock_query_chain
        mock_query_chain.filter.return_value = mock_query_chain
        mock_query_chain.all.return_value = [identification]

        processor = AudioProcessor(
            config=create_standard_test_config(),
            identification_query=mock_identification_query,
        )

        with patch.object(identification, "transcript_segment", segment):
            segments = processor.get_ad_segments(post)

        assert len(segments) == 1
        start, end = segments[0]
        assert start == 158.5
        assert end < 165.0
        assert segment.end_time == 174.1


def test_get_ad_segments_trims_mixed_chunk_using_word_times(app: Flask) -> None:
    post = Post(id=1, title="Test Post")
    segment = TranscriptSegment(
        id=1,
        post_id=1,
        sequence_num=0,
        start_time=158.5,
        end_time=174.1,
        text=(
            "ACAST.com Welcome to the Tupac Murder Trial from the history of the 90s. "
            "I'm your host, Kathy Kanzora."
        ),
        words=[
            {"word": "ACAST.com", "start": 158.5, "end": 159.4},
            {"word": " Welcome", "start": 162.4, "end": 162.9},
            {"word": " to", "start": 162.9, "end": 163.1},
        ],
    )
    identification = Identification(
        transcript_segment_id=1, model_call_id=1, label="ad", confidence=0.95
    )

    with app.app_context():
        mock_identification_query = MagicMock()
        mock_query_chain = MagicMock()
        mock_identification_query.join.return_value = mock_query_chain
        mock_query_chain.join.return_value = mock_query_chain
        mock_query_chain.filter.return_value = mock_query_chain
        mock_query_chain.all.return_value = [identification]

        processor = AudioProcessor(
            config=create_standard_test_config(),
            identification_query=mock_identification_query,
        )

        with patch.object(identification, "transcript_segment", segment):
            segments = processor.get_ad_segments(post)

        assert len(segments) == 1
        start, end = segments[0]
        assert start == 158.5
        assert end == pytest.approx(162.2)
        assert segment.end_time == 174.1


def test_get_ad_segments_honors_identification_span(app: Flask) -> None:
    post = Post(id=1, title="Test Post")
    segment = TranscriptSegment(
        id=1,
        post_id=1,
        sequence_num=0,
        start_time=0.0,
        end_time=40.0,
        text="This message comes from WISE. Send money abroad.",
    )
    identification = Identification(
        transcript_segment_id=1,
        model_call_id=1,
        label="ad",
        confidence=0.95,
        start_time=0.0,
        end_time=18.0,
    )

    with app.app_context():
        mock_identification_query = MagicMock()
        mock_query_chain = MagicMock()
        mock_identification_query.join.return_value = mock_query_chain
        mock_query_chain.join.return_value = mock_query_chain
        mock_query_chain.filter.return_value = mock_query_chain
        mock_query_chain.all.return_value = [identification]

        processor = AudioProcessor(
            config=create_standard_test_config(),
            identification_query=mock_identification_query,
        )

        with patch.object(identification, "transcript_segment", segment):
            segments = processor.get_ad_segments(post)

        assert segments == [(0.0, 18.0)]


def test_get_ad_segments_expands_dali_cta_labels(app: Flask) -> None:
    with app.app_context():
        post, _ = persist_salvador_dali_episode(db.session, guid="dali-audio-guid")
        processor = AudioProcessor(config=create_standard_test_config())
        windows = processor.get_ad_segments(post)

    assert len(windows) == len(SALVADOR_DALI_GOLD_WINDOWS)
    for predicted, gold in zip(windows, SALVADOR_DALI_GOLD_WINDOWS, strict=True):
        assert predicted[0] == pytest.approx(gold[0], abs=0.2)
        assert predicted[1] == pytest.approx(gold[1], abs=0.2)
    for stamp in NARRATIVE_START_TIMES:
        assert not windows_cover(windows, stamp)


def test_merge_ad_segments(
    test_processor_with_mocks: AudioProcessor,
) -> None:
    """Test merging of nearby ad segments"""
    duration_ms = 30000  # 30 seconds
    ad_segments = [
        (0.0, 5.0),  # 0-5s
        (6.0, 10.0),  # 6-10s - should merge with first segment
        (20.0, 25.0),  # 20-25s - should stay separate
    ]

    merged = test_processor_with_mocks.merge_ad_segments(
        duration_ms=duration_ms,
        ad_segments=ad_segments,
        min_ad_segment_length_seconds=2.0,
        min_ad_segment_separation_seconds=2.0,
    )

    # Should merge first two segments
    assert len(merged) == 2
    assert merged[0] == (0, 10000)  # 0-10s
    assert merged[1] == (20000, 25000)  # 20-25s


def test_merge_ad_segments_caps_glue_gap_below_config_separation(
    test_processor_with_mocks: AudioProcessor,
) -> None:
    """A 60s config gap must not glue distinct midrolls 12s apart."""
    merged = test_processor_with_mocks.merge_ad_segments(
        duration_ms=200000,
        ad_segments=[(0.0, 30.0), (42.0, 70.0)],
        min_ad_segment_length_seconds=2.0,
        min_ad_segment_separation_seconds=60.0,
    )
    assert len(merged) == 2
    assert merged[0] == (0, 30000)
    assert merged[1] == (42000, 70000)


def test_merge_ad_segments_with_short_segments(
    test_processor_with_mocks: AudioProcessor,
) -> None:
    """Test that segments shorter than minimum length are filtered out"""
    duration_ms = 30000
    ad_segments = [
        (0.0, 1.0),  # Too short, should be filtered
        (10.0, 15.0),  # Long enough, should stay
        (20.0, 20.5),  # Too short, should be filtered
    ]

    merged = test_processor_with_mocks.merge_ad_segments(
        duration_ms=duration_ms,
        ad_segments=ad_segments,
        min_ad_segment_length_seconds=2.0,
        min_ad_segment_separation_seconds=2.0,
    )

    assert len(merged) == 1
    assert merged[0] == (10000, 15000)


def test_merge_ad_segments_end_extension(
    test_processor_with_mocks: AudioProcessor,
) -> None:
    """Test that segments near the end are extended to the end"""
    duration_ms = 30000
    ad_segments = [
        (28.0, 29.0),  # Near end, should extend to 30s
    ]

    merged = test_processor_with_mocks.merge_ad_segments(
        duration_ms=duration_ms,
        ad_segments=ad_segments,
        min_ad_segment_length_seconds=2.0,
        min_ad_segment_separation_seconds=2.0,
    )

    assert len(merged) == 1
    assert merged[0] == (28000, 30000)  # Extended to end


def test_process_audio(
    app: Flask,
    test_config: Config,
    test_logger: logging.Logger,
) -> None:
    """Test the process_audio method"""
    with app.app_context():
        processor = AudioProcessor(
            config=test_config, logger=test_logger, db_session=db.session
        )

        feed = Feed(title="Test Feed", rss_url="http://example.com/rss.xml")
        db.session.add(feed)
        db.session.commit()

        post = Post(
            feed_id=feed.id,
            title="Test Post",
            guid="test-audio-guid",
            download_url="http://example.com/audio.mp3",
            unprocessed_audio_path="path/to/audio.mp3",
        )
        db.session.add(post)
        db.session.commit()

        output_path = "path/to/output.mp3"

        # Set up mocks for get_ad_segments and get_audio_duration_ms
        with (
            patch.object(processor, "get_ad_segments", return_value=[(5.0, 10.0)]),
            patch(
                "podcast_processor.audio_processor.get_audio_duration_ms",
                side_effect=[30000, 24000],
            ),
            patch(
                "podcast_processor.audio_processor.clip_segments_with_fade"
            ) as mock_clip,
        ):
            # Call the method
            removed_segments = processor.process_audio(post, output_path)

            refreshed = db.session.get(Post, post.id)
            assert refreshed is not None
            assert refreshed.duration == 24.0  # processed output duration
            assert refreshed.processed_audio_path == output_path
            # The default test config extends a final ad segment to the end when
            # it is within the minimum separation threshold of the episode end.
            assert removed_segments == [(5000, 30000)]
            mock_clip.assert_called_once()


GROW_PREROLL_ROWS: list[tuple[int, float, float, str, bool]] = [
    (0, 0.2, 2.3, "Summer's supposed to be the easy season.", False),
    (
        1,
        2.7,
        7.4,
        "So why are so many people quietly Googling a therapist between summer Fridays?",
        False,
    ),
    (2, 7.6, 10.1, "Because more daylight doesn't fix the hard stuff.", False),
    (3, 10.4, 12.2, "Sometimes it just turns the volume up.", False),
    (4, 12.4, 13.9, "Grow does therapy differently.", False),
    (
        5,
        14.2,
        23.1,
        "Grow connects you with thousands of high-quality, licensed therapists across the U.S.",
        True,
    ),
    (
        6,
        23.3,
        26.1,
        "There are no subscriptions, no long-term commitments.",
        False,
    ),
    (
        7,
        26.5,
        30.0,
        "It is August 18, 2026, in a packed Las Vegas courtroom.",
        False,
    ),
]


def persist_grow_preroll_episode(db_session, *, guid: str = "grow-preroll-guid"):
    feed = Feed(title="History of the 90s", rss_url=f"https://example.com/{guid}.rss")
    db_session.add(feed)
    db_session.commit()
    post = Post(
        feed_id=feed.id,
        guid=guid,
        download_url=f"https://example.com/{guid}.mp3",
        title="The Tupac Murder Trial",
        unprocessed_audio_path="/tmp/grow.mp3",
        whitelisted=True,
    )
    db_session.add(post)
    db_session.commit()

    segments = []
    for sequence_num, start, end, body, _labeled in GROW_PREROLL_ROWS:
        row = TranscriptSegment(
            post_id=post.id,
            sequence_num=sequence_num,
            start_time=start,
            end_time=end,
            text=body,
        )
        segments.append(row)
    db_session.add_all(segments)
    db_session.commit()

    model_call = ModelCall(
        post_id=post.id,
        model_name="test-model",
        first_segment_sequence_num=0,
        last_segment_sequence_num=GROW_PREROLL_ROWS[-1][0],
        prompt="classify",
        response='{"ad_segments":[]}',
        status="success",
    )
    db_session.add(model_call)
    db_session.commit()

    cta = next(
        row for row, spec in zip(segments, GROW_PREROLL_ROWS, strict=True) if spec[4]
    )
    db_session.add(
        Identification(
            transcript_segment_id=cta.id,
            model_call_id=model_call.id,
            label="ad",
            confidence=0.85,
        )
    )
    db_session.commit()
    return post, segments


def test_ad_merger_drops_weak_grow_cta_group() -> None:
    """Regression: AdMerger used to drop this group before expand, hiding it from ffmpeg."""
    merger = AdMerger()
    group = AdGroup(
        segments=[],
        identifications=[],
        start_time=14.2,
        end_time=23.1,
        confidence_avg=0.85,
        keywords=[],
    )
    assert merger._is_valid_group(group) is False


def test_get_ad_segments_keeps_grow_preroll_matching_stats(app: Flask) -> None:
    with app.app_context():
        post, _segments = persist_grow_preroll_episode(db.session)
        processor = AudioProcessor(config=create_standard_test_config())
        windows = processor.get_ad_segments(post)

        identifications = (
            Identification.query.join(TranscriptSegment)
            .filter(TranscriptSegment.post_id == post.id)
            .all()
        )
        model_calls = ModelCall.query.filter_by(post_id=post.id).all()
        eligible = cut_eligible_identifications(
            identifications, model_calls, min_confidence=0.7
        )
        _labeled, stats_blocks = final_cut_windows(eligible, post.segments.all())

    assert windows == stats_blocks
    assert len(windows) == 1
    start, end = windows[0]
    assert start == pytest.approx(0.2, abs=0.05)
    assert end == pytest.approx(26.1, abs=0.05)


def test_merge_ad_segments_keeps_grow_preroll_at_legacy_min_length(
    test_processor_with_mocks: AudioProcessor,
) -> None:
    window = [(0.2, 26.1)]
    for min_length in (5.0, 14.0):
        merged = test_processor_with_mocks.merge_ad_segments(
            duration_ms=900000,
            ad_segments=window,
            min_ad_segment_length_seconds=min_length,
            min_ad_segment_separation_seconds=60.0,
        )
        assert merged == [(200, 26100)]


def test_sqlite_wal_snapshot_hides_inserts_until_rollback(tmp_path: Path) -> None:
    """SQLite WAL readers miss other-connection commits until rollback/commit."""
    db_path = tmp_path / "wal_snapshot.sqlite"
    bootstrap = sqlite3.connect(str(db_path))
    bootstrap.execute("PRAGMA journal_mode=WAL")
    bootstrap.execute(
        "CREATE TABLE identification (id INTEGER PRIMARY KEY, label TEXT)"
    )
    bootstrap.commit()
    bootstrap.close()

    reader = sqlite3.connect(str(db_path))
    reader.isolation_level = "DEFERRED"
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM identification").fetchone()[0] == 0

        writer_conn = sqlite3.connect(str(db_path))
        try:
            writer_conn.execute("INSERT INTO identification (label) VALUES ('ad')")
            writer_conn.commit()
        finally:
            writer_conn.close()

        assert reader.execute("SELECT COUNT(*) FROM identification").fetchone()[0] == 0
        reader.rollback()
        assert reader.execute("SELECT COUNT(*) FROM identification").fetchone()[0] == 1
    finally:
        reader.close()
