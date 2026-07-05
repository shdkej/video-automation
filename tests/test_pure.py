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
    merge_scene_captions,
    overlaps,
    remap_transcript_to_cuts,
    total_duration,
    validate_segments,
)
from effects import compute_xfade_windows  # noqa: E402
from shorts_timeline import Timeline  # noqa: E402
from pipeline import (  # noqa: E402
    caption_for_segment,
    clean_shorts_args,
    longform_events,
    pick_intro_clips,
    pick_thumbnail_hook,
    rank_for_shorts,
    shorts_events,
    snap_to_word_bounds,
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


def test_validate_segments_preserves_hook_and_omits_when_absent():
    raw = [
        {"start": 0, "end": 10, "hook": "충격 한 줄"},
        {"start": 20, "end": 30},  # hook 없음 → 키 자체가 없어야(구캐시 호환)
    ]
    out = validate_segments(raw, video_duration=100)
    assert out[0]["hook"] == "충격 한 줄"
    assert "hook" not in out[1]


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
# rank_for_shorts — hook 전달/폴백
# ---------------------------------------------------------------------------

def test_rank_for_shorts_passes_hook_through():
    segments = [{"start": 0, "end": 20, "score": 90, "reason": "r", "hook": "충격 한 줄"}]
    out = rank_for_shorts(segments, ["cap"], top_k=1, max_short_sec=45)
    assert out[0]["hook"] == "충격 한 줄"


def test_rank_for_shorts_hook_falls_back_to_caption():
    # 구캐시(hook 없음) → caption으로 폴백
    segments = [{"start": 0, "end": 20, "score": 90, "reason": "r"}]
    out = rank_for_shorts(segments, ["폴백캡션"], top_k=1, max_short_sec=45)
    assert out[0]["hook"] == "폴백캡션"


# ---------------------------------------------------------------------------
# shorts_events — Timeline remap (점프컷·카라오케 words) vs caption 폴백
# ---------------------------------------------------------------------------

def _tl(*intervals):
    return Timeline(list(intervals))


def test_shorts_events_remaps_transcript_to_multiple_events():
    transcript = {"segments": [
        {"start": 10, "end": 13, "text": "첫 발화"},
        {"start": 13, "end": 16, "text": "둘째 발화"},
    ]}
    spec = {"start": 10.0, "end": 16.0, "caption": "폴백"}
    events = shorts_events(spec, transcript, _tl((10.0, 16.0)))
    assert len(events) == 2
    assert events[0]["start"] == 0.0 and events[0]["text"] == "첫 발화"
    assert events[1]["start"] == 3.0
    # 마지막 이벤트 end는 숏츠 끝까지 연장
    assert events[1]["end"] >= 6.0 - 0.05 - 1e-9


def test_shorts_events_jumpcut_timeline_shifts_later_events():
    # 12~14가 잘린 타임라인: 14초의 발화가 새 타임라인 2초로 당겨짐
    transcript = {"segments": [
        {"start": 10, "end": 12, "text": "앞"},
        {"start": 14, "end": 16, "text": "뒤"},
    ]}
    spec = {"start": 10.0, "end": 16.0, "caption": ""}
    events = shorts_events(spec, transcript, _tl((10.0, 12.0), (14.0, 16.0)))
    assert events[0]["start"] == 0.0
    assert events[1]["start"] == 2.0   # remap(14.0)


def test_shorts_events_karaoke_words_relative_to_event():
    transcript = {"segments": [
        {"start": 10, "end": 12, "text": "안녕 하세요",
         "words": [
             {"word": "안녕", "start": 10.2, "end": 10.8},
             {"word": "하세요", "start": 11.0, "end": 11.8},
         ]},
    ]}
    spec = {"start": 10.0, "end": 12.0, "caption": ""}
    [ev] = shorts_events(spec, transcript, _tl((10.0, 12.0)))
    assert ev["text"] == "안녕 하세요"      # text는 words에서 파생 (단일 출처)
    assert ev["start"] == 0.2               # 첫 단어 시작
    ws = ev["words"]
    assert ws[0] == {"text": "안녕", "start": 0.0, "end": 0.6}
    assert ws[1]["start"] == 0.8            # 11.0 remap → 1.0, 이벤트 상대 → 0.8


def test_shorts_events_words_strip_leading_fillers():
    transcript = {"segments": [
        {"start": 0, "end": 3, "text": "어 그러니까 본론은",
         "words": [
             {"word": "어", "start": 0.0, "end": 0.3},
             {"word": "그러니까", "start": 0.4, "end": 1.0},
             {"word": "본론은", "start": 1.2, "end": 2.0},
         ]},
    ]}
    spec = {"start": 0.0, "end": 3.0, "caption": ""}
    [ev] = shorts_events(spec, transcript, _tl((0.0, 3.0)))
    assert ev["text"] == "본론은"
    assert len(ev["words"]) == 1


def test_shorts_events_falls_back_to_caption_without_transcript():
    spec = {"start": 5.0, "end": 15.0, "caption": "캡션 폴백"}
    events = shorts_events(spec, None, _tl((5.0, 15.0)))
    assert events == [{"text": "캡션 폴백", "start": 0.0, "end": 10.0}]


def test_shorts_events_empty_when_no_transcript_and_no_caption():
    # scene/vision 구간(캡션 없음) → 빈 이벤트(자막 생략, 크래시 금지)
    spec = {"start": 5.0, "end": 15.0, "caption": ""}
    assert shorts_events(spec, None, _tl((5.0, 15.0))) == []


def test_shorts_events_falls_back_when_transcript_has_no_overlap():
    # transcript는 있으나 윈도우와 안 겹치면 caption 폴백
    transcript = {"segments": [{"start": 100, "end": 110, "text": "다른 데"}]}
    spec = {"start": 5.0, "end": 15.0, "caption": "캡션"}
    events = shorts_events(spec, transcript, _tl((5.0, 15.0)))
    assert events == [{"text": "캡션", "start": 0.0, "end": 10.0}]


# ---------------------------------------------------------------------------
# pick_thumbnail_hook — 썸네일에 얹을 hook 선정
# ---------------------------------------------------------------------------

def test_pick_thumbnail_hook_prefers_top_score_hook():
    segments = [
        {"start": 0, "end": 10, "score": 40, "hook": "낮은 훅"},
        {"start": 20, "end": 30, "score": 90, "hook": "강한 훅"},
    ]
    assert pick_thumbnail_hook(segments, ["a", "b"]) == "강한 훅"


def test_pick_thumbnail_hook_falls_back_to_caption_then_empty():
    segments = [{"start": 0, "end": 10}]
    assert pick_thumbnail_hook(segments, ["캡션"]) == "캡션"
    assert pick_thumbnail_hook([{"start": 0, "end": 1}], [""]) == ""


# ---------------------------------------------------------------------------
# longform_events — 발화별 매핑(다중) vs 24자 캡션 폴백, xfade 윈도우 clamp
# ---------------------------------------------------------------------------

def test_longform_events_splits_transcript_per_utterance():
    # 두 하이라이트 segment, 각 segment 안에 발화 2개씩 → 4개 이벤트로 흐름
    segments = [{"start": 100, "end": 110}, {"start": 200, "end": 210}]
    windows = compute_xfade_windows(segments, tdur=0.3)  # (0,10), (9.7,19.7)
    transcript = {"segments": [
        {"start": 101, "end": 104, "text": "첫 발화"},
        {"start": 105, "end": 108, "text": "둘째 발화"},
        {"start": 201, "end": 204, "text": "셋째 발화"},
        {"start": 205, "end": 208, "text": "넷째 발화"},
        {"start": 500, "end": 502, "text": "구간 밖"},  # 어느 segment와도 안 겹침
    ]}
    events = longform_events(segments, ["폴백캡션"], transcript, windows)
    assert len(events) == 4  # segment보다 훨씬 많은 발화 단위
    assert [e["text"] for e in events] == ["첫 발화", "둘째 발화", "셋째 발화", "넷째 발화"]
    # 첫 발화: W_start(0) + (101-100) = 1.0
    assert events[0]["start"] == pytest.approx(1.0)
    assert events[0]["end"] == pytest.approx(4.0)
    # 셋째 발화는 둘째 segment(W_start=9.7) 기준: 9.7 + (201-200) = 10.7
    assert events[2]["start"] == pytest.approx(10.7)
    # 단조 증가
    starts = [e["start"] for e in events]
    assert starts == sorted(starts)
    # … 잘림 없음
    assert all("…" not in e["text"] for e in events)


def test_longform_events_falls_back_to_captions_without_transcript():
    segments = [{"start": 0, "end": 10}, {"start": 20, "end": 30}]
    windows = compute_xfade_windows(segments, tdur=0.3)
    captions = ["캡션1", "캡션2"]
    events = longform_events(segments, captions, None, windows)
    assert [e["text"] for e in events] == ["캡션1", "캡션2"]
    assert events[0]["start"] == pytest.approx(windows[0][0])
    assert events[1]["start"] == pytest.approx(windows[1][0])


def test_longform_events_clamps_utterance_to_window():
    # 발화가 segment 경계를 넘어가도 [W_start, W_end] 안으로 clamp
    segments = [{"start": 100, "end": 110}]
    windows = [(0.0, 10.0)]
    transcript = {"segments": [
        {"start": 95, "end": 105, "text": "앞으로 넘침"},   # start<seg.start → 0으로 clamp
        {"start": 108, "end": 120, "text": "뒤로 넘침"},    # end>seg.end → 10으로 clamp
    ]}
    events = longform_events(segments, ["x"], transcript, windows)
    assert len(events) == 2
    assert events[0]["start"] == pytest.approx(0.0)   # max(0, -5) = 0
    assert events[1]["end"] == pytest.approx(10.0)    # min(10, 20) = 10
    assert all(0.0 <= e["start"] < e["end"] <= 10.0 for e in events)


# ---------------------------------------------------------------------------
# remap_transcript_to_cuts — 단일 컷 0기준 정렬
# ---------------------------------------------------------------------------

def test_remap_transcript_clips_and_offsets():
    transcript = [
        {"start": 5, "end": 8, "text": "A"},
        {"start": 12, "end": 15, "text": "B"},
    ]
    cut = [{"start": 10, "end": 20}]
    out = remap_transcript_to_cuts(transcript, cut)
    assert len(out) == 1                          # A는 컷 밖
    assert out[0]["start"] == pytest.approx(2)    # 12-10
    assert out[0]["end"] == pytest.approx(5)      # 15-10
    assert out[0]["text"] == "B"


# ---------------------------------------------------------------------------
# compute_xfade_windows — 겹침만큼 윈도우가 당겨짐
# ---------------------------------------------------------------------------

def test_compute_xfade_windows_overlap_pulls_later_clips():
    segments = [{"start": 0, "end": 10}, {"start": 0, "end": 8}]
    windows = compute_xfade_windows(segments, tdur=0.3)
    assert windows[0] == (0.0, 10.0)
    # 둘째 클립 가시 시작 = 첫 클립 길이 - tdur = 9.7
    assert windows[1][0] == pytest.approx(9.7)
    assert windows[1][1] == pytest.approx(17.7)   # 9.7 + 8


def test_compute_xfade_windows_single_clip():
    windows = compute_xfade_windows([{"start": 0, "end": 12}], tdur=0.3)
    assert windows == [(0.0, 12.0)]


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


# ---------------------------------------------------------------------------
# pick_intro_clips / snap_to_word_bounds — 인트로 몽타주 선정
# ---------------------------------------------------------------------------

INTRO_SEGS = [
    {"start": 0.0, "end": 8.0, "score": 40, "hook": "낮은 훅"},
    {"start": 10.0, "end": 13.0, "score": 95, "hook": "최고 훅!"},
    {"start": 20.0, "end": 24.0, "score": 70},
]


def test_pick_intro_clips_ranks_by_score_and_sorts_by_time():
    clips, hook = pick_intro_clips(INTRO_SEGS, intro_sec=4.5)
    assert len(clips) == 3
    # 선정은 score순이지만 출력은 시간순
    assert [c["start"] for c in clips] == sorted(c["start"] for c in clips)
    # hook은 최고 score 구간의 것
    assert hook == "최고 훅!"


def test_pick_intro_clips_falls_back_to_length_without_score():
    segs = [{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 12.0}]
    clips, hook = pick_intro_clips(segs, intro_sec=4.0, max_clips=1)
    assert clips[0]["start"] == 5.0  # 가장 긴 구간
    assert hook is None


def test_pick_intro_clips_reduces_count_when_too_short():
    # 2초 인트로에 3클립이면 클립당 0.67초 → 클립 수 축소
    clips, _ = pick_intro_clips(INTRO_SEGS, intro_sec=2.0)
    assert len(clips) == 1


def test_snap_to_word_bounds_snaps_within_shift():
    transcript = {"segments": [{"words": [
        {"word": "안녕", "start": 0.3, "end": 0.9},
        {"word": "하세요", "start": 0.9, "end": 1.6},
        {"word": "여행", "start": 3.1, "end": 3.8},
    ]}]}
    clip = {"start": 0.5, "end": 3.5}
    snapped = snap_to_word_bounds(clip, transcript)
    assert snapped["start"] == 0.3  # 가까운 단어 시작으로
    assert snapped["end"] == 3.8    # 가까운 단어 끝으로


def test_snap_to_word_bounds_noop_without_transcript():
    clip = {"start": 1.0, "end": 2.0}
    assert snap_to_word_bounds(clip, None) == clip
    assert snap_to_word_bounds(clip, {"segments": []}) == clip


def test_merge_scene_captions_fills_captions_and_enriches_segments():
    segs = [{"start": 0.0, "end": 6.0}, {"start": 10.0, "end": 16.0}]
    data = {"scenes": [
        {"idx": 1, "caption": "이 골목에서 한참 서 있었다", "hook": "여기 어디게?", "score": 80},
        {"idx": 2, "caption": "오늘의 하이라이트", "hook": "", "score": "높음"},  # score 형식 오류
        {"idx": 9, "caption": "범위 밖"},  # 무시
        {"idx": "x", "caption": "형식 오류"},  # 무시
    ]}
    caps = merge_scene_captions(segs, data)
    assert caps == ["이 골목에서 한참 서 있었다", "오늘의 하이라이트"]
    assert segs[0]["hook"] == "여기 어디게?" and segs[0]["score"] == 80.0
    assert "hook" not in segs[1] and "score" not in segs[1]  # 빈 hook·잘못된 score는 병합 안 함


def test_merge_scene_captions_empty_response():
    segs = [{"start": 0.0, "end": 6.0}]
    assert merge_scene_captions(segs, {}) == [""]


def test_clean_shorts_args_disables_effects_without_touching_original():
    from types import SimpleNamespace
    args = SimpleNamespace(
        no_subtitle=False, no_shorts_jumpcut=False, no_shorts_punchin=False,
        shorts_blur=True, sub_engine="remotion",
    )
    clean = clean_shorts_args(args)
    assert clean.no_subtitle and clean.no_shorts_jumpcut and clean.no_shorts_punchin
    assert clean.shorts_blur is True  # 리프레임 설정은 유지
    # 원본 args는 그대로 (사본이어야 풀 버전 생성에 영향 없음)
    assert args.no_subtitle is False and args.no_shorts_jumpcut is False


def test_snap_to_word_bounds_keeps_original_if_too_short():
    transcript = {"segments": [{"words": [
        {"word": "짧게", "start": 1.4, "end": 1.5},
    ]}]}
    clip = {"start": 1.0, "end": 1.6}
    # 스냅하면 1.4~1.5(0.1초)로 붕괴 → 원본 유지
    assert snap_to_word_bounds(clip, transcript) == clip
