from unittest.mock import patch

from app.writer.actions.processor import upsert_jingle_template_action


def test_upsert_jingle_template_action(app) -> None:
    from app.extensions import db
    from app.models import AdAudioFingerprint, Feed, Post

    with app.app_context():
        feed = Feed(title="Jingle Feed", rss_url="https://example.com/j.rss")
        db.session.add(feed)
        db.session.flush()
        post = Post(
            feed_id=feed.id,
            guid="jingle-post",
            download_url="https://example.com/e.mp3",
            title="Episode",
            unprocessed_audio_path="/tmp/audio.mp3",
        )
        db.session.add(post)
        db.session.commit()

        with patch(
            "podcast_processor.ad_audio_fingerprint.fingerprint_window",
            return_value="1,2,3,4",
        ):
            result = upsert_jingle_template_action(
                {
                    "feed_id": feed.id,
                    "post_id": post.id,
                    "start_time": 1.0,
                    "end_time": 5.0,
                }
            )

        assert result["kind"] == "jingle"
        row = (
            db.session.query(AdAudioFingerprint)
            .filter_by(feed_id=feed.id, kind="jingle")
            .one()
        )
        assert row.fingerprint == "1,2,3,4"
