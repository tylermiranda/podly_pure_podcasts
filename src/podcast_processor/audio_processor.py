import logging
from typing import Any

from app.db_guard import refresh_read_snapshot
from app.extensions import db
from app.models import Identification, ModelCall, Post, TranscriptSegment
from app.writer.client import writer_client
from podcast_processor.ad_spans import AD_MERGE_PROXIMITY_SECONDS
from podcast_processor.audio import clip_segments_with_fade, get_audio_duration_ms
from shared.config import Config


class AudioProcessor:
    """Handles audio processing and ad segment removal from podcast files."""

    def __init__(
        self,
        config: Config,
        logger: logging.Logger | None = None,
        identification_query: Any | None = None,
        transcript_segment_query: Any | None = None,
        model_call_query: Any | None = None,
        db_session: Any | None = None,
    ):
        self.logger = logger or logging.getLogger("global_logger")
        self.config = config
        self._identification_query_provided = identification_query is not None
        self.identification_query = identification_query or Identification.query
        self.transcript_segment_query = (
            transcript_segment_query or TranscriptSegment.query
        )
        self.model_call_query = model_call_query or ModelCall.query
        self.db_session = db_session or db.session

    def get_ad_segments(self, post: Post) -> list[tuple[float, float]]:
        """Return expanded cut windows using the same path as Stats Cut pills."""
        from app.routes.post_stats_utils import (
            cut_eligible_identifications,
            final_cut_windows,
            parse_refined_windows,
        )

        self.logger.info(f"Retrieving ad segments from database for post {post.id}.")
        identifications, transcript_segments, model_calls = self._load_cut_inputs(post)
        if self._identification_query_provided:
            eligible = identifications
        else:
            eligible = cut_eligible_identifications(
                identifications,
                model_calls,
                min_confidence=float(self.config.output.min_confidence),
            )

        corrections = self._corrections_for_post(post)
        refined_windows = parse_refined_windows(
            getattr(post, "refined_ad_boundaries", None)
        )
        _labeled, effective = final_cut_windows(
            eligible,
            transcript_segments,
            refined_windows=refined_windows or None,
            corrections=corrections,
        )
        self.logger.info(
            "Resolved %s eligible ad labels into %s cut windows for post %s",
            len(eligible),
            len(effective),
            post.id,
        )
        return effective

    def _load_cut_inputs(self, post: Post) -> tuple[list[Any], list[Any], list[Any]]:
        if self._identification_query_provided:
            identifications = self._identifications_from_injected_query(post)
            fallback_segments = [
                ident.transcript_segment
                for ident in identifications
                if getattr(ident, "transcript_segment", None) is not None
            ]
            return identifications, fallback_segments, []

        refresh_read_snapshot(self.db_session, self.logger, "get_ad_segments")
        identifications = (
            self.db_session.query(Identification)
            .join(
                TranscriptSegment,
                Identification.transcript_segment_id == TranscriptSegment.id,
            )
            .filter(TranscriptSegment.post_id == post.id)
            .all()
        )
        transcript_segments = (
            self.db_session.query(TranscriptSegment)
            .filter(TranscriptSegment.post_id == post.id)
            .order_by(TranscriptSegment.sequence_num)
            .all()
        )
        model_calls = (
            self.db_session.query(ModelCall).filter(ModelCall.post_id == post.id).all()
        )
        return identifications, transcript_segments, model_calls

    def _identifications_from_injected_query(self, post: Post) -> list[Any]:
        return (
            self.identification_query.join(
                TranscriptSegment,
                Identification.transcript_segment_id == TranscriptSegment.id,
            )
            .join(ModelCall, Identification.model_call_id == ModelCall.id)
            .filter(
                TranscriptSegment.post_id == post.id,
                Identification.label == "ad",
                Identification.confidence >= self.config.output.min_confidence,
                ModelCall.status == "success",
            )
            .all()
        )

    def _corrections_for_post(self, post: Post) -> list[Any]:
        if self._identification_query_provided:
            return []
        try:
            from podcast_processor.ad_corrections import (
                load_active_corrections_for_post,
            )

            return load_active_corrections_for_post(post.id)
        except Exception:  # noqa: BLE001
            return []

    def merge_ad_segments(
        self,
        *,
        duration_ms: int,
        ad_segments: list[tuple[float, float]],
        min_ad_segment_length_seconds: float,
        min_ad_segment_separation_seconds: float,
    ) -> list[tuple[int, int]]:
        """
        Merges nearby ad segments and filters out segments that are too short.

        Args:
            duration_ms: Duration of the audio in milliseconds
            ad_segments: List of ad segments as (start, end) tuples in seconds
            min_ad_segment_length_seconds: Minimum length of an ad segment to retain
            min_ad_segment_separation_seconds: Minimum separation between segments before merging

        Returns:
            List of merged ad segments as (start, end) tuples in milliseconds
        """
        audio_duration_seconds = duration_ms / 1000.0

        self.logger.info(
            f"Creating new audio with ads segments removed between: {ad_segments}"
        )
        if not ad_segments:
            return []

        ad_segments = sorted(ad_segments)

        last_segment = self._get_last_segment_if_near_end(
            ad_segments,
            audio_duration_seconds=audio_duration_seconds,
            min_separation=min_ad_segment_separation_seconds,
        )

        glue_gap = min(min_ad_segment_separation_seconds, AD_MERGE_PROXIMITY_SECONDS)
        ad_segments = self._merge_close_segments(ad_segments, min_separation=glue_gap)
        ad_segments = self._filter_short_segments(
            ad_segments, min_length=min_ad_segment_length_seconds
        )
        ad_segments = self._restore_last_segment_if_needed(ad_segments, last_segment)
        ad_segments = self._extend_last_segment_to_end_if_needed(
            ad_segments,
            audio_duration_seconds=audio_duration_seconds,
            min_separation=min_ad_segment_separation_seconds,
        )

        self.logger.info(f"Joined ad segments into: {ad_segments}")
        return [(int(start * 1000), int(end * 1000)) for start, end in ad_segments]

    def _get_last_segment_if_near_end(
        self,
        ad_segments: list[tuple[float, float]],
        *,
        audio_duration_seconds: float,
        min_separation: float,
    ) -> tuple[float, float] | None:
        if not ad_segments:
            return None
        if (audio_duration_seconds - ad_segments[-1][1]) < min_separation:
            return ad_segments[-1]
        return None

    def _merge_close_segments(
        self,
        ad_segments: list[tuple[float, float]],
        *,
        min_separation: float,
    ) -> list[tuple[float, float]]:
        merged = list(ad_segments)
        i = 0
        while i < len(merged) - 1:
            if merged[i][1] + min_separation >= merged[i + 1][0]:
                merged[i] = (merged[i][0], merged[i + 1][1])
                merged.pop(i + 1)
            else:
                i += 1
        return merged

    def _filter_short_segments(
        self,
        ad_segments: list[tuple[float, float]],
        *,
        min_length: float,
    ) -> list[tuple[float, float]]:
        return [s for s in ad_segments if (s[1] - s[0]) >= min_length]

    def _restore_last_segment_if_needed(
        self,
        ad_segments: list[tuple[float, float]],
        last_segment: tuple[float, float] | None,
    ) -> list[tuple[float, float]]:
        if last_segment is None:
            return ad_segments
        if not ad_segments or ad_segments[-1] != last_segment:
            return [*ad_segments, last_segment]
        return ad_segments

    def _extend_last_segment_to_end_if_needed(
        self,
        ad_segments: list[tuple[float, float]],
        *,
        audio_duration_seconds: float,
        min_separation: float,
    ) -> list[tuple[float, float]]:
        if not ad_segments:
            return ad_segments
        if (audio_duration_seconds - ad_segments[-1][1]) < min_separation:
            return [*ad_segments[:-1], (ad_segments[-1][0], audio_duration_seconds)]
        return ad_segments

    def process_audio(self, post: Post, output_path: str) -> list[tuple[int, int]]:
        """
        Process the podcast audio by removing ad segments.

        Args:
            post: The Post object containing the podcast to process
            output_path: Path where the processed audio file should be saved
        Returns:
            The merged ad segments that were removed, as millisecond windows.
        """
        ad_segments = self.get_ad_segments(post)

        duration_ms = get_audio_duration_ms(post.unprocessed_audio_path)
        if duration_ms is None:
            raise ValueError(
                f"Could not determine duration for audio: {post.unprocessed_audio_path}"
            )

        merged_ad_segments = self.merge_ad_segments(
            duration_ms=duration_ms,
            ad_segments=ad_segments,
            min_ad_segment_length_seconds=float(
                self.config.output.min_ad_segment_length_seconds
            ),
            min_ad_segment_separation_seconds=float(
                self.config.output.min_ad_segement_separation_seconds
            ),
        )

        # LLM strategy doesn't use chapter markers, so VBR is fine for smaller files
        clip_segments_with_fade(
            in_path=post.unprocessed_audio_path,
            ad_segments_ms=merged_ad_segments,
            fade_ms=self.config.output.fade_ms,
            out_path=output_path,
            use_vbr=True,
        )

        processed_duration_ms = get_audio_duration_ms(output_path)
        if processed_duration_ms is None:
            self.logger.warning(
                "Could not determine processed audio duration for post %s at %s; "
                "falling back to source duration",
                post.id,
                output_path,
            )
            processed_duration_ms = duration_ms

        # Persist the final MP3 runtime so downstream RSS/stats reflect ad-removed
        # audio rather than the source episode length.
        post.duration = processed_duration_ms / 1000.0
        post.processed_audio_path = output_path
        result = writer_client.update(
            "Post",
            post.id,
            {"processed_audio_path": output_path, "duration": post.duration},
            wait=True,
        )
        if not result or not result.success:
            raise RuntimeError(getattr(result, "error", "Failed to update post"))
        try:
            self.db_session.expire(post)
        except Exception:  # noqa: BLE001
            pass

        self.logger.info(
            f"Audio processing complete for post {post.id}, saved to {output_path}"
        )
        return merged_ad_segments
