"""효과음 타이밍 계산 단위 테스트 — 출력 타임라인 매핑이 핵심 판단."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import sfx_events_longform, sfx_for_short  # noqa: E402


def test_longform_events_respect_xfade_overlap():
    # 10초 구간 3개, tdur=0.3 — 가시 시작은 0 / 9.7 / 19.4
    segs = [
        {"start": 0, "end": 10, "sfx": "ding.mp3"},
        {"start": 20, "end": 30},
        {"start": 40, "end": 50, "sfx": "boom.mp3"},
    ]
    assert sfx_events_longform(segs, tdur=0.3) == [(0.0, "ding.mp3"), (19.4, "boom.mp3")]


def test_longform_single_segment():
    assert sfx_events_longform([{"start": 5, "end": 9, "sfx": "pop.mp3"}]) == [(0.0, "pop.mp3")]


def test_longform_no_sfx_empty():
    assert sfx_events_longform([{"start": 0, "end": 10}]) == []


def test_short_matches_overlapping_segment():
    segs = [
        {"start": 0, "end": 10, "sfx": "ding.mp3"},
        {"start": 20, "end": 30, "sfx": "boom.mp3"},
    ]
    # spec이 구간을 절단(중앙 윈도우)해도 겹침으로 매칭된다
    assert sfx_for_short({"start": 22, "end": 28}, segs) == "boom.mp3"
    assert sfx_for_short({"start": 12, "end": 18}, segs) is None
