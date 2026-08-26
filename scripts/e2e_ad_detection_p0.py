#!/usr/bin/env python3
"""Local end-to-end smoke for ad-detection P0 (eval / verify / creatives / cut).

Does not require live LLM keys: litellm is mocked for the verify pass.
Uses real SQLAlchemy models, writer local fallback, and ffmpeg.

Run from repo root:

  PODLY_WRITER_LOCAL_FALLBACK=1 uv run python scripts/e2e_ad_detection_p0.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

SAMPLE_MP3 = ROOT / "src" / "tests" / "data" / "count_0_99.mp3"


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def main() -> None:
    if not SAMPLE_MP3.is_file():
        _fail(f"missing sample audio: {SAMPLE_MP3}")

    os.environ["PODLY_WRITER_LOCAL_FALLBACK"] = "1"
    os.environ["PYTEST_CURRENT_TEST"] = "e2e_ad_detection_p0"
    os.environ["DEVELOPER_MODE"] = "true"
    os.environ["PODLY_DISABLE_SCHEDULER"] = "1"
    os.environ.setdefault("PYTHONPATH", str(SRC))

    tmp = Path(tempfile.mkdtemp(prefix="podly-e2e-p0-"))
    data_dir = tmp / "data"
    (data_dir / "in").mkdir(parents=True)
    (data_dir / "srv").mkdir(parents=True)
    os.environ["PODLY_INSTANCE_DIR"] = str(tmp)
    os.environ["PODLY_PODCAST_DATA_DIR"] = str(data_dir)

    from flask import Flask

    from app.extensions import db
    from app.models import (
        AdCreative,
        Feed,
        Identification,
        ModelCall,
        Post,
        TranscriptSegment,
    )
    from app.routes.post_stats_utils import (
        cut_eligible_identifications,
        final_cut_windows,
        labeled_cut_windows,
        merge_time_windows,
        parse_refined_windows,
    )
    from app.writer.client import writer_client
    from podcast_processor.ad_creatives import (
        load_feed_creatives,
        match_segment_to_creatives,
        upsert_from_post_cut_windows,
    )
    from podcast_processor.ad_eval import score_windows_detailed
    from podcast_processor.ad_verifier import AdVerifier
    from podcast_processor.audio import get_audio_duration_ms
    from podcast_processor.audio_processor import AudioProcessor
    from shared.test_utils import create_standard_test_config
    from tests.salvador_dali_fixture import (
        SALVADOR_DALI_GOLD_WINDOWS,
        labeled_ad_windows,
        salvador_dali_segments,
    )

    app = Flask("podly-e2e-p0")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp / 'e2e.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.init_app(app)
        db.create_all()

        feed = Feed(
            title="E2E P0 Feed",
            description="local e2e",
            rss_url="https://example.com/e2e-p0.xml",
        )
        db.session.add(feed)
        db.session.commit()

        unprocessed = data_dir / "in" / "e2e-source.mp3"
        shutil.copyfile(SAMPLE_MP3, unprocessed)
        post = Post(
            feed_id=feed.id,
            guid="e2e-ad-detection-p0",
            download_url="https://example.com/e2e-p0.mp3",
            title="E2E Ad Detection P0",
            whitelisted=True,
            unprocessed_audio_path=str(unprocessed),
        )
        db.session.add(post)
        db.session.commit()

        segs = salvador_dali_segments()
        db_segs: list[TranscriptSegment] = []
        for s in segs:
            row = TranscriptSegment(
                post_id=post.id,
                sequence_num=int(s.sequence_num),
                start_time=float(s.start_time),
                end_time=float(s.end_time),
                text=str(s.text),
            )
            db.session.add(row)
            db_segs.append(row)
        db.session.commit()

        model_call = ModelCall(
            post_id=post.id,
            model_name="e2e-mock",
            status="success",
            prompt="e2e",
            first_segment_sequence_num=0,
            last_segment_sequence_num=len(db_segs) - 1,
        )
        db.session.add(model_call)
        db.session.commit()

        for s, row in zip(segs, db_segs, strict=True):
            if not getattr(s, "labeled_ad", False):
                continue
            db.session.add(
                Identification(
                    transcript_segment_id=row.id,
                    model_call_id=model_call.id,
                    label="ad",
                    confidence=0.92,
                    start_time=float(row.start_time),
                    end_time=float(row.end_time),
                )
            )
        db.session.commit()

        idents = (
            Identification.query.join(TranscriptSegment)
            .filter(TranscriptSegment.post_id == post.id)
            .all()
        )
        for ident in idents:
            _ = ident.transcript_segment

        eligible = cut_eligible_identifications(
            idents, [model_call], min_confidence=0.7
        )
        draft_windows = merge_time_windows(
            labeled_cut_windows(eligible), gap_seconds=1.0
        )
        if not draft_windows:
            draft_windows = labeled_ad_windows(segs)
        _ok(f"draft labeled windows={len(draft_windows)}")

        verify_payload = {
            "adjustments": [
                {
                    "action": "expand",
                    "start": float(draft_windows[0][0]) - 0.5,
                    "end": float(draft_windows[0][1]) + 0.5,
                    "confidence": 0.95,
                }
            ]
        }
        if len(draft_windows) > 1:
            verify_payload["adjustments"].append(
                {
                    "action": "confirm",
                    "start": float(draft_windows[1][0]),
                    "end": float(draft_windows[1][1]),
                    "confidence": 0.9,
                }
            )

        config = create_standard_test_config()
        config.enable_ad_verify = True
        config.output.min_ad_segment_length_seconds = 1
        config.output.min_ad_segement_separation_seconds = 2
        config.output.fade_ms = 0

        mock_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(verify_payload))
                )
            ]
        )

        with patch("litellm.completion", return_value=mock_response):
            verified = AdVerifier(config).verify_and_store(
                post=post,
                draft_windows=draft_windows,
                segments=db_segs,
            )
        db.session.expire(post)
        db.session.refresh(post)
        refined = parse_refined_windows(post.refined_ad_boundaries)
        if not refined or not verified:
            _fail(
                f"verify failed refined={post.refined_ad_boundaries!r} "
                f"verified={verified!r}"
            )
        _ok(f"verify stored refined windows={len(refined)}")

        _labeled, effective = final_cut_windows(
            eligible,
            db_segs,
            refined_windows=refined,
            corrections=[],
        )
        _ok(f"final_cut_windows={len(effective)}")

        score = score_windows_detailed(effective, SALVADOR_DALI_GOLD_WINDOWS)
        _ok(
            "eval "
            f"f1={float(score['f1']):.3f} "
            f"false_cut={float(score['false_cut_seconds']):.1f}s "
            f"residual={float(score['residual_ad_seconds']):.1f}s"
        )

        touched = upsert_from_post_cut_windows(
            post=post,
            windows=effective,
            segments=db_segs,
            min_chars=24,
        )
        creatives = AdCreative.query.filter_by(feed_id=feed.id).all()
        if touched < 1 or not creatives:
            _fail(f"expected AdCreative rows, touched={touched} count={len(creatives)}")
        _ok(f"AdCreative upserted touched={touched} rows={len(creatives)}")

        loaded = load_feed_creatives(feed_id=feed.id)
        hit = match_segment_to_creatives(creatives[0].sample_text or "", loaded)
        if hit is None:
            _fail("creative match failed for indexed sample")
        _ok("cross-episode creative match hit")

        duration_ms = get_audio_duration_ms(str(unprocessed))
        if not duration_ms:
            _fail("could not read sample duration")
        duration_s = duration_ms / 1000.0
        cut_end = min(2.0, max(0.6, duration_s - 0.1))
        result = writer_client.update(
            "Post",
            post.id,
            {
                "refined_ad_boundaries": [
                    {
                        "orig_start": 0.5,
                        "orig_end": cut_end,
                        "refined_start": 0.5,
                        "refined_end": cut_end,
                        "confidence": 0.99,
                    }
                ]
            },
            wait=True,
        )
        if not result or not result.success:
            _fail(f"writer update failed: {getattr(result, 'error', result)}")
        db.session.expire(post)
        db.session.refresh(post)

        out_path = str(data_dir / "srv" / "e2e-processed.mp3")
        processor = AudioProcessor(config=config, db_session=db.session)
        removed = processor.process_audio(post, out_path)
        if not Path(out_path).is_file():
            _fail(f"processed audio missing: {out_path}")
        if not removed:
            _fail("process_audio removed no segments")
        _ok(f"ffmpeg cut ok removed_ms={removed}")

        print("E2E PASS: ad-detection P0 local pipeline")
        print(f"tmp={tmp}")


if __name__ == "__main__":
    main()
