from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from podcast_processor.ad_classifier import AdClassifier
from shared.test_utils import create_standard_test_config


@pytest.mark.parametrize("segments", [[], [SimpleNamespace(sequence_num=1)]])
def test_next_candidate_index(segments: list) -> None:
    classifier = AdClassifier(config=create_standard_test_config())
    assert classifier._next_candidate_index(0, {2, 5}, 10) == 2
    assert classifier._next_candidate_index(6, {2, 5}, 10) is None


def test_two_stage_skips_non_candidate_regions() -> None:
    config = create_standard_test_config()
    config.enable_two_stage_classify = True
    config.processing.num_segments_to_input_to_prompt = 2
    config.processing.max_overlap_segments = 0

    classifier = AdClassifier(config=config)
    segments = [
        SimpleNamespace(
            id=i + 1,
            sequence_num=i + 1,
            start_time=float(i),
            end_time=float(i + 1),
            text="content only",
        )
        for i in range(6)
    ]
    post = SimpleNamespace(id=1, feed_id=1, feed=None, unprocessed_audio_path=None)

    step_calls: list[int] = []

    def fake_step(params, overlap, index, segs):
        step_calls.append(index)
        return 2, []

    classifier._step = fake_step  # type: ignore[method-assign]
    classifier._collect_ad_detection_signals = MagicMock(  # type: ignore[method-assign]
        return_value={
            "creatives": [],
            "audio_fp_windows": [],
            "jingle_windows": [],
            "gap_windows": [],
            "debug": {},
        }
    )
    classifier._store_ad_detection_debug = MagicMock()  # type: ignore[method-assign]
    classifier._apply_sponsor_cue_labels = MagicMock(return_value=0)  # type: ignore[method-assign]
    classifier._label_repeated_creatives = MagicMock(return_value=0)  # type: ignore[method-assign]
    classifier._label_known_creatives = MagicMock(return_value=0)  # type: ignore[method-assign]
    classifier.boundary_refiner = None

    with patch("podcast_processor.ad_candidates.build_candidate_spans") as build:
        from podcast_processor.ad_candidates import CandidateSpan

        build.return_value = [CandidateSpan(4, 5, ["cue"])]
        classifier.classify(
            transcript_segments=segments,
            system_prompt="sys",
            user_prompt_template=MagicMock(),
            post=post,
        )

    assert step_calls == [4]
