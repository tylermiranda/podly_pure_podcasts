from unittest.mock import MagicMock

from podcast_processor.ad_audio_fingerprint import (
    fingerprint_distance,
    fingerprint_window,
    match_fingerprints,
    upsert_fingerprint,
)


def test_fingerprint_distance_identical() -> None:
    raw = "1,2,3,4"
    assert fingerprint_distance(raw, raw) == 0.0


def test_fingerprint_distance_disjoint() -> None:
    assert fingerprint_distance("0,0", "4294967295,4294967295") == 1.0


def test_fingerprint_window_parses_fpcalc_output() -> None:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "DURATION=5\nFINGERPRINT=9,8,7\n"
    fp = fingerprint_window("/tmp/x.mp3", 0.0, 5.0, subprocess_run=proc)
    assert fp == "9,8,7"
    assert "fpcalc" in proc.call_args.args[0][0]


def test_match_fingerprints_respects_threshold() -> None:
    row = MagicMock()
    row.fingerprint = "1,2,3"
    hits = match_fingerprints("1,2,3", [row], threshold=0.15)
    assert hits == [row]
    misses = match_fingerprints("4294967295,4294967295", [row], threshold=0.15)
    assert misses == []


def test_upsert_fingerprint(app) -> None:
    from app.extensions import db
    from app.models import AdAudioFingerprint, Feed

    with app.app_context():
        feed = Feed(title="t", rss_url="http://example.com/f.rss")
        db.session.add(feed)
        db.session.commit()
        assert upsert_fingerprint(
            feed_id=feed.id,
            fingerprint="1,2,3",
            duration_seconds=5.0,
            kind="jingle",
        )
        row = (
            db.session.query(AdAudioFingerprint)
            .filter_by(feed_id=feed.id, kind="jingle")
            .one()
        )
        assert row.hit_count == 1
