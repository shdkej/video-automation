#!/usr/bin/env python3
"""pipeline.py — 하나의 입력 영상에서 4종 산출물을 한 번에 생성.

  롱폼(longform.mp4) : 하이라이트 컷 16:9 본편 (+ 클립별 자막, speech 모드만)
  숏츠(shorts_NN.mp4): 임팩트 구간 1~3개를 세로 9:16로 재프레이밍 (+ 자막, fade)
  썸네일(thumbnail_NN.jpg): 여러 구간에서 후보 N장
  인트로(intro.mp4)  : 베스트 구간 hook 클립 (+ fade, 풀스크린 타이틀 카드 미사용)

분석은 한 번만 수행하고(segments + captions), 그 결과를 4종 산출에 재사용한다.
--cache면 outputs/selection.json·captions.json을 재사용해 LLM/Whisper 재호출을 피한다.
부분 실패는 격리한다 — 한 종이 실패해도 나머지는 살리고 끝에 재시도 안내를 준다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from auto_cut import (
    PipelineError,
    cut_video,
    detect_scene_changes,
    filter_grounded_segments,
    mux_audio_into_video,
    overlaps,
    pick_scene_segments,
    resolve_llm_model,
    select_highlights,
    select_vision_segments,
    total_duration,
    transcribe_video,
    validate_transcript_quality,
)
from effects import (
    apply_audio_fade_out,
    apply_fade,
    build_short_footage,
    compute_xfade_windows,
    concat_sources,
    cut_with_xfade,
    extract_thumbnail,
    overlay_hook_text,
    reframe_vertical,
)
from probe import has_audio_stream, has_video_stream, probe_duration
from shorts_timeline import plan_short, punch_plan
from subtitle import render_subtitled
from subtitle_remotion import render_subtitled_remotion


# ============================================================================
# Domain — 분석 결과 가공 (외부 의존 없음)
# ============================================================================

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg"}
# .webm/.mkv/.ogv 등은 오디오만 담길 수도 있어 확장자로 못 가른다 → 스트림 검사


def split_media(paths: list) -> tuple:
    """입력 경로들을 (영상, 오디오)로 분류.

    명백한 확장자는 즉시 분류하고, .webm/.mkv 처럼 양쪽 다 가능한 컨테이너는
    실제 비디오 스트림 유무로 판정한다 (음성만 담긴 .webm을 영상으로 오인하지 않도록).
    여러 파일을 올릴 때 음성 파일이 섞여 있으면 영상에 이어붙이지 않고
    별도 사운드트랙으로 인식하기 위함.
    """
    videos, audios = [], []
    for p in paths:
        ext = p.suffix.lower()
        if ext in AUDIO_EXTS:
            audios.append(p)
        elif ext in VIDEO_EXTS:
            videos.append(p)
        elif has_video_stream(p):
            videos.append(p)
        elif has_audio_stream(p):
            audios.append(p)
        # 미디어 스트림이 전혀 없는 파일(.json 사이드카 등)은 입력에서 무시
    return videos, audios

# 자막 앞에 붙는 한국어 추임새/필러 — 선두에서만 제거 (의미어는 보존)
_FILLERS = {
    "어", "음", "아", "에", "오", "그", "저",
    "어음", "으음", "음음", "그그",
    "그러니까", "그래서", "그러면", "그리고", "그냥", "뭐", "막", "이제", "자", "근데",
}


def strip_leading_fillers(text: str) -> str:
    """문장 선두의 추임새를 연속으로 제거. '어음 그러니까 가장…' → '가장…'."""
    words = text.split()
    while words and words[0] in _FILLERS:
        words.pop(0)
    return " ".join(words)


def caption_for_segment(seg: dict, transcript_segments: list, max_len: int = 24) -> str:
    """speech 구간 → 겹치는 트랜스크립트 텍스트를 모아 한 줄 캡션(추임새 제거 + 축약)."""
    texts = [
        t["text"].strip()
        for t in transcript_segments
        if overlaps(seg["start"], seg["end"], t["start"], t["end"])
    ]
    cap = strip_leading_fillers(" ".join(x for x in texts if x).strip())
    if len(cap) > max_len:
        cap = cap[:max_len].rstrip() + "…"
    return cap


def rank_for_shorts(
    segments: list, captions: list, top_k: int,
    max_short_sec: float, ideal_sec: float = 25.0,
) -> list:
    """숏츠 후보 선정.

    선정: 구간에 임팩트 점수(score, speech=LLM·scene=scene_score)가 있으면 그걸 우선.
          점수가 없으면(vision 등) 숏폼 적정 길이(ideal_sec) 근접으로 폴백.
    절단: max_short_sec를 넘으면 앞부분(맥락)을 버리고 구간 중앙 기준으로 윈도우
          (숏폼 생존은 도입부가 아니라 펀치라인에 달림 → hook을 앞으로 당김).
    return: [{"start", "end", "reason", "caption", "hook"}, ...] (시간순)
            hook은 LLM이 준 후킹 문구(구캐시는 없어서 caption 폴백).
    """
    def rank_key(item):
        _, seg = item
        score = seg.get("score")
        if score is not None:
            return (0, -float(score))  # 점수 그룹 우선, 높은 점수 먼저
        return (1, abs((seg["end"] - seg["start"]) - ideal_sec))  # 폴백: 적정 길이 근접

    indexed = list(enumerate(segments))
    indexed.sort(key=rank_key)
    chosen = indexed[:top_k]
    chosen.sort(key=lambda x: x[1]["start"])

    out = []
    for i, seg in chosen:
        dur = seg["end"] - seg["start"]
        if dur <= max_short_sec:
            start, end = seg["start"], seg["end"]
        else:
            mid = (seg["start"] + seg["end"]) / 2
            start = max(seg["start"], mid - max_short_sec / 2)
            end = start + max_short_sec
        cap = captions[i] if i < len(captions) else ""
        out.append({
            "start": start,
            "end": end,
            "reason": seg.get("reason", ""),
            "caption": cap,
            "hook": seg.get("hook") or cap,  # 후킹 문구(구캐시는 caption 폴백)
        })
    return out


def pick_thumbnail_times(segments: list, count: int) -> list:
    """썸네일 후보 시점: 구간들을 시간축으로 균등 분산해 각 중앙. 최대 count개."""
    if not segments:
        return []
    if len(segments) <= count:
        chosen = segments
    else:
        step = len(segments) / count
        chosen = [segments[int(i * step)] for i in range(count)]
    return [(s["start"] + s["end"]) / 2 for s in chosen]


def pick_thumbnail_hook(segments: list, captions: list) -> str:
    """썸네일에 얹을 hook 문구 — 최고 점수 구간의 hook 우선, 없으면 첫 비어있지 않은 캡션."""
    scored = [s for s in segments if s.get("score") is not None and s.get("hook")]
    if scored:
        return max(scored, key=lambda s: float(s["score"]))["hook"]
    for s in segments:
        if s.get("hook"):
            return s["hook"]
    for c in captions:
        if c.strip():
            return c.strip()
    return ""


def snap_to_word_bounds(clip: dict, transcript: dict | None, max_shift: float = 0.6) -> dict:
    """클립 시작/끝을 가까운 발화(단어) 경계로 스냅 — 말이 중간에 끊긴 채 시작/끝나는 것 방지.

    max_shift 안에 단어 경계가 없으면 그대로 둔다(무발화 구간). 스냅으로 클립이
    지나치게 짧아지면(0.8초 미만) 원본을 유지한다. speech 모드 외에는 no-op.
    """
    if not transcript:
        return clip
    words = [w for ts in transcript.get("segments", []) for w in ts.get("words", [])]
    if not words:
        return clip
    start, end = clip["start"], clip["end"]
    starts = [w["start"] for w in words if abs(w["start"] - start) <= max_shift]
    if starts:
        start = min(starts, key=lambda t: abs(t - start))
    ends = [w["end"] for w in words if abs(w["end"] - end) <= max_shift]
    if ends:
        end = min(ends, key=lambda t: abs(t - end))
    if end - start < 0.8:
        return clip
    return {**clip, "start": round(start, 3), "end": round(end, 3)}


def pick_intro_clips(
    segments: list, intro_sec: float, transcript: dict | None = None, max_clips: int = 3,
) -> tuple[list, str | None]:
    """인트로 몽타주 클립 선정 — score 상위 구간을 시간순으로 잇는다.

    선정은 LLM의 숏폼 임팩트 score 내림차순(없는 구캐시/scene은 구간 길이 폴백),
    출력은 시간순 정렬(몽타주가 이야기 순서를 따르도록). 각 클립은 구간 앞부분
    intro_sec/k초를 쓰고 발화 경계에 스냅한다. hook은 최고 score 구간의 것.
    """
    has_score = any(s.get("score") is not None for s in segments)
    key = (lambda s: s.get("score") or 0) if has_score else (lambda s: s["end"] - s["start"])
    ranked = sorted(segments, key=key, reverse=True)

    k = min(max_clips, len(ranked))
    while k > 1 and intro_sec / k < 1.2:  # 클립당 1.2초 미만이면 개수를 줄인다
        k -= 1
    per_clip = intro_sec / k

    clips = []
    for seg in ranked[:k]:
        end = min(seg["end"], seg["start"] + per_clip)
        clips.append(snap_to_word_bounds({"start": seg["start"], "end": end}, transcript))
    clips.sort(key=lambda c: c["start"])

    hook = next((s.get("hook") for s in ranked if s.get("hook")), None)
    return clips, hook


# ============================================================================
# Service — 분석 (mode별로 segments + captions 한 번만 산출)
# ============================================================================

def analyze(args, outdir: Path) -> tuple:
    """입력 영상 → (segments, captions, transcript). 산출 4종이 공유하는 단일 분석.

    captions는 '자막으로 burn-in할 텍스트'다. scene/vision은 의미 있는 발화
    텍스트가 없으므로 빈 문자열로 둔다 (디버그 reason이 화면에 박히지 않도록).
    transcript는 speech 모드만 dict, scene/vision은 None (숏츠 발화별 자막용).
    각 구간의 reason은 selection.json에 그대로 보존된다.
    """
    sel_path = outdir / "selection.json"
    cap_path = outdir / "captions.json"
    if args.cache and sel_path.exists() and cap_path.exists():
        print("[분석] 캐시된 selection.json/captions.json 재사용 (LLM/분석 생략)")
        transcript = None
        if args.mode == "speech":
            tpath = args.input.with_suffix(".transcript.json")
            if tpath.exists():
                transcript = json.loads(tpath.read_text())
        return json.loads(sel_path.read_text()), json.loads(cap_path.read_text()), transcript

    duration = probe_duration(args.input)

    if args.mode == "scene":
        print(f"[분석] scene 모드 (threshold={args.scene_threshold})")
        scenes = detect_scene_changes(args.input, args.scene_threshold)
        segments = pick_scene_segments(
            scenes, duration, args.target_minutes * 60, args.clip_seconds,
        )
        if not segments:
            raise PipelineError(
                f"씬 체인지가 감지되지 않습니다 (threshold={args.scene_threshold}). "
                f"--scene-threshold를 더 낮게 (예: 0.1) 시도해보세요."
            )
        return segments, ["" for _ in segments], None

    if args.mode == "vision":
        print("[분석] vision 모드 (모자이크 + 비전 LLM)")
        resolve_llm_model(args)
        segments, mosaic_path = select_vision_segments(
            args.input, duration, args.llm_model,
            args.target_minutes, args.clip_seconds,
        )
        mosaic_path.unlink(missing_ok=True)  # 입력 폴더에 잔존하지 않도록 정리
        if not segments:
            raise PipelineError("LLM이 선정한 장면이 없습니다. 모델을 더 큰 것으로 바꿔보세요.")
        return segments, ["" for _ in segments], None

    # speech (기본)
    print("[분석] speech 모드 (Whisper + LLM)")
    resolve_llm_model(args)
    transcript_path = args.input.with_suffix(".transcript.json")
    if args.cache and transcript_path.exists():
        print(f"  트랜스크립트 캐시 로드: {transcript_path}")
        transcript = json.loads(transcript_path.read_text())
    else:
        transcript = transcribe_video(args.input, args.whisper_model, args.language)
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    try:
        validate_transcript_quality(transcript)
    except ValueError as e:
        raise PipelineError(f"트랜스크립트 품질 미달: {e}")

    raw = select_highlights(transcript, args.target_minutes, args.llm_model)
    segments = filter_grounded_segments(raw, transcript["segments"])
    if not segments:
        raise PipelineError("선정 구간이 트랜스크립트와 겹치지 않습니다 (LLM 환각 의심).")
    captions = [caption_for_segment(s, transcript["segments"]) for s in segments]
    return segments, captions, transcript


# ============================================================================
# Service — 산출물 4종 (각자 try/finally로 임시파일 정리)
# ============================================================================

_XFADE_TDUR = 0.3  # 롱폼 클립 간 크로스페이드 길이(초)


def longform_events(
    segments: list, captions: list, transcript: dict | None, windows: list,
) -> list:
    """하이라이트 컷의 최종 타임라인에 발화별 자막 이벤트를 매핑.

    transcript가 있으면 각 segment의 원본 구간과 겹치는 발화를 찾아, 그 발화의
    상대 위치를 xfade 윈도우 [W_start, W_end] 안으로 옮겨 다중 이벤트를 만든다
    (말하는 순서대로 자막이 흐름, 24자 제한 없음).
    없으면(scene/vision/구캐시 transcript 없음) 기존 segment별 24자 캡션으로 폴백한다.
    어느 쪽도 크래시하지 않는다 — shorts_events와 같은 철학.
    """
    tsegs = transcript.get("segments") if transcript else None
    if tsegs:
        events = []
        for seg, (w_start, w_end) in zip(segments, windows):
            for t in tsegs:
                if not overlaps(seg["start"], seg["end"], t["start"], t["end"]):
                    continue
                text = strip_leading_fillers(t["text"].strip())
                if not text:
                    continue
                start = max(w_start, w_start + (t["start"] - seg["start"]))
                end = min(w_end, w_start + (t["end"] - seg["start"]))
                if end <= start:
                    continue
                events.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
        if events:
            events.sort(key=lambda e: e["start"])
            return events
    # 폴백: segment별 24자 캡션 (구캐시/무음 구간)
    return [
        {"text": cap, "start": round(s, 3), "end": round(e, 3)}
        for cap, (s, e) in zip((c.strip() or " " for c in captions), windows)
    ]


def build_longform(
    args, segments: list, captions: list, outdir: Path, transcript=None,
) -> Path:
    """하이라이트 컷 16:9 본편. 클립 2개+면 xfade로 부드럽게 잇고 자막은 발화별로 흐른다.

    xfade는 클립을 tdur만큼 겹쳐 총길이를 줄이므로, 자막 타이밍을 단순 누적이 아니라
    compute_xfade_windows로 계산해야 싱크가 맞는다. 클립 1개면 xfade 생략.
    transcript가 있으면 발화 단위로 자막이 교체되고, 없으면 24자 캡션으로 폴백한다.
    """
    raw = outdir / "longform_raw.mp4"
    final = outdir / "longform.mp4"
    try:
        if len(segments) >= 2:
            cut_with_xfade(args.input, segments, raw, tdur=_XFADE_TDUR)
        else:
            cut_video(args.input, segments, raw)
        windows = compute_xfade_windows(segments, tdur=_XFADE_TDUR)

        events = longform_events(segments, captions, transcript, windows)
        if args.no_subtitle or not any(e["text"].strip() for e in events):
            raw.replace(final)
            return final
        if args.sub_engine == "remotion":
            render_subtitled_remotion(
                cut_path=raw, captions=[], segments=[], events=events, output=final,
                font_size=args.sub_font_size, margin_bottom=args.sub_margin_v,
                style=args.sub_style, mode="longform",
            )
        else:
            # PIL 엔진: events의 text/window 리스트로 변환해 발화별 자막 교체.
            # segments는 길이 검증·SRT 폴백용 더미(타이밍은 windows가 결정).
            ev_caps = [e["text"].strip() or " " for e in events]
            ev_windows = [(e["start"], e["end"]) for e in events]
            ev_segs = [{"start": s, "end": e} for s, e in ev_windows]
            render_subtitled(
                cut_path=raw, captions=ev_caps, segments=ev_segs, output=final,
                font_size=args.sub_font_size, margin_v=args.sub_margin_v,
                windows=ev_windows,
            )
        return final
    finally:
        raw.unlink(missing_ok=True)


# 숏츠 말 자막: 세로 1920 기준 65% 지점 = 하단 여백 ≈ 화면 높이의 30%
_SHORTS_FONT_SIZE = 56
_SHORTS_MARGIN_BOTTOM = 576  # 1920 * 0.30
_SHORTS_PADDED_TAIL = 0.05   # 마지막 발화 end가 숏츠 끝에 닿도록 보정


def _event_words(tseg: dict, lo: float, hi: float) -> list:
    """이벤트에 넣을 단어 목록 — 윈도우와 겹치는 단어에서 선두 추임새 제거.

    text와 words가 어긋나지 않도록, words가 있으면 text도 words에서 파생한다.
    """
    ws = [w for w in tseg.get("words", []) if w["start"] < hi and w["end"] > lo]
    while ws and ws[0]["word"].strip() in _FILLERS:
        ws = ws[1:]
    return ws


def shorts_events(spec: dict, transcript: dict | None, timeline) -> list:
    """숏츠 타임라인(점프컷 반영) 기준 자막 이벤트. words가 있으면 카라오케 타이밍 포함.

    모든 시점은 timeline.remap을 통과한다 — footage와 자막의 단일 출처.
    transcript 없음(scene/vision)·구캐시(words 없음)·겹침 없음 어느 쪽도
    크래시하지 않고 caption 단일 이벤트 또는 균일 stagger로 폴백한다.
    """
    lo = timeline.intervals[0][0]
    hi = timeline.intervals[-1][1]
    events = []
    for t in (transcript or {}).get("segments") or []:
        if t["end"] <= lo or t["start"] >= hi:
            continue
        ws = _event_words(t, lo, hi)
        if ws:
            ns = timeline.remap(ws[0]["start"])
            ne = timeline.remap(ws[-1]["end"])
            if ne - ns < 0.1:
                continue
            ev_words = [
                {"text": w["word"].strip(),
                 "start": round(max(0.0, timeline.remap(w["start"]) - ns), 3),
                 "end": round(max(0.0, timeline.remap(w["end"]) - ns), 3)}
                for w in ws
            ]
            events.append({
                "text": " ".join(w["text"] for w in ev_words),
                "start": round(ns, 3), "end": round(ne, 3), "words": ev_words,
            })
        else:
            text = strip_leading_fillers(t["text"].strip())
            if not text:
                continue
            ns = timeline.remap(max(t["start"], lo))
            ne = timeline.remap(min(t["end"], hi))
            if ne - ns < 0.1:
                continue
            events.append({"text": text, "start": round(ns, 3), "end": round(ne, 3)})
    if events:
        # 마지막 발화 end를 숏츠 끝까지 늘려 배너가 전 구간 상시 표시되게.
        events[-1]["end"] = max(events[-1]["end"], round(timeline.duration - _SHORTS_PADDED_TAIL, 3))
        return events
    cap = spec.get("caption", "").strip()
    if cap:
        return [{"text": cap, "start": 0.0, "end": round(timeline.duration, 3)}]
    return []


def build_one_short(args, spec: dict, stem: str, outdir: Path, transcript=None) -> Path:
    """단일 숏츠: 타임라인 계획(점프컷) → footage(punch-in) → 세로 → 자막 → 오디오 페이드.

    영상 페이드는 넣지 않는다 — 첫 프레임이 곧 커버(트렌드). 측정치(무음 제거·컷 수·
    초당 화면변화)를 출력해 A/B 비교의 근거를 남긴다.
    """
    tl = plan_short(
        spec["start"], spec["end"],
        (transcript or {}).get("segments"),
        min_silence=args.shorts_silence_min,
        jumpcut=not args.no_shorts_jumpcut,
    )
    clips = list(tl.intervals) if args.no_shorts_punchin else punch_plan(tl.intervals)

    raw = outdir / f".{stem}_raw.mp4"
    vert = outdir / f".{stem}_vert.mp4"
    subbed = outdir / f".{stem}_sub.mp4"
    final = outdir / f"{stem}.mp4"
    try:
        build_short_footage(args.input, clips, raw, punchin=not args.no_shorts_punchin)
        reframe_vertical(raw, vert, blur_bg=args.shorts_blur)

        events = shorts_events(spec, transcript, tl)
        hook = (spec.get("hook") or spec.get("caption", "")).strip() or None
        if args.no_subtitle or not events:
            src = vert
        elif args.sub_engine == "remotion":
            render_subtitled_remotion(
                cut_path=vert, output=subbed, captions=[], segments=[],
                events=events, hook=hook, mode="shorts",
                font_size=_SHORTS_FONT_SIZE, margin_bottom=_SHORTS_MARGIN_BOTTOM,
            )
            src = subbed
        else:
            # PIL+숏츠는 신규 스타일 미지원 — 현행 정적 캡션 그대로(첫 이벤트 텍스트).
            print("  ⚠ PIL 엔진은 숏츠 펀치 자막/hook 배너를 지원하지 않습니다(정적 캡션).")
            cap = events[0]["text"]
            seg0 = {"start": 0.0, "end": tl.duration}
            render_subtitled(
                cut_path=vert, captions=[cap], segments=[seg0], output=subbed,
                font_size=args.sub_font_size + 8, margin_v=args.sub_margin_v + 120,
                max_caption_width=960,
            )
            src = subbed
        apply_audio_fade_out(src, final)
        # 릴스 커버용 세로 썸네일 — hook 배너가 이미 박힌 첫 장면
        extract_thumbnail(final, outdir / f"{stem}_cover.jpg", at_sec=0.1, grade=False)

        changes = (len(clips) - 1) + len(events)
        print(f"  {stem}: {tl.duration:.1f}s | 무음 제거 {tl.removed_sec:.1f}s · "
              f"점프컷 {tl.cut_count} · 화면변화 {changes / max(tl.duration, 0.1):.1f}/s")
        return final
    finally:
        for tmp in (raw, vert, subbed):
            tmp.unlink(missing_ok=True)


def build_thumbnail(args, segments: list, captions: list, outdir: Path) -> list:
    """대표 구간들에서 후보 N장 추출 + hook 문구 burn-in(--no-thumb-text로 끔)."""
    times = pick_thumbnail_times(segments, args.thumbnail_count)
    hook = "" if args.no_thumb_text else pick_thumbnail_hook(segments, captions)
    paths = []
    for n, at in enumerate(times, 1):
        name = "thumbnail.jpg" if len(times) == 1 else f"thumbnail_{n:02d}.jpg"
        out = outdir / name
        extract_thumbnail(args.input, out, at, grade=not args.no_grade)
        if hook:
            overlay_hook_text(out, hook)
        paths.append(out)
    return paths


def build_intro(args, segments: list, outdir: Path, transcript: dict | None = None) -> Path:
    """score 상위 구간 몽타주 + Remotion 훅 배너 + fade.

    - 선정: LLM score 상위 2~3구간을 시간순 몽타주 (구캐시/scene은 길이 폴백)
    - 경계: speech 모드는 발화(단어) 경계 스냅으로 중간 절단 방지
    - 훅: 최고 score 구간의 hook 문구를 Remotion 배너로 오버레이
      (sub_engine이 remotion이 아니거나 렌더 실패 시 배너 없이 진행)
    """
    clips, hook = pick_intro_clips(segments, args.intro_seconds, transcript)
    raw = outdir / ".intro_raw.mp4"
    hooked = outdir / ".intro_hooked.mp4"
    final = outdir / "intro.mp4"
    try:
        cut_video(args.input, clips, raw)
        fade_src = raw
        if hook and not args.no_subtitle and args.sub_engine == "remotion":
            try:
                from subtitle_remotion import render_subtitled_remotion
                render_subtitled_remotion(
                    raw, [], [], hooked, events=[], hook=hook, mode="intro",
                )
                fade_src = hooked
            except Exception as e:  # noqa: BLE001 — 배너는 장식, 실패해도 인트로는 낸다
                print(f"  ⚠ 인트로 훅 배너 렌더 실패(배너 없이 진행): {e}")
        apply_fade(fade_src, final, fade_in=0.4, fade_out=0.4)
        return final
    finally:
        raw.unlink(missing_ok=True)
        hooked.unlink(missing_ok=True)


# ============================================================================
# Controller
# ============================================================================

WANTED = ("longform", "shorts", "thumbnail", "intro")


def run(args) -> None:
    for p in args.inputs:
        if not p.exists():
            raise PipelineError(f"입력 파일 없음: {p}")

    args.outdir.mkdir(parents=True, exist_ok=True)
    videos, audios = split_media(args.inputs)
    if not videos:
        raise PipelineError("영상 파일이 없습니다 (오디오만으로는 처리할 수 없습니다).")

    if len(videos) == 1:
        args.input = videos[0]
    else:
        # 여러 영상 → 공통 규격으로 정규화 후 이어붙여 단일 타임라인으로
        merged = args.outdir / "_merged_source.mp4"
        print(f"[입력] {len(videos)}개 영상 소스를 순서대로 이어붙이는 중…")
        concat_sources(videos, merged)
        args.input = merged

    # 오디오: --audio 명시가 우선, 없으면 업로드 파일 중 오디오를 자동 인식해 mux
    audio_path = args.audio
    if audio_path is None and audios:
        audio_path = audios[0]
        if len(audios) > 1:
            print(f"  ⚠ 오디오 파일이 {len(audios)}개입니다. 첫 번째({audios[0].name})만 사용합니다.")
        print(f"[입력] 오디오 파일 자동 인식: {audio_path.name} → 영상에 입힘")
    if audio_path:
        if not audio_path.exists():
            raise PipelineError(f"오디오 파일 없음: {audio_path}")
        muxed = args.outdir / "_muxed_av.mp4"
        mux_audio_into_video(args.input, audio_path, muxed)
        args.input = muxed

    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    wanted = list(args.only) if args.only else list(WANTED)

    segments, captions, transcript = analyze(args, outdir)
    (outdir / "selection.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2))
    (outdir / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2))
    print(f"  → {len(segments)}개 구간, 총 {total_duration(segments):.1f}초 선정")

    # segment 부족 경고 — 4종이 같은 구간으로 수렴하는 케이스를 사용자에게 알림
    if len(segments) == 1:
        print("  ⚠ 선정 구간이 1개뿐입니다. 썸네일/인트로/숏츠가 같은 장면에서 나옵니다.")
    if "shorts" in wanted and args.shorts_count > len(segments):
        print(f"  ⚠ 숏츠 {args.shorts_count}개 요청했으나 구간이 {len(segments)}개라 그만큼만 생성됩니다.")
    print()

    produced: dict = {}
    failed: list = []

    def step(label: str, key: str, fn) -> None:
        """한 종 생성 — 실패해도 다른 종은 계속(부분 실패 격리)."""
        if key not in wanted:
            return
        print(label)
        try:
            produced[key] = fn()
        except Exception as e:  # noqa: BLE001 — 종 단위 격리가 목적
            print(f"  ⚠ {key} 생성 실패: {e}")
            failed.append(key)

    step("[1/4] 롱폼 생성…", "longform",
         lambda: build_longform(args, segments, captions, outdir, transcript=transcript))
    step(f"[2/4] 숏츠 생성 (적정 길이 우선 상위 {args.shorts_count}개)…", "shorts",
         lambda: [build_one_short(args, s, f"shorts_{n:02d}", outdir, transcript=transcript)
                  for n, s in enumerate(
                      rank_for_shorts(segments, captions, args.shorts_count,
                                      args.shorts_max_seconds, args.shorts_ideal_seconds), 1)])
    step(f"[3/4] 썸네일 후보 {args.thumbnail_count}장 추출…", "thumbnail",
         lambda: build_thumbnail(args, segments, captions, outdir))
    step("[4/4] 인트로 생성…", "intro",
         lambda: build_intro(args, segments, outdir, transcript=transcript))

    print(f"\n완료 → {outdir}/")
    for key in WANTED:
        if key not in produced:
            continue
        val = produced[key]
        for p in (val if isinstance(val, list) else [val]):
            print(f"  - {p.name}")

    if failed:
        print(f"\n⚠ 실패한 종: {', '.join(failed)}")
        print(f"  → 분석은 재사용됩니다. 다음으로 재시도: "
              f"python pipeline.py {args.input} --cache --only {' '.join(failed)} -o {outdir}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="하나의 입력 영상에서 롱폼/숏츠/썸네일/인트로 4종을 생성",
    )
    parser.add_argument(
        "inputs", type=Path, nargs="+",
        help="입력 영상 경로(들). 여러 영상은 순서대로 이어붙임. "
             "오디오 파일(.mp3/.m4a/.wav 등)을 섞으면 영상에 입힐 사운드트랙으로 자동 인식",
    )
    parser.add_argument("--audio", type=Path, default=None, help="별도 오디오 파일(자동 mux). 우선순위 높음")
    parser.add_argument("-o", "--outdir", type=Path, default=Path("outputs"), help="출력 폴더")
    parser.add_argument(
        "--only", nargs="+", choices=WANTED, default=None,
        help="일부만 생성 (예: --only shorts thumbnail). 기본은 4종 전체",
    )

    # 분석
    parser.add_argument(
        "--mode", choices=["speech", "scene", "vision"], default="speech",
        help="speech: 음성+LLM(자막 가능), scene: 씬 감지(무료), vision: 모자이크+비전 LLM",
    )
    parser.add_argument("-t", "--target-minutes", type=float, default=10.0, help="롱폼 목표 길이(분)")
    parser.add_argument("--scene-threshold", type=float, default=0.3, help="scene 모드 임계값")
    parser.add_argument("--clip-seconds", type=float, default=6.0, help="scene/vision 클립 길이(초)")
    parser.add_argument("-m", "--whisper-model", default="medium", help="Whisper 모델(speech)")
    parser.add_argument("--language", default="ko", help="언어 코드(auto 가능)")
    parser.add_argument("--llm-model", default=None, help="하이라이트 선정 LLM(claude-*/gpt-*)")
    parser.add_argument(
        "--cache", action="store_true",
        help="outputs/selection.json·트랜스크립트 캐시 재사용 (LLM 재호출 회피)",
    )

    # 숏츠
    parser.add_argument("--shorts-count", type=int, default=2, help="숏츠 개수(1~3 권장)")
    parser.add_argument("--shorts-max-seconds", type=float, default=45.0, help="숏츠 최대 길이(초)")
    parser.add_argument("--shorts-ideal-seconds", type=float, default=25.0, help="숏츠 적정 길이(선정 기준)")
    parser.add_argument("--shorts-blur", action="store_true", help="세로 변환 시 좌우 crop 대신 흐린 배경")
    parser.add_argument("--shorts-silence-min", type=float, default=0.45,
                        help="점프컷으로 제거할 최소 무음 길이(초)")
    parser.add_argument("--no-shorts-jumpcut", action="store_true",
                        help="침묵 제거 점프컷 끄기 (A/B 비교용)")
    parser.add_argument("--no-shorts-punchin", action="store_true",
                        help="컷 경계 punch-in 줌 끄기 (A/B 비교용)")

    # 썸네일
    parser.add_argument("--thumbnail-count", type=int, default=3, help="썸네일 후보 장수")
    parser.add_argument("--no-thumb-text", action="store_true", help="썸네일 hook 문구 burn-in 생략")

    # 인트로
    parser.add_argument("--intro-seconds", type=float, default=4.0, help="인트로 hook 길이(초)")

    # 자막/효과
    parser.add_argument("--no-subtitle", action="store_true", help="자막 burn-in 생략")
    parser.add_argument("--no-grade", action="store_true", help="썸네일 컬러 그레이드 생략")
    parser.add_argument("--sub-font-size", type=int, default=44, help="자막 폰트 크기")
    parser.add_argument("--sub-margin-v", type=int, default=80, help="자막 하단 여백")
    parser.add_argument(
        "--sub-engine", choices=["pil", "remotion"], default="remotion",
        help="자막 엔진. remotion: 투명 애니메이션(기본, 숏츠 펀치+hook 배너), pil: PIL PNG 정적",
    )
    parser.add_argument(
        "--sub-style", choices=["fade", "kinetic"], default="fade",
        help="remotion 엔진 자막 스타일. fade: 전체 페이드, kinetic: 단어별 순차 등장",
    )

    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        sys.exit(str(e))
