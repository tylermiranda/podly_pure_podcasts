import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from openai.types.audio.transcription_segment import TranscriptionSegment

# from pytest_mock import MockerFixture


@pytest.mark.skip
def test_remote_transcribe() -> None:
    # import here instead of the toplevel because torch is not installed properly in CI.
    from podcast_processor.transcribe import (
        OpenAIWhisperTranscriber,
    )

    logger = logging.getLogger("global_logger")
    from shared.test_utils import create_standard_test_config

    config = create_standard_test_config().model_dump()

    transcriber = OpenAIWhisperTranscriber(logger, config)

    transcription = transcriber.transcribe("file.mp3", language="en")
    assert transcription == []


@pytest.mark.skip
def test_local_transcribe() -> None:
    # import here instead of the toplevel because torch is not installed properly in CI.
    from podcast_processor.transcribe import (
        LocalWhisperTranscriber,
    )

    logger = logging.getLogger("global_logger")
    transcriber = LocalWhisperTranscriber(logger, "base.en")
    transcription = transcriber.transcribe("src/tests/file.mp3", language="en")
    assert transcription == []


@pytest.mark.skip
def test_groq_transcribe(mocker: Any) -> None:
    # import here instead of the toplevel because dependencies aren't installed properly in CI.
    from podcast_processor.transcribe import (
        GroqWhisperTranscriber,
    )
    from shared.config import (
        GroqWhisperConfig,
    )

    # Mock the requests call
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "This is a test segment."},
            {"start": 1.0, "end": 2.0, "text": "This is another test segment."},
        ]
    }
    mocker.patch("requests.post", return_value=mock_response)

    # Mock file operations
    mocker.patch("builtins.open", mocker.mock_open(read_data="test audio data"))
    mocker.patch("pathlib.Path.exists", return_value=True)
    mocker.patch("podcast_processor.audio.split_audio", return_value=[("test.mp3", 0)])
    mocker.patch("shutil.rmtree")

    logger = logging.getLogger("global_logger")
    config = GroqWhisperConfig(
        api_key="test_key", model="whisper-large-v3-turbo", language="en"
    )

    transcriber = GroqWhisperTranscriber(logger, config)
    transcription = transcriber.transcribe("test.mp3", language="en")

    assert len(transcription) == 2
    assert transcription[0].text == "This is a test segment."
    assert transcription[1].text == "This is another test segment."


def test_offset() -> None:
    from podcast_processor.transcribe import (
        OpenAIWhisperTranscriber,
        Segment,
        WordTimestamp,
    )

    offset = OpenAIWhisperTranscriber.add_offset_to_segments(
        [
            Segment(
                start=12.345,
                end=45.678,
                text="hi",
                words=[WordTimestamp(word="hi", start=12.345, end=12.5)],
            )
        ],
        123,
    )
    assert offset[0].start == pytest.approx(12.468)
    assert offset[0].end == pytest.approx(45.801)
    assert offset[0].words is not None
    assert offset[0].words[0].start == pytest.approx(12.468)
    assert offset[0].words[0].end == pytest.approx(12.623)


def test_convert_segments_without_words() -> None:
    from podcast_processor.transcribe import OpenAIWhisperTranscriber

    converted = OpenAIWhisperTranscriber.convert_segments(
        [
            TranscriptionSegment(
                id=1,
                avg_logprob=2,
                seek=6,
                temperature=7,
                text="hi",
                tokens=[],
                compression_ratio=3,
                no_speech_prob=4,
                start=12.345,
                end=45.678,
            )
        ]
    )
    assert len(converted) == 1
    assert converted[0].text == "hi"
    assert converted[0].start == 12.345
    assert converted[0].words is None


def test_bucket_words_into_segments() -> None:
    from podcast_processor.transcribe import (
        Segment,
        WordTimestamp,
        bucket_words_into_segments,
    )

    segments = [
        Segment(start=0.0, end=10.0, text="hello there"),
        Segment(start=10.0, end=20.0, text="welcome back"),
    ]
    words = [
        WordTimestamp(word="hello", start=0.1, end=0.4),
        WordTimestamp(word=" there", start=0.4, end=0.8),
        WordTimestamp(word="welcome", start=10.0, end=10.4),
        WordTimestamp(word=" back", start=20.0, end=20.2),
    ]
    filled = bucket_words_into_segments(segments, words)
    assert [w.word for w in filled[0].words or []] == ["hello", " there"]
    assert [w.word for w in filled[1].words or []] == ["welcome", " back"]


def test_nested_segment_words_not_overwritten_by_top_level() -> None:
    from podcast_processor.transcribe import (
        WordTimestamp,
        segments_from_whisper_response,
    )

    class RawSeg:
        start = 0.0
        end = 1.0
        text = "hi"
        words = [{"word": "hi", "start": 0.0, "end": 1.0}]

    filled = segments_from_whisper_response(
        [RawSeg()],
        [WordTimestamp(word="other", start=0.5, end=0.6)],
    )
    assert filled[0].words is not None
    assert filled[0].words[0].word == "hi"


def test_extract_transcription_words_from_model_dump() -> None:
    from podcast_processor.transcribe import extract_transcription_words

    class DumpOnly:
        words = None

        def model_dump(self) -> dict[str, Any]:
            return {"words": [{"word": "hi", "start": 0.1, "end": 0.2}]}

    parsed = extract_transcription_words(DumpOnly())
    assert len(parsed) == 1
    assert parsed[0].word == "hi"
    assert parsed[0].start == 0.1


def test_get_segments_retries_without_word_granularity(tmp_path: Any) -> None:
    from podcast_processor.transcribe import OpenAIWhisperTranscriber
    from shared.config import RemoteWhisperConfig

    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake-audio")

    transcriber = OpenAIWhisperTranscriber(
        logging.getLogger("test"), RemoteWhisperConfig(api_key="test")
    )
    transcription = MagicMock()
    transcription.segments = [
        TranscriptionSegment(
            id=1,
            avg_logprob=0,
            seek=0,
            temperature=0,
            text="hi",
            tokens=[],
            compression_ratio=0,
            no_speech_prob=0,
            start=0.0,
            end=1.0,
        )
    ]
    transcription.words = None
    transcription.model_extra = None
    transcription.model_dump.return_value = {}

    calls: list[list[str]] = []

    def fake_create(**kwargs: Any) -> Any:
        granularities = kwargs["timestamp_granularities"]
        calls.append(list(granularities))
        if granularities == ["word", "segment"]:
            raise ValueError("unsupported timestamp_granularities: word")
        return transcription

    with patch.object(
        transcriber.openai_client.audio.transcriptions, "create", fake_create
    ):
        result = transcriber.get_segments_for_chunk(str(chunk), "en")
    assert calls == [["word", "segment"], ["segment"]]
    assert result[0].text == "hi"
    assert result[0].words is None
