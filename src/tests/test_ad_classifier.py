from collections.abc import Generator
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from jinja2 import Template
from litellm.exceptions import InternalServerError
from litellm.types.utils import Choices

from app.extensions import db
from app.models import Feed, Identification, ModelCall, Post, TranscriptSegment
from podcast_processor.ad_classifier import AdClassifier
from podcast_processor.model_output import (
    AdSegmentPrediction,
    AdSegmentPredictionList,
)
from shared.config import Config
from shared.test_utils import create_standard_test_config
from tests.salvador_dali_fixture import persist_salvador_dali_episode


@pytest.fixture
def app() -> Generator[Flask, None, None]:
    """Create and configure a Flask app for testing."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app


@pytest.fixture
def test_config() -> Config:
    return create_standard_test_config()


@pytest.fixture
def mock_db_session() -> MagicMock:
    """Create a mock database session"""
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.add_all = MagicMock()
    mock_session.commit = MagicMock()
    mock_session.rollback = MagicMock()
    return mock_session


@pytest.fixture
def test_classifier(test_config: Config) -> AdClassifier:
    """Create an AdClassifier with default dependencies"""
    return AdClassifier(config=test_config)


@pytest.fixture
def test_classifier_with_mocks(
    test_config: Config, mock_db_session: MagicMock
) -> AdClassifier:
    """Create an AdClassifier with mock dependencies"""
    mock_model_call_query = MagicMock()
    mock_identification_query = MagicMock()

    return AdClassifier(
        config=test_config,
        model_call_query=mock_model_call_query,
        identification_query=mock_identification_query,
        db_session=mock_db_session,
    )


def test_call_model(test_config: Config, app: Flask) -> None:
    """Test the _call_model method with mocked litellm"""
    with app.app_context():
        classifier = AdClassifier(config=test_config, db_session=db.session)

        # Create and persist a ModelCall row (writer_client local fallback updates by id)
        dummy_model_call = ModelCall(
            post_id=0,
            model_name=test_config.llm_model,
            prompt="test prompt",
            first_segment_sequence_num=0,
            last_segment_sequence_num=0,
            status="pending",
        )
        db.session.add(dummy_model_call)
        db.session.commit()

        # Create a mock message and choice directly
        mock_message = MagicMock()
        mock_message.content = "test response"

        mock_choice = MagicMock(spec=Choices)
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        # Patch the litellm.completion function for this test
        with patch("litellm.completion", return_value=mock_response):
            # Call the method
            response = classifier._call_model(
                model_call_obj=dummy_model_call,
                system_prompt="test system prompt",
            )

            # Verify response
            assert response == "test response"
            refreshed = db.session.get(ModelCall, dummy_model_call.id)
            assert refreshed is not None
            assert refreshed.status == "success"
            assert refreshed.response == "test response"


def test_call_model_retry_on_internal_error(test_config: Config, app: Flask) -> None:
    """Test that _call_model retries on InternalServerError"""
    with app.app_context():
        classifier = AdClassifier(config=test_config, db_session=db.session)

        dummy_model_call = ModelCall(
            post_id=0,
            model_name=test_config.llm_model,
            prompt="test prompt",
            first_segment_sequence_num=0,
            last_segment_sequence_num=0,
            status="pending",
        )
        db.session.add(dummy_model_call)
        db.session.commit()

        # Create a mock message and choice directly
        mock_message = MagicMock()
        mock_message.content = "test response"

        mock_choice = MagicMock(spec=Choices)
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        # First call fails, second succeeds
        mock_completion_side_effects = [
            InternalServerError(
                message="test error",
                llm_provider="test_provider",
                model="test_model",
            ),
            mock_response,
        ]

        # Patch time.sleep to avoid waiting during tests
        with (
            patch("time.sleep"),
            patch(
                "litellm.completion", side_effect=mock_completion_side_effects
            ) as mocked_completion,
        ):
            response = classifier._call_model(
                model_call_obj=dummy_model_call,
                system_prompt="test system prompt",
            )

            assert response == "test response"
            assert mocked_completion.call_count == 2
            refreshed = db.session.get(ModelCall, dummy_model_call.id)
            assert refreshed is not None
            assert refreshed.status == "success"
            assert refreshed.response == "test response"
            assert refreshed.retry_attempts == 2


def test_process_chunk(test_config: Config, app: Flask) -> None:
    """Test processing a chunk of transcript segments"""
    with app.app_context():
        # Create mocks
        mock_db_session = MagicMock()
        mock_model_call_query = MagicMock()

        # Create the classifier with our mocks
        classifier = AdClassifier(
            config=test_config,
            model_call_query=mock_model_call_query,
            db_session=mock_db_session,
        )

        # Create test data
        post = Post(id=1, title="Test Post")
        segments = [
            TranscriptSegment(
                id=1,
                post_id=1,
                sequence_num=0,
                start_time=0.0,
                end_time=10.0,
                text="Test segment 1",
            ),
            TranscriptSegment(
                id=2,
                post_id=1,
                sequence_num=1,
                start_time=10.0,
                end_time=20.0,
                text="Test segment 2",
            ),
        ]

        # Create a proper Jinja2 Template object
        user_template = Template("Test template: {{ podcast_title }}")

        user_prompt = classifier._generate_user_prompt(
            current_chunk_db_segments=segments,
            post=post,
            user_prompt_template=user_template,
            includes_start=True,
            includes_end=True,
        )

        # Create an actual ModelCall instance instead of a MagicMock
        model_call = ModelCall(
            post_id=1,
            model_name=test_config.llm_model,
            prompt="test prompt",
            first_segment_sequence_num=0,
            last_segment_sequence_num=1,
            status="success",
            response='{"ad_segments": []}',
        )

        # Use patch.multiple to mock multiple methods with a single context manager
        mock_get_model_call = MagicMock(return_value=model_call)
        mock_process_response = MagicMock(return_value=segments)

        with patch.multiple(
            classifier,
            _get_or_create_model_call=mock_get_model_call,
            _process_successful_response=mock_process_response,
        ):
            result = classifier._process_chunk(
                chunk_segments=segments,
                system_prompt="test system prompt",
                post=post,
                user_prompt_str=user_prompt,
            )

            mock_get_model_call.assert_called_once()
            mock_process_response.assert_called_once()
            assert result == segments


def test_compute_next_overlap_segments_includes_context(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    classifier = test_classifier_with_mocks
    segments = [
        TranscriptSegment(
            id=i + 1,
            post_id=1,
            sequence_num=i,
            start_time=float(i),
            end_time=float(i + 1),
            text=f"Segment {i}",
        )
        for i in range(6)
    ]

    identified_segments = [segments[2], segments[3], segments[4]]

    result = classifier._compute_next_overlap_segments(
        chunk_segments=segments,
        identified_segments=identified_segments,
        max_overlap_segments=6,
    )

    assert [seg.sequence_num for seg in result] == [0, 1, 2, 3, 4, 5]


def test_compute_next_overlap_segments_respects_cap(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    classifier = test_classifier_with_mocks
    segments = [
        TranscriptSegment(
            id=i + 1,
            post_id=1,
            sequence_num=i,
            start_time=float(i),
            end_time=float(i + 1),
            text=f"Segment {i}",
        )
        for i in range(6)
    ]
    identified_segments = [segments[2], segments[3], segments[4]]

    result = classifier._compute_next_overlap_segments(
        chunk_segments=segments,
        identified_segments=identified_segments,
        max_overlap_segments=2,
    )

    assert [seg.sequence_num for seg in result] == [4, 5]


def test_compute_next_overlap_segments_baseline_overlap_without_ads(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    classifier = test_classifier_with_mocks
    segments = [
        TranscriptSegment(
            id=i + 1,
            post_id=1,
            sequence_num=i,
            start_time=float(i),
            end_time=float(i + 1),
            text=f"Segment {i}",
        )
        for i in range(8)
    ]

    result = classifier._compute_next_overlap_segments(
        chunk_segments=segments, identified_segments=[], max_overlap_segments=4
    )

    assert [seg.sequence_num for seg in result] == [4, 5, 6, 7]


def test_create_identifications_skips_content_only_label(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    classifier = test_classifier_with_mocks
    mock_query = classifier.identification_query
    mock_query.filter_by.return_value.first.return_value = None

    segment = TranscriptSegment(
        id=1,
        post_id=1,
        sequence_num=0,
        start_time=925.4,
        end_time=955.1,
        text="Okay, that's it for this episode of the Tupac murder trial.",
    )
    prediction_list = AdSegmentPredictionList(
        ad_segments=[AdSegmentPrediction(segment_offset=925.4, confidence=0.95)]
    )
    model_call = ModelCall(
        post_id=1,
        model_name=classifier.config.llm_model,
        prompt="prompt",
        first_segment_sequence_num=0,
        last_segment_sequence_num=0,
    )

    created_count, matched_segments = classifier._create_identifications(
        prediction_list=prediction_list,
        current_chunk_db_segments=[segment],
        model_call=model_call,
    )

    assert created_count == 0
    assert matched_segments == []

    classifier = test_classifier_with_mocks
    mock_query = classifier.identification_query
    mock_query.filter_by.return_value.first.return_value = MagicMock()

    segment = TranscriptSegment(
        id=1,
        post_id=1,
        sequence_num=0,
        start_time=0.0,
        end_time=10.0,
        text="Test segment",
    )
    prediction_list = AdSegmentPredictionList(
        ad_segments=[AdSegmentPrediction(segment_offset=0.0, confidence=0.9)]
    )
    model_call = ModelCall(
        post_id=1,
        model_name=classifier.config.llm_model,
        prompt="prompt",
        first_segment_sequence_num=0,
        last_segment_sequence_num=0,
    )

    created_count, matched_segments = classifier._create_identifications(
        prediction_list=prediction_list,
        current_chunk_db_segments=[segment],
        model_call=model_call,
    )

    assert created_count == 0
    assert matched_segments == [segment]
    cast(MagicMock, classifier.db_session.add).assert_not_called()


def test_build_chunk_payload_trims_for_token_limit(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    classifier = test_classifier_with_mocks
    classifier.config.processing.num_segments_to_input_to_prompt = 3
    classifier.config.processing.max_overlap_segments = 5
    classifier.config.llm_max_input_tokens_per_call = 1000

    overlap_segments = [
        TranscriptSegment(
            id=1,
            post_id=1,
            sequence_num=0,
            start_time=0.0,
            end_time=1.0,
            text="Overlap",
        )
    ]
    remaining_segments = [
        TranscriptSegment(
            id=i + 2,
            post_id=1,
            sequence_num=i + 1,
            start_time=float(i + 1),
            end_time=float(i + 2),
            text=f"Segment {i + 1}",
        )
        for i in range(3)
    ]

    system_prompt = "System"
    template = Template("{{ transcript }}")

    with patch.object(
        classifier,
        "_validate_token_limit",
        side_effect=[False, True],
    ) as mock_validator:
        chunk_segments, user_prompt, consumed, trimmed = (
            classifier._build_chunk_payload(
                overlap_segments=overlap_segments,
                remaining_segments=remaining_segments,
                total_segments=overlap_segments + remaining_segments,
                post=Post(id=1, title="Test"),
                system_prompt=system_prompt,
                user_prompt_template=template,
                max_new_segments=3,
            )
        )

    assert trimmed is True
    assert consumed == 2
    assert len(chunk_segments) >= consumed
    assert mock_validator.call_count == 2
    assert user_prompt


def test_apply_sponsor_cue_labels_marks_unlabeled_acast(
    test_config: Config, app: Flask
) -> None:
    with app.app_context():
        feed = Feed(title="History of the 90s", rss_url="http://example.com/h90.rss")
        post = Post(
            feed=feed,
            guid="suge-1",
            download_url="http://example.com/suge.mp3",
            title="Suge Knight",
            unprocessed_audio_path="/tmp/suge.mp3",
        )
        db.session.add_all([feed, post])
        db.session.commit()

        model_call = ModelCall(
            post_id=post.id,
            model_name="openai/~deepseek/deepseek-v4-flash-latest",
            first_segment_sequence_num=0,
            last_segment_sequence_num=1,
            prompt="classify",
            response='{"segment_offset": 0.0, "confidence": 0.95}',
            status="success",
            language=None,
        )
        promo = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=0.0,
            end_time=25.0,
            text="ACAST powers the world's best podcasts. Here's a show we recommend.",
        )
        content = TranscriptSegment(
            post_id=post.id,
            sequence_num=1,
            start_time=43.5,
            end_time=68.4,
            text="Coming up today, the final phase of jury selection.",
        )
        db.session.add_all([model_call, promo, content])
        db.session.commit()

        classifier = AdClassifier(config=test_config, db_session=db.session)
        created = classifier._apply_sponsor_cue_labels([promo, content], post)

        assert created == 1
        labeled = Identification.query.filter_by(label="ad").all()
        assert len(labeled) == 1
        assert labeled[0].transcript_segment_id == promo.id
        assert labeled[0].confidence >= 0.9
        assert labeled[0].start_time == 0.0
        assert labeled[0].end_time == 25.0


def test_apply_sponsor_cue_labels_generic_url_not_brand_list(
    test_config: Config, app: Flask
) -> None:
    with app.app_context():
        feed = Feed(title="History Daily", rss_url="http://example.com/hd.rss")
        post = Post(
            feed=feed,
            guid="manila-1",
            download_url="http://example.com/manila.mp3",
            title="Manila",
            unprocessed_audio_path="/tmp/manila.mp3",
        )
        db.session.add_all([feed, post])
        db.session.commit()

        model_call = ModelCall(
            post_id=post.id,
            model_name="openai/~deepseek/deepseek-v4-flash-latest",
            first_segment_sequence_num=0,
            last_segment_sequence_num=1,
            prompt="classify",
            response='{"ad_segments":[]}',
            status="success",
            language=None,
        )
        promo = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=179.8,
            end_time=205.6,
            text="Go to HistoryDailyLive.com. That's HistoryDailyLive.com.",
        )
        story = TranscriptSegment(
            post_id=post.id,
            sequence_num=1,
            start_time=206.0,
            end_time=227.6,
            text="Cuba lies only 90 miles from Florida.",
        )
        db.session.add_all([model_call, promo, story])
        db.session.commit()

        classifier = AdClassifier(config=test_config, db_session=db.session)
        created = classifier._apply_sponsor_cue_labels([promo, story], post)

        assert created == 1
        labeled = Identification.query.filter_by(label="ad").all()
        assert labeled[0].transcript_segment_id == promo.id


def test_should_not_expand_neighbor_on_gap_alone(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    assert (
        test_classifier_with_mocks._should_expand_neighbor(
            has_strong_cue=False,
            is_transition=False,
            gap_seconds=3.0,
        )
        is False
    )
    assert (
        test_classifier_with_mocks._should_expand_neighbor(
            has_strong_cue=False,
            is_transition=True,
            gap_seconds=3.0,
        )
        is True
    )


def test_should_expand_neighbor_on_recoverable_copy(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    assert (
        test_classifier_with_mocks._should_expand_neighbor(
            has_strong_cue=False,
            is_transition=False,
            gap_seconds=5.0,
            is_recoverable=True,
        )
        is True
    )


def test_apply_sponsor_cue_labels_promotional_copy_without_url(
    test_config: Config, app: Flask
) -> None:
    with app.app_context():
        feed = Feed(title="Short History Of", rss_url="http://example.com/sho.rss")
        post = Post(
            feed=feed,
            guid="noiser-plus-1",
            download_url="http://example.com/sho.mp3",
            title="Noiser Plus",
            unprocessed_audio_path="/tmp/sho.mp3",
        )
        db.session.add_all([feed, post])
        db.session.commit()
        model_call = ModelCall(
            post_id=post.id,
            model_name="test-model",
            first_segment_sequence_num=0,
            last_segment_sequence_num=1,
            prompt="classify",
            response='{"ad_segments":[]}',
            status="success",
            language=None,
        )
        promo = TranscriptSegment(
            post_id=post.id,
            sequence_num=0,
            start_time=2996.3,
            end_time=3002.6,
            text=(
                "You can listen to the next two episodes of Short History of "
                "right now, without waiting and without adverts, by subscribing "
                "to Noiser+."
            ),
        )
        story = TranscriptSegment(
            post_id=post.id,
            sequence_num=1,
            start_time=59.4,
            end_time=61.4,
            text="It is July the 1st, 1936.",
        )
        db.session.add_all([model_call, promo, story])
        db.session.commit()

        classifier = AdClassifier(config=test_config, db_session=db.session)
        created = classifier._apply_sponsor_cue_labels([promo, story], post)

        assert created == 1
        labeled = Identification.query.filter_by(label="ad").all()
        assert labeled[0].transcript_segment_id == promo.id


def test_label_repeated_creatives_marks_confirmed_midroll_copy(
    test_config: Config, app: Flask
) -> None:
    with app.app_context():
        post, segments = persist_salvador_dali_episode(
            db.session, guid="dali-classifier-guid"
        )
        classifier = AdClassifier(config=test_config, db_session=db.session)
        created = classifier._label_repeated_creatives(segments, post)

        assert created > 0
        labeled_texts = {
            ident.transcript_segment.text
            for ident in Identification.query.filter_by(label="ad").all()
        }
        assert (
            "When you think about Crocs, you think the classic clog." in labeled_texts
        )
        assert "Chevy is called the heartbeat of America for a reason." in labeled_texts
        assert "It is July the 1st, 1936." not in labeled_texts
        assert "That's next time." not in labeled_texts


def test_create_identifications_matches_interior_offset(
    test_classifier_with_mocks: AdClassifier,
) -> None:
    classifier = test_classifier_with_mocks
    classifier._segment_has_ad_identification = MagicMock(return_value=False)  # type: ignore[method-assign]
    segment = TranscriptSegment(
        id=7,
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
    prediction_list = AdSegmentPredictionList(
        ad_segments=[AdSegmentPrediction(segment_offset=170.2, confidence=0.95)]
    )
    model_call = ModelCall(
        id=3,
        post_id=1,
        model_name=classifier.config.llm_model,
        prompt="prompt",
        first_segment_sequence_num=0,
        last_segment_sequence_num=0,
    )

    with patch("podcast_processor.ad_classifier.writer_client") as writer:
        writer.action.return_value = MagicMock(success=True, data={"inserted": 1})
        created_count, matched = classifier._create_identifications(
            prediction_list=prediction_list,
            current_chunk_db_segments=[segment],
            model_call=model_call,
        )

    assert created_count == 1
    assert matched == [segment]
    payload = writer.action.call_args.args[1]["identifications"][0]
    assert payload["start_time"] == 158.5
    assert payload["end_time"] == pytest.approx(162.2)
