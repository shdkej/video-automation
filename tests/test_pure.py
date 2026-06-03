"""순수 함수 단위 테스트 — 외부 의존(ffmpeg/LLM/Whisper) 없는 도메인 로직만.

분석 파이프라인의 핵심 판단(구간 검증·환각 필터·숏츠 랭킹·캡션·미디어 분류)을
ffmpeg/네트워크 없이 빠르게 회귀 검증한다. 실행: pytest tests/
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_cut import (  # noqa: E402
    PipelineError,
    filter_grounded_segments,
    overlaps,
    total_duration,
    validate_segments,
)
from pipeline import (  # noqa: E402
    caption_for_segment,
    rank_for_shorts,
    split_media,
    strip_leading_fillers,
)


# ---------------------------------------------------------------------------
# validate_segments
# ---------------------------------------------------------------------------

def test_validate_segments_keeps_valid_and_sorts():
    raw = [
        {"start": 30, "end": 40, "reason": "b", "score": 80},
        {"start": 5, "end": 10, "reason": "a"},
    ]
    out = validate_segments(raw, video_duration=100)
    assert [s["start"] for s in out] == [5, 30]  # 시간순 정렬
    assert out[1]["score"] == 80.0
    assert "score" not in out[0]  # 점수 없는 항목은 키 자체가 없음


def test_validate_segments_drops_out_of_range_and_inverted():
    raw = [
        {"start": -1, "end": 10},          # 음수 시작
        {"start": 10, "end": 5},           # start >= end
        {"start": 90, "end": 200},         # end가 duration+0.5 초과
        {"start": "x", "end": 10},         # 숫자 변환 실패
    ]
    assert validate_segments(raw, video_duration=100) == []


def test_validate_segments_clamps_end_within_tolerance():
    # duration+0.5 이내면 살리되 end는 duration으로 클램프
    out = validate_segments([{"start": 0, "end": 100.4}], video_duration=100)
    assert len(out) == 1
    assert out[0]["end"] == 100


# ---------------------------------------------------------------------------
# overlaps / filter_grounded_segments
# ---------------------------------------------------------------------------

def test_overlaps_basic():
    assert overlaps(0, 10, 5, 15) is True
    assert overlaps(0, 5, 5, 10) is False   # 경계 접촉은 겹침 아님
    assert overlaps(0, 5, 10, 20) is False


def test_filter_grounded_keeps_only_overlapping():
    transcript = [{"start": 0, "end": 10}, {"start": 50, "end": 60}]
    llm = [
        {"start": 5, "end": 8},     # transcript[0]과 겹침 → 채택
        {"start": 20, "end": 30},   # 어느 것과도 안 겹침 → 환각 제외
        {"start": 55, "end": 70},   # transcript[1]과 겹침 → 채택
    ]
    out = filter_grounded_segments(llm, transcript)
    assert [s["start"] for s in out] == [5, 55]


# ---------------------------------------------------------------------------
# total_duration
# ---------------------------------------------------------------------------

def test_total_duration():
    assert total_duration([{"start": 0, "end": 10}, {"start": 20, "end": 35}]) == 25
    assert total_duration([]) == 0


# ---------------------------------------------------------------------------
# strip_leading_fillers / caption_for_segment
# ---------------------------------------------------------------------------

def test_strip_leading_fillers_removes_only_leading():
    assert strip_leading_fillers("그러니까 가장 중요한 건") == "가장 중요한 건"
    assert strip_leading_fillers("어 음 그 결론은") == "결론은"
    # 중간/끝의 의미어는 보존
    assert strip_leading_fillers("결론은 그래서 중요") == "결론은 그래서 중요"
    assert strip_leading_fillers("그 그") == ""  # 전부 필러면 빈 문자열


def test_caption_for_segment_joins_and_truncates():
    transcript = [
        {"start": 0, "end": 5, "text": "그러니까 이것은"},
        {"start": 5, "end": 10, "text": "정말 중요한 핵심 메시지입니다"},
        {"start": 50, "end": 55, "text": "겹치지 않는 부분"},
    ]
    seg = {"start": 0, "end": 10}
    cap = caption_for_segment(seg, transcript, max_len=12)
    assert not cap.startswith("그러니까")        # 선두 필러 제거됨
    assert cap.endswith("…")                      # max_len 초과로 축약
    assert "겹치지" not in cap                     # 겹치지 않는 구간은 제외


def test_caption_for_segment_short_no_ellipsis():
    transcript = [{"start": 0, "end": 5, "text": "짧은 캡션"}]
    cap = caption_for_segment({"start": 0, "end": 5}, transcript, max_len=24)
    assert cap == "짧은 캡션"


# ---------------------------------------------------------------------------
# rank_for_shorts
# ---------------------------------------------------------------------------

def test_rank_for_shorts_prefers_high_score():
    segments = [
        {"start": 0, "end": 20, "score": 10, "reason": "low"},
        {"start": 30, "end": 50, "score": 90, "reason": "high"},
        {"start": 60, "end": 80, "score": 50, "reason": "mid"},
    ]
    captions = ["c0", "c1", "c2"]
    out = rank_for_shorts(segments, captions, top_k=2, max_short_sec=45)
    # 점수 상위 2개(90, 50) 채택, 결과는 시간순
    reasons = [o["reason"] for o in out]
    assert set(reasons) == {"high", "mid"}
    assert out[0]["start"] < out[1]["start"]


def test_rank_for_shorts_fallback_to_ideal_length_when_no_score():
    # score 없으면 ideal_sec 근접 길이 우선
    segments = [
        {"start": 0, "end": 60, "reason": "too_long"},     # 60s
        {"start": 70, "end": 95, "reason": "ideal"},        # 25s ← ideal에 근접
    ]
    out = rank_for_shorts(segments, ["a", "b"], top_k=1, max_short_sec=45, ideal_sec=25)
    assert out[0]["reason"] == "ideal"


def test_rank_for_shorts_truncates_around_center():
    # max_short_sec 초과 구간은 중앙 기준 윈도우로 절단
    segments = [{"start": 0, "end": 100, "score": 99, "reason": "x"}]
    out = rank_for_shorts(segments, ["cap"], top_k=1, max_short_sec=40)
    assert out[0]["end"] - out[0]["start"] == pytest.approx(40)
    # 중앙(50) 기준이라 도입부(0~30)는 버려짐
    assert out[0]["start"] == pytest.approx(30)
    assert out[0]["caption"] == "cap"


# ---------------------------------------------------------------------------
# split_media (명백한 확장자만 — 스트림 검사는 ffprobe 필요라 제외)
# ---------------------------------------------------------------------------

def test_split_media_by_extension():
    paths = [Path("a.mp4"), Path("b.mp3"), Path("c.mov"), Path("d.wav")]
    videos, audios = split_media(paths)
    assert videos == [Path("a.mp4"), Path("c.mov")]
    assert audios == [Path("b.mp3"), Path("d.wav")]


# ---------------------------------------------------------------------------
# PipelineError 계약 — web/app.py가 except Exception으로 잡을 수 있어야 함
# ---------------------------------------------------------------------------

def test_pipeline_error_is_ordinary_exception():
    assert issubclass(PipelineError, Exception)
    with pytest.raises(Exception):  # noqa: B017 — BaseException이 아님을 보장
        raise PipelineError("boom")
