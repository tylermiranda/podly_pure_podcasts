#!/usr/bin/env python3
"""Local smoke for ad-detection P1 modules (candidates, gaps, jingle writer).

PODLY_WRITER_LOCAL_FALLBACK=1 uv run python scripts/e2e_ad_detection_p1.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def main() -> None:
    os.environ["PODLY_WRITER_LOCAL_FALLBACK"] = "1"
    os.environ["PYTEST_CURRENT_TEST"] = "e2e_ad_detection_p1"

    from podcast_processor.ad_audio_gaps import detect_suspicious_gaps
    from podcast_processor.ad_candidates import build_candidate_spans, candidate_indices

    segments = [
        {"sequence_num": 1, "start_time": 0.0, "end_time": 2.0, "text": "intro"},
        {
            "sequence_num": 2,
            "start_time": 12.0,
            "end_time": 20.0,
            "text": "brought to you by Example",
        },
    ]
    spans = build_candidate_spans(segments=segments, creatives=[])
    indices = candidate_indices(spans)
    assert indices, "expected candidate indices from cues/edges"
    print(f"OK: candidate spans={len(spans)} indices={len(indices)}")

    sample_stderr = """
[silencedetect @ 0x1] silence_start: 0.0
[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5
[silencedetect @ 0x1] silence_start: 10.0
[silencedetect @ 0x1] silence_end: 12.0 | silence_duration: 2.0
"""

    def fake_run(cmd, **kwargs):
        return type(
            "Proc",
            (),
            {"returncode": 0, "stderr": sample_stderr, "stdout": ""},
        )()

    gaps = detect_suspicious_gaps(
        audio_path="/tmp/x.mp3",
        segments=segments,
        duration=20.0,
        subprocess_run=fake_run,
    )
    assert gaps, "expected suspicious gap"
    print(f"OK: gap candidates={gaps}")

    tmp = Path(tempfile.mkdtemp(prefix="podly-e2e-p1-"))
    os.environ["PODLY_INSTANCE_DIR"] = str(tmp)
    os.environ["PODLY_PODCAST_DATA_DIR"] = str(tmp / "data")

    from flask import Flask

    from app.extensions import db
    from app.models import AdAudioFingerprint, Feed, Post
    from app.writer.actions.processor import upsert_jingle_template_action

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp / 'test.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()
        feed = Feed(title="P1", rss_url="https://example.com/p1.rss")
        db.session.add(feed)
        db.session.flush()
        post = Post(
            feed_id=feed.id,
            guid="p1",
            download_url="https://example.com/e.mp3",
            title="Ep",
            unprocessed_audio_path=str(ROOT / "src/tests/data/count_0_99.mp3"),
        )
        db.session.add(post)
        db.session.commit()
        with (
            patch(
                "podcast_processor.ad_audio_fingerprint.fingerprint_window",
                return_value="9,8,7",
            ),
            patch(
                "app.config_store.to_pydantic_config",
                return_value=type(
                    "Cfg",
                    (),
                    {
                        "jingle_min_seconds": 1.0,
                        "jingle_max_seconds": 15.0,
                    },
                )(),
            ),
        ):
            upsert_jingle_template_action(
                {
                    "feed_id": feed.id,
                    "post_id": post.id,
                    "start_time": 1.0,
                    "end_time": 4.0,
                }
            )
        row = (
            db.session.query(AdAudioFingerprint)
            .filter_by(feed_id=feed.id, kind="jingle")
            .one()
        )
        assert row.fingerprint == "9,8,7"
    print("OK: jingle template upsert")
    print("PASS: e2e_ad_detection_p1")


if __name__ == "__main__":
    main()
