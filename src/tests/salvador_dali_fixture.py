"""Salvador Dalí (Short History Of...) gold cut windows and compact transcript.

Episode guid e60d215e-7c7c-11f1-8d29-b70ac5a24b6a. Windows stop at the
narrative date cold-opens and do not include story lines.
"""

from types import SimpleNamespace
from typing import Any

# Full Crocs+Chevy (or Noiser+/Booking) blocks that should be removed.
SALVADOR_DALI_GOLD_WINDOWS: list[tuple[float, float]] = [
    (1.0, 55.9),
    (678.2, 733.1),
    (1039.8, 1094.7),
    (1949.5, 2004.4),
    (2996.3, 3066.4),
]

NARRATIVE_STARTS = (
    "It is July the 1st, 1936.",
    "It is August 1929, in the village of Caracas.",
    "It's 7 in the morning on March 16, 1939.",
)

# Story timestamps that must not be covered by predicted cut windows.
NARRATIVE_START_TIMES = (59.4, 735.0, 1099.7, 2008.4, 2989.7)


def _seg(
    sequence_num: int,
    start: float,
    end: float,
    text: str,
    *,
    ad: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sequence_num + 1,
        sequence_num=sequence_num,
        start_time=start,
        end_time=end,
        text=text,
        words=None,
        labeled_ad=ad,
    )


def salvador_dali_segments() -> list[SimpleNamespace]:
    """Compact transcript covering each ad block and the adjacent story lines."""
    return [
        _seg(0, 1.0, 3.3, "When you think about Crocs, you think the classic clog."),
        _seg(1, 4.0, 6.3, "But did you know there's also a canvas clog?"),
        _seg(2, 6.7, 7.7, "A vegan suede clog."),
        _seg(3, 8.1, 9.6, "A coastal-inspired style."),
        _seg(4, 10.0, 13.0, "A style with studs, straps, and lots of sass."),
        _seg(5, 13.7, 15.8, "Honestly, I could keep going with the surprises."),
        _seg(
            6,
            16.3,
            20.7,
            "You've got to see them for yourself because there is literally a style for every kind of girl.",
        ),
        _seg(
            7,
            21.1,
            24.5,
            "So do your thing and head to crocs.com or a store near you now.",
            ad=True,
        ),
        _seg(8, 29.6, 32.3, "Chevy is called the heartbeat of America for a reason."),
        _seg(
            9,
            33.3,
            46.7,
            "With SUVs made to move with your rhythm, the versatile Equinox tackles your entire day, the spacious Traverse fits your crew and your whole weekend, and Trax brings style with value you can count on.",
        ),
        _seg(
            10,
            47.6,
            52.5,
            "All infused with tech that has your back, so your drive always hits the right chord.",
        ),
        _seg(11, 53.9, 55.9, "Chevrolet, together let's drive."),
        _seg(12, 59.4, 61.4, "It is July the 1st, 1936."),
        _seg(
            114,
            670.2,
            676.4,
            "And how can I cultivate this persona that actually becomes a little bit of the persona that he keeps for the rest of his life?",
        ),
        _seg(
            115,
            678.2,
            680.5,
            "When you think about Crocs, you think the classic clog.",
            ad=True,
        ),
        _seg(116, 681.2, 683.5, "But did you know there's also a canvas clog?"),
        _seg(117, 683.9, 684.9, "A vegan suede clog."),
        _seg(118, 685.3, 686.8, "A coastal-inspired style."),
        _seg(119, 687.2, 690.2, "A style with studs, straps, and lots of sass."),
        _seg(120, 690.9, 693.0, "Honestly, I could keep going with the surprises."),
        _seg(
            121,
            693.5,
            697.8,
            "You've got to see them for yourself because there is literally a style for every kind of girl.",
        ),
        _seg(
            122,
            698.3,
            701.7,
            "So do your thing and head to crocs.com or a store near you now.",
            ad=True,
        ),
        _seg(
            123, 706.8, 709.6, "Chevy is called the heartbeat of America for a reason."
        ),
        _seg(
            124,
            710.5,
            723.9,
            "With SUVs made to move with your rhythm, the versatile Equinox tackles your entire day, the spacious Traverse fits your crew and your whole weekend, and Trax brings style with value you can count on.",
        ),
        _seg(
            125,
            724.9,
            729.6,
            "All infused with tech that has your back, so your drive always hits the right chord.",
        ),
        _seg(126, 731.0, 733.1, "Chevrolet, together let's drive."),
        _seg(
            127,
            735.0,
            740.2,
            "The young artist makes friends, too, with Luis Buñuel, who would become known as a pioneering filmmaker.",
        ),
        _seg(
            173,
            1026.0,
            1033.8,
            "For a time, however, the object of his affection remains unimpressed, prompting him to reinvent himself with an unusual makeover.",
        ),
        _seg(
            174,
            1039.8,
            1042.1,
            "When you think about Crocs, you think the classic clog.",
        ),
        _seg(175, 1042.8, 1045.1, "But did you know there's also a canvas clog?"),
        _seg(176, 1045.5, 1046.5, "A vegan suede clog."),
        _seg(177, 1046.9, 1048.4, "A coastal-inspired style."),
        _seg(178, 1048.8, 1051.8, "A style with studs, straps, and lots of sass."),
        _seg(179, 1052.5, 1054.6, "Honestly, I could keep going with the surprises."),
        _seg(
            180,
            1055.0,
            1059.4,
            "You've got to see them for yourself because there is literally a style for every kind of girl.",
        ),
        _seg(
            181,
            1059.8,
            1063.2,
            "So do your thing and head to crocs.com or a store near you now.",
            ad=True,
        ),
        _seg(
            182,
            1068.4,
            1071.1,
            "Chevy is called the heartbeat of America for a reason.",
        ),
        _seg(
            183,
            1072.1,
            1085.4,
            "With SUVs made to move with your rhythm, the versatile Equinox tackles your entire day, the spacious Traverse fits your crew and your whole weekend, and Trax brings style with value you can count on.",
        ),
        _seg(
            184,
            1086.4,
            1091.2,
            "All infused with tech that has your back, so your drive always hits the right chord.",
        ),
        _seg(185, 1092.6, 1094.7, "Chevrolet, together let's drive."),
        _seg(186, 1099.7, 1104.6, "It is August 1929, in the village of Caracas."),
        _seg(
            328,
            1937.5,
            1943.1,
            "But with work, money, and acclaim pouring in from the United States, Dali continues to hog the headlines.",
        ),
        _seg(
            329,
            1949.5,
            1951.8,
            "When you think about Crocs, you think the classic clog.",
            ad=True,
        ),
        _seg(330, 1952.5, 1954.8, "But did you know there's also a canvas clog?"),
        _seg(331, 1955.2, 1956.2, "A vegan suede clog."),
        _seg(332, 1956.6, 1958.1, "A coastal-inspired style."),
        _seg(333, 1958.5, 1961.5, "A style with studs, straps, and lots of sass."),
        _seg(334, 1962.2, 1964.3, "Honestly, I could keep going with the surprises."),
        _seg(
            335,
            1964.7,
            1969.1,
            "You've got to see them for yourself because there is literally a style for every kind of girl.",
        ),
        _seg(
            336,
            1969.5,
            1972.9,
            "So do your thing and head to crocs.com or a store near you now.",
            ad=True,
        ),
        _seg(
            337,
            1978.1,
            1980.8,
            "Chevy is called the heartbeat of America for a reason.",
        ),
        _seg(
            338,
            1981.8,
            1995.1,
            "With SUVs made to move with your rhythm, the versatile Equinox tackles your entire day, the spacious Traverse fits your crew and your whole weekend, and Trax brings style with value you can count on.",
        ),
        _seg(
            339,
            1996.1,
            2000.9,
            "All infused with tech that has your back, so your drive always hits the right chord.",
        ),
        _seg(340, 2002.3, 2004.4, "Chevrolet, together let's drive."),
        _seg(341, 2008.4, 2012.0, "It's 7 in the morning on March 16, 1939."),
        _seg(483, 2989.7, 2990.7, "That's next time."),
        _seg(
            484,
            2996.3,
            3002.6,
            "You can listen to the next two episodes of Short History of right now, without waiting and without adverts, by subscribing to Noiser+.",
        ),
        _seg(
            485,
            3003.1,
            3010.6,
            "Just hit the link in the episode description or head to www.noiser.com forward slash subscriptions to unlock more episodes today.",
            ad=True,
        ),
        _seg(
            486,
            3012.1,
            3014.4,
            "When you think about Crocs, you think the classic clog.",
        ),
        _seg(487, 3015.1, 3017.4, "But did you know there's also a canvas clog?"),
        _seg(488, 3017.8, 3018.8, "A vegan suede clog."),
        _seg(489, 3019.2, 3020.7, "A coastal-inspired style."),
        _seg(490, 3021.1, 3024.1, "A style with studs, straps, and lots of sass."),
        _seg(491, 3024.8, 3026.9, "Honestly, I could keep going with the surprises."),
        _seg(
            492,
            3027.4,
            3031.7,
            "You've got to see them for yourself because there is literally a style for every kind of girl.",
        ),
        _seg(
            493,
            3032.1,
            3035.5,
            "So do your thing and head to crocs.com or a store near you now.",
            ad=True,
        ),
        _seg(
            494,
            3039.4,
            3055.6,
            "Booking.com is the easiest way from a day surrounded by noise to a stay surrounded by nature.",
            ad=True,
        ),
        _seg(495, 3058.5, 3059.3, "That's nice."),
        _seg(496, 3060.9, 3061.9, "Go on, book it."),
        _seg(497, 3062.3, 3063.0, "It's easy."),
        _seg(498, 3063.9, 3064.8, "Booking.com.", ad=True),
        _seg(499, 3065.1, 3066.4, "Booking.yeah."),
    ]


def labeled_ad_windows(segments: list[SimpleNamespace]) -> list[tuple[float, float]]:
    return [
        (float(seg.start_time), float(seg.end_time))
        for seg in segments
        if getattr(seg, "labeled_ad", False)
    ]


def windows_cover(windows: list[tuple[float, float]], timestamp: float) -> bool:
    return any(start - 0.05 <= timestamp < end for start, end in windows)


def persist_salvador_dali_episode(
    db_session: Any,
    *,
    guid: str = "e60d215e-7c7c-11f1-8d29-b70ac5a24b6a",
) -> tuple[Any, list[Any]]:
    """Insert the compact Dalí transcript with CTA-only ad labels."""
    from app.models import Feed, Identification, ModelCall, Post, TranscriptSegment

    feed = Feed(
        title="Short History Of...",
        rss_url=f"https://example.com/{guid}.rss",
    )
    db_session.add(feed)
    db_session.commit()
    post = Post(
        feed_id=feed.id,
        guid=guid,
        download_url=f"https://example.com/{guid}.mp3",
        title="Salvador Dali",
        unprocessed_audio_path="/tmp/dali.mp3",
        whitelisted=True,
    )
    db_session.add(post)
    db_session.commit()

    fixture_segments = salvador_dali_segments()
    db_segments = []
    for fixture in fixture_segments:
        row = TranscriptSegment(
            post_id=post.id,
            sequence_num=fixture.sequence_num,
            start_time=fixture.start_time,
            end_time=fixture.end_time,
            text=fixture.text,
        )
        db_segments.append(row)
    db_session.add_all(db_segments)
    db_session.commit()

    model_call = ModelCall(
        post_id=post.id,
        model_name="test-model",
        first_segment_sequence_num=0,
        last_segment_sequence_num=fixture_segments[-1].sequence_num,
        prompt="classify",
        response='{"ad_segments":[]}',
        status="success",
        language=None,
    )
    db_session.add(model_call)
    db_session.commit()

    idents = []
    for fixture, row in zip(fixture_segments, db_segments, strict=True):
        if not getattr(fixture, "labeled_ad", False):
            continue
        idents.append(
            Identification(
                transcript_segment_id=row.id,
                model_call_id=model_call.id,
                label="ad",
                confidence=0.95,
            )
        )
    db_session.add_all(idents)
    db_session.commit()
    return post, db_segments
