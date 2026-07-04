"""shorts_timeline 단위 테스트 — 단어 스냅·침묵 컷·remap·punch 계획 (외부 의존 없음)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts_timeline import (  # noqa: E402
    Timeline,
    cut_silences,
    flatten_words,
    plan_short,
    punch_plan,
    snap_to_words,
)

# 발화 2덩어리 사이에 1.0초 무음이 있는 합성 words (절대 시점)
WORDS = [
    {"word": "안녕하세요", "start": 10.0, "end": 10.6},
    {"word": "오늘은", "start": 10.7, "end": 11.1},
    {"word": "핵심만", "start": 11.2, "end": 11.8},
    # --- 1.0초 무음 ---
    {"word": "말씀드리면", "start": 12.8, "end": 13.5},
    {"word": "이겁니다", "start": 13.6, "end": 14.2},
]

SEGMENTS = [
    {"start": 10.0, "end": 11.8, "text": "안녕하세요 오늘은 핵심만",
     "words": WORDS[:3]},
    {"start": 12.8, "end": 14.2, "text": "말씀드리면 이겁니다",
     "words": WORDS[3:]},
]


def test_flatten_words_collects_and_sorts():
    out = flatten_words(SEGMENTS)
    assert [w["word"] for w in out] == ["안녕하세요", "오늘은", "핵심만", "말씀드리면", "이겁니다"]


def test_flatten_words_empty_for_legacy_cache():
    # 구캐시: words 키 없음 → 빈 목록 (크래시 없음)
    assert flatten_words([{"start": 0, "end": 5, "text": "안녕"}]) == []


def test_snap_to_words_expands_mid_word_boundaries():
    # 10.3은 '안녕하세요'(10.0~10.6) 중간 → 10.0으로, 13.9는 '이겁니다' 중간 → 14.2로
    assert snap_to_words(10.3, 13.9, WORDS) == (10.0, 14.2)


def test_snap_to_words_keeps_boundaries_in_gaps():
    # 무음 구간(11.9)과 단어 사이(10.65)는 그대로
    assert snap_to_words(10.65, 11.9, WORDS) == (10.65, 11.9)


def test_cut_silences_removes_long_gap_and_pads():
    out = cut_silences(10.0, 14.2, WORDS, min_silence=0.45, pad=0.12, min_clip=0.6)
    assert len(out) == 2
    (s1, e1), (s2, e2) = out
    assert s1 == 10.0            # 윈도우 시작 클램프 (10.0-0.12 < 10.0)
    assert abs(e1 - 11.92) < 1e-6  # 11.8 + pad
    assert abs(s2 - 12.68) < 1e-6  # 12.8 - pad
    assert e2 == 14.2            # 윈도우 끝 클램프


def test_cut_silences_keeps_short_gaps():
    # 단어 간 0.1초 간격은 무음 아님 → 통짜 1개
    out = cut_silences(10.0, 11.8, WORDS[:3], min_silence=0.45)
    assert len(out) == 1


def test_cut_silences_no_words_returns_whole():
    assert cut_silences(3.0, 20.0, []) == [(3.0, 20.0)]


def test_cut_silences_falls_back_when_total_too_short():
    # keep 총량 3초 미만 → 통짜 폴백
    words = [{"word": "짧다", "start": 5.0, "end": 5.4}]
    assert cut_silences(0.0, 30.0, words) == [(0.0, 30.0)]


def test_timeline_remap_within_and_across_gaps():
    tl = Timeline([(10.0, 12.0), (13.0, 15.0)])
    assert tl.duration == 4.0
    assert tl.remap(10.0) == 0.0
    assert tl.remap(11.0) == 1.0
    assert tl.remap(12.5) == 2.0   # 잘린 무음 → 다음 keep 시작으로 클램프
    assert tl.remap(14.0) == 3.0
    assert tl.remap(99.0) == 4.0   # 범위 밖 → duration 클램프
    assert tl.cut_count == 1
    assert abs(tl.removed_sec - 1.0) < 1e-6


def test_plan_short_without_words_is_single_interval():
    tl = plan_short(5.0, 30.0, [{"start": 0, "end": 40, "text": "x"}])
    assert tl.intervals == [(5.0, 30.0)]
    assert tl.cut_count == 0


def test_plan_short_jumpcut_false_is_single_interval():
    tl = plan_short(10.0, 14.2, SEGMENTS, jumpcut=False)
    assert tl.intervals == [(10.0, 14.2)]


def test_plan_short_snaps_then_cuts():
    tl = plan_short(10.3, 13.9, SEGMENTS)
    assert tl.cut_count == 1
    assert tl.intervals[0][0] == 10.0   # 스냅 결과
    assert tl.intervals[-1][1] == 14.2


def test_punch_plan_passthrough_for_multiple_intervals():
    ivs = [(0.0, 4.0), (5.0, 9.0)]
    assert punch_plan(ivs) == ivs


def test_punch_plan_splits_long_single_interval():
    out = punch_plan([(0.0, 10.0)], period=4.0)
    assert out[0] == (0.0, 4.0)
    assert out[-1][1] == 10.0
    assert all(e > s for s, e in out)
    # 연속성: 가상 컷은 시간을 버리지 않는다
    for (_, e1), (s2, _) in zip(out, out[1:]):
        assert abs(e1 - s2) < 1e-9


def test_punch_plan_short_single_interval_untouched():
    assert punch_plan([(0.0, 5.0)], period=4.0) == [(0.0, 5.0)]


def test_punch_plan_merges_tiny_tail():
    # 마지막 조각이 1초 미만이면 직전과 병합
    out = punch_plan([(0.0, 8.5)], period=4.0)
    assert out[-1][1] == 8.5
    assert out[-1][1] - out[-1][0] >= 1.0
