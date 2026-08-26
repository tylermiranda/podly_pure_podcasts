from podcast_processor.ad_audio_gaps import (
    detect_suspicious_gaps,
    non_silent_runs,
    parse_silencedetect_output,
)

SAMPLE_STDERR = """
[silencedetect @ 0x1] silence_start: 0.0
[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5
[silencedetect @ 0x1] silence_start: 10.0
[silencedetect @ 0x1] silence_end: 12.0 | silence_duration: 2.0
"""


def test_parse_silencedetect_output() -> None:
    silences = parse_silencedetect_output(SAMPLE_STDERR)
    assert silences == [(0.0, 2.5), (10.0, 12.0)]


def test_non_silent_runs() -> None:
    runs = non_silent_runs([(0.0, 2.5), (10.0, 12.0)], duration=20.0)
    assert [(r.start, r.end) for r in runs] == [(2.5, 10.0), (12.0, 20.0)]


def test_detect_suspicious_gaps_flags_untranscribed_audio() -> None:
    segments = [
        {"start_time": 0.0, "end_time": 2.0, "text": "intro"},
        {"start_time": 12.0, "end_time": 20.0, "text": "outro"},
    ]

    def fake_run(cmd, **kwargs):
        proc = type("P", (), {})()
        proc.returncode = 0
        proc.stderr = SAMPLE_STDERR
        proc.stdout = ""
        return proc

    gaps = detect_suspicious_gaps(
        audio_path="/tmp/x.mp3",
        segments=segments,
        duration=20.0,
        min_seconds=3.0,
        subprocess_run=fake_run,
    )
    assert gaps == [(2.5, 10.0)]
