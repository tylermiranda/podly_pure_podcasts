"""Tests for cross-episode ad creative index."""

from types import SimpleNamespace

from podcast_processor.ad_creatives import (
    creative_fingerprint,
    extract_creative_texts_from_windows,
    jaccard,
    match_segment_to_creatives,
    token_set,
    upsert_creatives_for_feed,
)
from podcast_processor.ad_spans import normalize_ad_copy


def test_creative_fingerprint_stable() -> None:
    text = normalize_ad_copy("Visit crocs.com for a style for every kind of girl.")
    assert creative_fingerprint(text) == creative_fingerprint(text)
    assert len(creative_fingerprint(text)) == 32


def test_jaccard_and_match() -> None:
    creatives = [
        SimpleNamespace(
            normalized_text=normalize_ad_copy(
                "Head to crocs.com or a store near you now for canvas clogs."
            ),
            sample_text="Head to crocs.com or a store near you now for canvas clogs.",
        )
    ]
    exact = match_segment_to_creatives(
        "Head to crocs.com or a store near you now for canvas clogs.",
        creatives,
        jaccard_threshold=0.85,
    )
    assert exact is creatives[0]

    fuzzy = match_segment_to_creatives(
        "Head to crocs.com or a store near you now for canvas clogs please.",
        creatives,
        jaccard_threshold=0.85,
    )
    assert fuzzy is creatives[0]

    miss = match_segment_to_creatives(
        "It is July the 1st, 1936 in Spain.",
        creatives,
        jaccard_threshold=0.85,
    )
    assert miss is None
    assert jaccard(token_set("a b c"), token_set("a b d")) == 2 / 4


def test_extract_creative_texts_from_windows() -> None:
    segments = [
        SimpleNamespace(
            start_time=1.0,
            end_time=5.0,
            text="Visit example.com for a free trial of our product today.",
        ),
        SimpleNamespace(
            start_time=20.0,
            end_time=25.0,
            text="It is July the 1st, 1936.",
        ),
    ]
    texts = extract_creative_texts_from_windows(
        segments, [(0.0, 10.0)], min_chars=24
    )
    assert len(texts) == 1
    assert "example.com" in texts[0]


def test_upsert_creatives_for_feed_across_posts(app) -> None:
    from app.extensions import db
    from app.models import AdCreative, Feed, Post

    with app.app_context():
        feed = Feed(
            title="Creative Feed",
            description="d",
            rss_url="https://example.com/creative-feed.xml",
        )
        db.session.add(feed)
        db.session.commit()
        post_a = Post(
            feed_id=feed.id,
            guid="creative-guid-a",
            download_url="https://example.com/a.mp3",
            title="A",
            whitelisted=True,
        )
        post_b = Post(
            feed_id=feed.id,
            guid="creative-guid-b",
            download_url="https://example.com/b.mp3",
            title="B",
            whitelisted=True,
        )
        db.session.add_all([post_a, post_b])
        db.session.commit()

        text = "Head to crocs.com or a store near you now for canvas clogs."
        touched = upsert_creatives_for_feed(
            feed_id=feed.id,
            texts=[text],
            source_post_id=post_a.id,
        )
        assert touched == 1
        row = AdCreative.query.filter_by(feed_id=feed.id).one()
        assert row.hit_count == 1

        touched_again = upsert_creatives_for_feed(
            feed_id=feed.id,
            texts=[text],
            source_post_id=post_b.id,
        )
        assert touched_again == 1
        db.session.refresh(row)
        assert row.hit_count == 2
        assert row.source_post_id == post_b.id

        other = Feed(
            title="Other Feed",
            description="d",
            rss_url="https://example.com/other-feed.xml",
        )
        db.session.add(other)
        db.session.commit()
        assert AdCreative.query.filter_by(feed_id=other.id).count() == 0
