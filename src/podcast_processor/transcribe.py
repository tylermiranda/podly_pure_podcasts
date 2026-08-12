import logging
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from groq import Groq
from openai import OpenAI
from openai.types.audio.transcription_segment import TranscriptionSegment
from pydantic import BaseModel

from podcast_processor.audio import split_audio
from shared.config import GroqWhisperConfig, RemoteWhisperConfig

try:
    from openai import BadRequestError as OpenAIBadRequestError
except ImportError:  # pragma: no cover
    OpenAIBadRequestError = ()  # type: ignore[misc, assignment]


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[WordTimestamp] | None = None


def parse_word_timestamps(raw: Any) -> list[WordTimestamp]:
    """Normalize mlx/OpenAI word lists into WordTimestamp objects."""
    if not raw:
        return []
    parsed: list[WordTimestamp] = []
    for item in raw:
        if isinstance(item, dict):
            token = item.get("word", item.get("text"))
            start = item.get("start")
            end = item.get("end")
        else:
            token = getattr(item, "word", None) or getattr(item, "text", None)
            start = getattr(item, "start", None)
            end = getattr(item, "end", None)
        if token is None or start is None or end is None:
            continue
        parsed.append(
            WordTimestamp(word=str(token), start=float(start), end=float(end))
        )
    return parsed


def extract_transcription_words(transcription: Any) -> list[WordTimestamp]:
    raw = getattr(transcription, "words", None)
    if not raw:
        extra = getattr(transcription, "model_extra", None) or {}
        if isinstance(extra, dict):
            raw = extra.get("words")
    if not raw and hasattr(transcription, "model_dump"):
        dumped = transcription.model_dump()
        if isinstance(dumped, dict):
            raw = dumped.get("words")
    return parse_word_timestamps(raw)


def extract_nested_segment_words(segment: Any) -> list[WordTimestamp]:
    raw = getattr(segment, "words", None)
    if not raw:
        extra = getattr(segment, "model_extra", None) or {}
        if isinstance(extra, dict):
            raw = extra.get("words")
    if not raw and hasattr(segment, "model_dump"):
        dumped = segment.model_dump()
        if isinstance(dumped, dict):
            raw = dumped.get("words")
    return parse_word_timestamps(raw)


def bucket_words_into_segments(
    segments: list[Segment], words: list[WordTimestamp]
) -> list[Segment]:
    """Assign top-level words to segments by word.start.

    Interval is [start, end) except the last segment, which is inclusive of end.
    Segments that already have nested words keep them.
    """
    if not segments or not words:
        return segments

    buckets: list[list[WordTimestamp]] = [[] for _ in segments]
    last_idx = len(segments) - 1
    for word in words:
        for i, seg in enumerate(segments):
            if i == last_idx:
                in_range = seg.start <= word.start <= seg.end
            else:
                in_range = seg.start <= word.start < seg.end
            if in_range:
                buckets[i].append(word)
                break

    filled: list[Segment] = []
    for seg, bucket in zip(segments, buckets, strict=True):
        if seg.words:
            filled.append(seg)
            continue
        filled.append(seg.model_copy(update={"words": bucket or None}))
    return filled


def segments_from_whisper_response(
    raw_segments: list[Any], top_level_words: list[WordTimestamp] | None = None
) -> list[Segment]:
    segments = [
        Segment(
            start=float(raw.start),
            end=float(raw.end),
            text=raw.text,
            words=extract_nested_segment_words(raw) or None,
        )
        for raw in raw_segments
    ]
    return bucket_words_into_segments(segments, top_level_words or [])


def offset_segments(segments: list[Segment], offset_ms: int) -> list[Segment]:
    offset_sec = float(offset_ms) / 1000.0
    if offset_sec == 0:
        return segments
    for segment in segments:
        segment.start += offset_sec
        segment.end += offset_sec
        if segment.words:
            for word in segment.words:
                word.start += offset_sec
                word.end += offset_sec
    return segments


def words_to_payload(words: list[WordTimestamp] | None) -> list[dict[str, Any]] | None:
    if not words:
        return None
    return [
        {"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
        for w in words
    ]


def _is_word_granularity_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "timestamp_granularit" in msg or "granularity" in msg:
        return True
    if OpenAIBadRequestError and isinstance(exc, OpenAIBadRequestError):
        return True
    return False


class Transcriber(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @abstractmethod
    def transcribe(self, audio_file_path: str, language: str) -> list[Segment]:
        pass


class LocalTranscriptSegment(BaseModel):
    id: int
    seek: int
    start: float
    end: float
    text: str
    tokens: list[int]
    temperature: float
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float

    def to_segment(self) -> Segment:
        return Segment(start=self.start, end=self.end, text=self.text)


class TestWhisperTranscriber(Transcriber):
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    @property
    def model_name(self) -> str:
        return "test_whisper"

    def transcribe(self, audio_file_path: str, language: str) -> list[Segment]:
        del audio_file_path, language
        self.logger.info("Using test whisper")
        return [
            Segment(start=0, end=1, text="This is a test"),
            Segment(start=1, end=2, text="This is another test"),
        ]


class LocalWhisperTranscriber(Transcriber):
    def __init__(self, logger: logging.Logger, whisper_model: str):
        self.logger = logger
        self.whisper_model = whisper_model

    @property
    def model_name(self) -> str:
        return f"local_{self.whisper_model}"

    @staticmethod
    def convert_to_pydantic(
        transcript_data: list[Any],
    ) -> list[LocalTranscriptSegment]:
        return [LocalTranscriptSegment(**item) for item in transcript_data]

    @staticmethod
    def local_seg_to_seg(local_segments: list[LocalTranscriptSegment]) -> list[Segment]:
        return [seg.to_segment() for seg in local_segments]

    def transcribe(self, audio_file_path: str, language: str) -> list[Segment]:
        # Import whisper only when needed to avoid CUDA dependencies during module import
        try:
            import whisper
        except ImportError as e:
            self.logger.error(f"Failed to import whisper: {e}")
            raise ImportError(
                "whisper library is required for LocalWhisperTranscriber"
            ) from e

        self.logger.info("Using local whisper")
        models = whisper.available_models()
        self.logger.info(f"Available models: {models}")

        model = whisper.load_model(name=self.whisper_model)

        self.logger.info("Beginning transcription")
        start = time.time()
        result = model.transcribe(audio_file_path, fp16=False, language=language)
        end = time.time()
        elapsed = end - start
        self.logger.info(f"Transcription completed in {elapsed}")
        segments = result["segments"]
        typed_segments = self.convert_to_pydantic(segments)

        return self.local_seg_to_seg(typed_segments)


class OpenAIWhisperTranscriber(Transcriber):
    def __init__(self, logger: logging.Logger, config: RemoteWhisperConfig):
        self.logger = logger
        self.config = config

        self.openai_client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=config.timeout_sec,
        )

    @property
    def model_name(self) -> str:
        return self.config.model  # e.g. "whisper-1"

    def transcribe(self, audio_file_path: str, language: str) -> list[Segment]:
        self.logger.info(
            "[WHISPER_REMOTE] Starting remote whisper transcription for: %s",
            audio_file_path,
        )
        audio_chunk_path = audio_file_path + "_parts"

        chunks = split_audio(
            Path(audio_file_path),
            Path(audio_chunk_path),
            self.config.chunksize_mb * 1024 * 1024,
        )

        self.logger.info("[WHISPER_REMOTE] Processing %d chunks", len(chunks))
        all_segments: list[Segment] = []

        for idx, chunk in enumerate(chunks):
            chunk_path, offset = chunk
            self.logger.info(
                "[WHISPER_REMOTE] Processing chunk %d/%d: %s",
                idx + 1,
                len(chunks),
                chunk_path,
            )
            segments = self.get_segments_for_chunk(str(chunk_path), language=language)
            self.logger.info(
                "[WHISPER_REMOTE] Chunk %d/%d complete: %d segments",
                idx + 1,
                len(chunks),
                len(segments),
            )
            all_segments.extend(self.add_offset_to_segments(segments, offset))

        shutil.rmtree(audio_chunk_path)
        self.logger.info(
            "[WHISPER_REMOTE] Transcription complete: %d total segments",
            len(all_segments),
        )
        return all_segments

    @staticmethod
    def convert_segments(segments: list[TranscriptionSegment]) -> list[Segment]:
        return segments_from_whisper_response(segments)

    @staticmethod
    def add_offset_to_segments(
        segments: list[Segment], offset_ms: int
    ) -> list[Segment]:
        return offset_segments(segments, offset_ms)

    def _call_whisper(
        self,
        chunk_path: str,
        language: str,
        granularities: list[Literal["word", "segment"]],
    ) -> Any:
        with open(chunk_path, "rb") as f:
            self.logger.info(
                "[WHISPER_API_CALL] Sending chunk to API: %s (timeout=%ds, granularities=%s)",
                chunk_path,
                self.config.timeout_sec,
                granularities,
            )
            return self.openai_client.audio.transcriptions.create(
                model=self.config.model,
                file=f,
                timestamp_granularities=granularities,
                language=language,
                response_format="verbose_json",
            )

    def get_segments_for_chunk(self, chunk_path: str, language: str) -> list[Segment]:
        try:
            transcription = self._call_whisper(
                chunk_path, language, ["word", "segment"]
            )
        except Exception as exc:
            if not _is_word_granularity_error(exc):
                raise
            self.logger.warning(
                "[WHISPER_API_CALL] Word timestamps rejected (%s); retrying with segment-only",
                exc,
            )
            transcription = self._call_whisper(chunk_path, language, ["segment"])

        self.logger.debug("Got transcription")
        raw_segments = transcription.segments
        assert raw_segments is not None
        self.logger.debug(f"Got {len(raw_segments)} segments")
        return segments_from_whisper_response(
            raw_segments, extract_transcription_words(transcription)
        )


class GroqTranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str


class GroqWhisperTranscriber(Transcriber):
    def __init__(self, logger: logging.Logger, config: GroqWhisperConfig):
        self.logger = logger
        self.config = config
        self.client = Groq(
            api_key=config.api_key,
            max_retries=config.max_retries,
        )

    @property
    def model_name(self) -> str:
        return f"groq_{self.config.model}"

    def transcribe(self, audio_file_path: str, language: str) -> list[Segment]:
        self.logger.info(
            "[WHISPER_GROQ] Starting Groq whisper transcription for: %s",
            audio_file_path,
        )
        audio_chunk_path = audio_file_path + "_parts"

        # 12MB seems to cause instability in Groq
        chunks = split_audio(
            Path(audio_file_path), Path(audio_chunk_path), 6 * 1024 * 1024
        )

        self.logger.info("[WHISPER_GROQ] Processing %d chunks", len(chunks))
        all_segments: list[GroqTranscriptionSegment] = []

        for idx, chunk in enumerate(chunks):
            chunk_path, offset = chunk
            self.logger.info(
                "[WHISPER_GROQ] Processing chunk %d/%d: %s",
                idx + 1,
                len(chunks),
                chunk_path,
            )
            segments = self.get_segments_for_chunk(str(chunk_path), language=language)
            self.logger.info(
                "[WHISPER_GROQ] Chunk %d/%d complete: %d segments",
                idx + 1,
                len(chunks),
                len(segments),
            )
            all_segments.extend(self.add_offset_to_segments(segments, offset))

        shutil.rmtree(audio_chunk_path)
        self.logger.info(
            "[WHISPER_GROQ] Transcription complete: %d total segments",
            len(all_segments),
        )
        return self.convert_segments(all_segments)

    @staticmethod
    def convert_segments(segments: list[GroqTranscriptionSegment]) -> list[Segment]:
        return [
            Segment(
                start=seg.start,
                end=seg.end,
                text=seg.text,
            )
            for seg in segments
        ]

    @staticmethod
    def add_offset_to_segments(
        segments: list[GroqTranscriptionSegment], offset_ms: int
    ) -> list[GroqTranscriptionSegment]:
        offset_sec = float(offset_ms) / 1000.0
        for segment in segments:
            segment.start += offset_sec
            segment.end += offset_sec

        return segments

    def get_segments_for_chunk(
        self, chunk_path: str, language: str
    ) -> list[GroqTranscriptionSegment]:
        retries = self.config.max_retries if self.config.max_retries is not None else 0
        max_attempts = retries + 1
        for attempt in range(1, max_attempts + 1):
            self.logger.info(
                "[GROQ_API_CALL] Sending chunk to Groq API: %s (attempt %d/%d)",
                chunk_path,
                attempt,
                max_attempts,
            )
            try:
                transcription = self.client.audio.transcriptions.create(
                    file=Path(chunk_path),
                    model=self.config.model,
                    response_format="verbose_json",  # Ensure segments are included
                    language=language,
                )
            except Exception as exc:
                self.logger.warning(
                    "[GROQ_API_CALL] Attempt %d/%d failed for %s: %s",
                    attempt,
                    max_attempts,
                    chunk_path,
                    exc,
                )
                if attempt == max_attempts:
                    raise
                time.sleep(1.5**attempt)
                continue

            self.logger.info(
                "[GROQ_API_CALL] Received response from Groq API for: %s (attempt %d/%d)",
                chunk_path,
                attempt,
                max_attempts,
            )

            if transcription.segments is None:  # type: ignore [attr-defined]
                self.logger.warning(
                    "[GROQ_API_CALL] No segments found in transcription for %s",
                    chunk_path,
                )
                return []

            groq_segments = [
                GroqTranscriptionSegment(
                    start=seg["start"], end=seg["end"], text=seg["text"]
                )
                for seg in transcription.segments  # type: ignore [attr-defined]
            ]

            self.logger.info(
                "[GROQ_API_CALL] Got %d segments from chunk (attempt %d/%d)",
                len(groq_segments),
                attempt,
                max_attempts,
            )
            return groq_segments

        # unreachable, but satisfies type checker
        return []
