from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AdCorrection, Identification, ModelCall, Post, TranscriptSegment


def upsert_model_call_action(params: dict[str, Any]) -> dict[str, Any]:
    post_id = params.get("post_id")
    model_name = params.get("model_name")
    first_seq = params.get("first_segment_sequence_num")
    last_seq = params.get("last_segment_sequence_num")
    prompt = params.get("prompt")

    if post_id is None or model_name is None or first_seq is None or last_seq is None:
        raise ValueError(
            "post_id, model_name, first_segment_sequence_num, last_segment_sequence_num are required"
        )
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("prompt is required")

    def _query() -> ModelCall | None:
        return (
            db.session.query(ModelCall)
            .filter_by(
                post_id=int(post_id),
                model_name=str(model_name),
                first_segment_sequence_num=int(first_seq),
                last_segment_sequence_num=int(last_seq),
            )
            .order_by(ModelCall.timestamp.desc())
            .first()
        )

    model_call = _query()
    if model_call is None:
        model_call = ModelCall(
            post_id=int(post_id),
            first_segment_sequence_num=int(first_seq),
            last_segment_sequence_num=int(last_seq),
            model_name=str(model_name),
            prompt=str(prompt),
            status="pending",
            timestamp=datetime.now(UTC).replace(tzinfo=None),
            retry_attempts=0,
            error_message=None,
            response=None,
        )
        db.session.add(model_call)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            model_call = _query()
            if model_call is None:
                raise

    # Match prior behavior: reset only when pending/failed_retries.
    if model_call.status in ["pending", "failed_retries"]:
        model_call.status = "pending"
        model_call.prompt = str(prompt)
        model_call.retry_attempts = 0
        model_call.error_message = None
        model_call.response = None

    db.session.flush()
    return {"model_call_id": int(model_call.id)}


def upsert_whisper_model_call_action(params: dict[str, Any]) -> dict[str, Any]:
    post_id = params.get("post_id")
    model_name = params.get("model_name")
    first_seq = params.get("first_segment_sequence_num", 0)
    last_seq = params.get("last_segment_sequence_num", -1)
    prompt = params.get("prompt") or "Whisper transcription job"
    language = params.get("language")

    if post_id is None or model_name is None:
        raise ValueError("post_id and model_name are required")

    reset_fields: dict[str, Any] = params.get("reset_fields") or {
        "status": "pending",
        "prompt": "Whisper transcription job",
        "retry_attempts": 0,
        "error_message": None,
        "response": None,
    }

    def _query() -> ModelCall | None:
        # The partial unique index on (post, model_name, language) guarantees
        # at most one matching row. We match on those three columns (not the
        # seq nums) so that a previously-finalized row gets reused and reset
        # rather than colliding with a new placeholder.
        return (
            db.session.query(ModelCall)
            .filter_by(
                post_id=int(post_id),
                model_name=str(model_name),
                language=language,
            )
            .one_or_none()
        )

    model_call = _query()
    if model_call is None:
        model_call = ModelCall(
            post_id=int(post_id),
            model_name=str(model_name),
            first_segment_sequence_num=int(first_seq),
            last_segment_sequence_num=int(last_seq),
            prompt=str(prompt),
            language=language,
            status=str(reset_fields.get("status") or "pending"),
            retry_attempts=int(reset_fields.get("retry_attempts") or 0),
            error_message=reset_fields.get("error_message"),
            response=reset_fields.get("response"),
            timestamp=datetime.now(UTC).replace(tzinfo=None),
        )
        db.session.add(model_call)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            model_call = _query()
            if model_call is None:
                raise

    # Reset to placeholder seq nums (overwriting whatever a finalized row had);
    # reset_fields then resets status / retry_attempts / etc.
    model_call.first_segment_sequence_num = int(first_seq)
    model_call.last_segment_sequence_num = int(last_seq)
    for k, v in reset_fields.items():
        if hasattr(model_call, k):
            setattr(model_call, k, v)
    db.session.flush()

    return {"model_call_id": int(model_call.id)}


def _normalize_segments_payload(
    segments: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        normalized.append(
            {
                "post_id": int(seg["post_id"]),
                "sequence_num": int(seg["sequence_num"]),
                "start_time": float(seg["start_time"]),
                "end_time": float(seg["end_time"]),
                "text": str(seg["text"]),
            }
        )
    return normalized


def _words_column_value(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    payload: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        word = item.get("word", item.get("text"))
        start = item.get("start")
        end = item.get("end")
        if word is None or start is None or end is None:
            continue
        payload.append(
            {
                "word": str(word),
                "start": round(float(start), 3),
                "end": round(float(end), 3),
            }
        )
    return payload or None


def replace_transcription_action(params: dict[str, Any]) -> dict[str, Any]:
    post_id = params.get("post_id")
    segments = params.get("segments")
    model_call_id = params.get("model_call_id")

    if post_id is None:
        raise ValueError("post_id is required")
    if not isinstance(segments, list):
        raise ValueError("segments must be a list")

    post_id_i = int(post_id)

    seg_ids = [
        row[0]
        for row in db.session.query(TranscriptSegment.id)
        .filter(TranscriptSegment.post_id == post_id_i)
        .all()
    ]
    if seg_ids:
        db.session.query(Identification).filter(
            Identification.transcript_segment_id.in_(seg_ids)
        ).delete(synchronize_session=False)

    from podcast_processor.ad_corrections import mark_ad_corrections_stale_for_post

    mark_ad_corrections_stale_for_post(post_id_i)

    db.session.query(TranscriptSegment).filter(
        TranscriptSegment.post_id == post_id_i
    ).delete(synchronize_session=False)

    payload = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        payload.append(
            {
                "post_id": post_id_i,
                "sequence_num": int(seg.get("sequence_num", i)),
                "start_time": float(seg["start_time"]),
                "end_time": float(seg["end_time"]),
                "text": str(seg["text"]),
                "words": _words_column_value(seg.get("words")),
            }
        )

    if payload:
        db.session.execute(sqlite_insert(TranscriptSegment).values(payload))

    if model_call_id is not None:
        mc = db.session.get(ModelCall, int(model_call_id))
        if mc is not None:
            mc.first_segment_sequence_num = 0
            mc.last_segment_sequence_num = len(payload) - 1
            mc.response = f"{len(payload)} segments transcribed."
            mc.status = "success"
            mc.error_message = None

            # When this is a real language change — i.e. a prior whisper
            # ModelCall for another language exists — invalidate downstream
            # caches:
            #   1. Sibling whisper ModelCalls are marked `superseded` so the
            #      cache lookup doesn't return them pointing at someone
            #      else's segments.
            #   2. LLM ModelCalls (language IS NULL — ad classifier and
            #      similar) are deleted: their stored prompts were built
            #      from the prior transcript content and their responses
            #      classified ads against text that no longer exists.
            # Gating LLM-delete on "supersede actually fired" avoids nuking
            # ad-classifier caches on the first post-migration whisper run,
            # where the only prior whisper row is a legacy one with
            # language=NULL (already excluded from the supersede filter).
            if mc.language is not None:
                superseded = (
                    db.session.query(ModelCall)
                    .filter(
                        ModelCall.post_id == post_id_i,
                        ModelCall.id != int(model_call_id),
                        ModelCall.status == "success",
                        ModelCall.language.isnot(None),
                    )
                    .update({ModelCall.status: "superseded"}, synchronize_session=False)
                )
                if superseded:
                    db.session.query(ModelCall).filter(
                        ModelCall.post_id == post_id_i,
                        ModelCall.language.is_(None),
                    ).delete(synchronize_session=False)

    db.session.flush()
    return {"post_id": post_id_i, "segment_count": len(payload)}


def mark_model_call_failed_action(params: dict[str, Any]) -> dict[str, Any]:
    model_call_id = params.get("model_call_id")
    error_message = params.get("error_message")
    status = params.get("status", "failed_permanent")

    if model_call_id is None:
        raise ValueError("model_call_id is required")

    mc = db.session.get(ModelCall, int(model_call_id))
    if mc is None:
        return {"updated": False}

    mc.status = str(status)
    mc.error_message = str(error_message) if error_message is not None else None
    db.session.flush()
    return {"updated": True, "model_call_id": int(mc.id)}


def insert_identifications_action(params: dict[str, Any]) -> dict[str, Any]:
    identifications = params.get("identifications")
    if not isinstance(identifications, list):
        raise ValueError("identifications must be a list")

    values = []
    for ident in identifications:
        if not isinstance(ident, dict):
            continue
        values.append(
            {
                "transcript_segment_id": int(ident["transcript_segment_id"]),
                "model_call_id": int(ident["model_call_id"]),
                "label": str(ident.get("label") or "ad"),
                "confidence": ident.get("confidence"),
                "start_time": (
                    float(ident["start_time"])
                    if ident.get("start_time") is not None
                    else None
                ),
                "end_time": (
                    float(ident["end_time"])
                    if ident.get("end_time") is not None
                    else None
                ),
            }
        )

    if not values:
        return {"inserted": 0}

    stmt = sqlite_insert(Identification).values(values).prefix_with("OR IGNORE")
    result = db.session.execute(stmt)
    db.session.flush()
    return {"inserted": int(getattr(result, "rowcount", 0) or 0)}


def replace_identifications_action(params: dict[str, Any]) -> dict[str, Any]:
    delete_ids = params.get("delete_ids") or []
    new_identifications = params.get("new_identifications") or []

    if not isinstance(delete_ids, list) or not isinstance(new_identifications, list):
        raise ValueError("delete_ids and new_identifications must be lists")

    if delete_ids:
        db.session.query(Identification).filter(
            Identification.id.in_([int(i) for i in delete_ids])
        ).delete(synchronize_session=False)

    inserted = insert_identifications_action(
        {"identifications": new_identifications}
    ).get("inserted", 0)

    db.session.flush()
    return {"deleted": len(delete_ids), "inserted": int(inserted)}


def insert_ad_correction_action(params: dict[str, Any]) -> dict[str, Any]:
    from podcast_processor.ad_corrections import (
        CORRECTION_KINDS,
        CORRECTION_LABELS,
        current_transcript_model_call_id,
        default_kind_for_label,
        example_text_for_range,
        snap_range_to_words,
    )

    post_id = params.get("post_id")
    if post_id is None:
        raise ValueError("post_id is required")
    post = db.session.get(Post, int(post_id))
    if post is None:
        raise ValueError(f"Post {post_id} not found")

    label = str(params.get("label") or "").strip().lower()
    if label not in CORRECTION_LABELS:
        raise ValueError("label must be 'ad' or 'content'")

    kind = str(params.get("kind") or default_kind_for_label(label)).strip().lower()
    if kind not in CORRECTION_KINDS:
        raise ValueError("kind must be missed_ad, false_positive, or retime")

    try:
        start_time = float(params["start_time"])
        end_time = float(params["end_time"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("start_time and end_time are required") from exc
    if end_time <= start_time:
        raise ValueError("end_time must be greater than start_time")

    segments = (
        db.session.query(TranscriptSegment)
        .filter(TranscriptSegment.post_id == post.id)
        .order_by(TranscriptSegment.sequence_num)
        .all()
    )
    start_time, end_time = snap_range_to_words(start_time, end_time, segments)

    segment_ids = params.get("segment_ids")
    if segment_ids is not None and not isinstance(segment_ids, list):
        raise ValueError("segment_ids must be a list")
    source_identification_ids = params.get("source_identification_ids")
    if source_identification_ids is not None and not isinstance(
        source_identification_ids, list
    ):
        raise ValueError("source_identification_ids must be a list")

    supersedes_id = params.get("supersedes_id")
    if supersedes_id is not None:
        superseded = db.session.get(AdCorrection, int(supersedes_id))
        if superseded is None or superseded.post_id != post.id:
            raise ValueError("supersedes_id does not match this post")

    reason = params.get("reason")
    if reason is not None:
        reason = str(reason).strip() or None

    created_by_user_id = params.get("created_by_user_id")
    transcript_model_call_id = params.get("transcript_model_call_id")
    if transcript_model_call_id is None:
        transcript_model_call_id = current_transcript_model_call_id(post.id)

    correction = AdCorrection(
        post_id=post.id,
        feed_id=post.feed_id,
        created_by_user_id=(
            int(created_by_user_id) if created_by_user_id is not None else None
        ),
        kind=kind,
        label=label,
        start_time=start_time,
        end_time=end_time,
        segment_ids=segment_ids,
        source_identification_ids=source_identification_ids,
        reason=reason,
        example_text=example_text_for_range(start_time, end_time, segments),
        transcript_model_call_id=(
            int(transcript_model_call_id)
            if transcript_model_call_id is not None
            else None
        ),
        stale=False,
        supersedes_id=int(supersedes_id) if supersedes_id is not None else None,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.session.add(correction)
    db.session.flush()

    if label == "ad":
        try:
            from podcast_processor.ad_creatives import upsert_creatives_for_feed

            sample = (correction.example_text or "").strip()
            if sample:
                feed = getattr(post, "feed", None)
                upsert_creatives_for_feed(
                    feed_id=int(post.feed_id),
                    texts=[sample],
                    source_post_id=post.id,
                    prompt_tag_id=(
                        getattr(feed, "prompt_tag_id", None)
                        if feed is not None
                        else None
                    ),
                    commit=False,
                )
        except Exception:  # noqa: BLE001
            # Creative index must not fail correction saves.
            pass

    return {
        "id": int(correction.id),
        "post_id": post.id,
        "start_time": float(correction.start_time),
        "end_time": float(correction.end_time),
    }


def apply_ad_corrections_action(params: dict[str, Any]) -> dict[str, Any]:
    """Validate the post for recut. Audio recut runs in the Flask request, not here."""
    post_id = params.get("post_id")
    if post_id is None:
        raise ValueError("post_id is required")
    post = db.session.get(Post, int(post_id))
    if post is None:
        raise ValueError(f"Post {post_id} not found")
    return {"post_id": post.id, "recut": False}


def mark_ad_corrections_stale_action(params: dict[str, Any]) -> dict[str, Any]:
    from podcast_processor.ad_corrections import mark_ad_corrections_stale_for_post

    post_id = params.get("post_id")
    if post_id is None:
        raise ValueError("post_id is required")
    updated = mark_ad_corrections_stale_for_post(int(post_id))
    return {"post_id": int(post_id), "updated": updated}


def upsert_jingle_template_action(params: dict[str, Any]) -> dict[str, Any]:
    """Extract audio slice fingerprint and upsert as kind=jingle for a feed."""
    from app.config_store import to_pydantic_config
    from podcast_processor.ad_audio_fingerprint import (
        ffmpeg_available,
        fingerprint_window,
        fpcalc_available,
        upsert_fingerprint,
    )

    feed_id = params.get("feed_id")
    post_id = params.get("post_id")
    start_time = params.get("start_time")
    end_time = params.get("end_time")
    if feed_id is None or post_id is None:
        raise ValueError("feed_id and post_id are required")
    if start_time is None or end_time is None:
        raise ValueError("start_time and end_time are required")

    start = float(start_time)
    end = float(end_time)
    if end <= start:
        raise ValueError("end_time must be greater than start_time")

    config = to_pydantic_config()
    duration = end - start
    min_s = float(getattr(config, "jingle_min_seconds", 1.0) or 1.0)
    max_s = float(getattr(config, "jingle_max_seconds", 15.0) or 15.0)
    if duration < min_s or duration > max_s:
        raise ValueError(
            f"jingle duration must be between {min_s}s and {max_s}s (got {duration:.1f}s)"
        )

    post = db.session.get(Post, int(post_id))
    if post is None:
        raise ValueError(f"Post {post_id} not found")
    if int(post.feed_id) != int(feed_id):
        raise ValueError("post does not belong to feed")

    audio_path = post.unprocessed_audio_path
    if not audio_path or not os.path.isfile(audio_path):
        raise ValueError(
            "Original unprocessed audio is required for jingle templates. "
            "Reprocess the episode if the source file was removed after processing."
        )

    if not fpcalc_available():
        raise ValueError(
            "fpcalc is not installed (Docker: libchromaprint-tools; Mac: brew install chromaprint)"
        )
    if not ffmpeg_available():
        raise ValueError("ffmpeg is not installed")

    fingerprint = fingerprint_window(str(audio_path), start, end)
    if not fingerprint:
        raise ValueError(
            "Could not fingerprint the selected audio range. "
            "Use a 1–15 second selection from the episode timeline."
        )

    feed = getattr(post, "feed", None)
    upsert_fingerprint(
        feed_id=int(feed_id),
        fingerprint=fingerprint,
        duration_seconds=duration,
        kind="jingle",
        source_post_id=post.id,
        source_start=start,
        source_end=end,
        prompt_tag_id=getattr(feed, "prompt_tag_id", None) if feed else None,
        commit=True,
    )
    return {
        "feed_id": int(feed_id),
        "post_id": post.id,
        "start_time": start,
        "end_time": end,
        "kind": "jingle",
    }
