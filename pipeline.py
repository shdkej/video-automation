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
from effects import apply_fade, concat_sources, extract_thumbnail, reframe_vertical
from probe import has_video_stream, probe_duration
from subtitle import render_subtitled


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
        else:
            (videos if has_video_stream(p) else audios).append(p)
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
    return: [{"start", "end", "reason", "caption"}, ...] (시간순)
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
        out.append({
            "start": start,
            "end": end,
            "reason": seg.get("reason", ""),
            "caption": captions[i] if i < len(captions) else "",
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


def pick_intro_segment(segments: list, intro_sec: float) -> dict:
    """인트로 hook: 가장 긴 구간의 앞 intro_sec초."""
    best = max(segments, key=lambda s: s["end"] - s["start"])
    end = min(best["end"], best["start"] + intro_sec)
    return {"start": best["start"], "end": end}


# ============================================================================
# Service — 분석 (mode별로 segments + captions 한 번만 산출)
# ============================================================================

def analyze(args, outdir: Path) -> tuple:
    """입력 영상 → (segments, captions). 산출 4종이 공유하는 단일 분석.

    captions는 '자막으로 burn-in할 텍스트'다. scene/vision은 의미 있는 발화
    텍스트가 없으므로 빈 문자열로 둔다 (디버그 reason이 화면에 박히지 않도록).
    각 구간의 reason은 selection.json에 그대로 보존된다.
    """
    sel_path = outdir / "selection.json"
    cap_path = outdir / "captions.json"
    if args.cache and sel_path.exists() and cap_path.exists():
        print("[분석] 캐시된 selection.json/captions.json 재사용 (LLM/분석 생략)")
        return json.loads(sel_path.read_text()), json.loads(cap_path.read_text())

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
        return segments, ["" for _ in segments]

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
        return segments, ["" for _ in segments]

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
    return segments, captions


# ============================================================================
# Service — 산출물 4종 (각자 try/finally로 임시파일 정리)
# ============================================================================

def build_longform(args, segments: list, captions: list, outdir: Path) -> Path:
    """하이라이트 컷 16:9 본편. 자막 있으면 클립별 burn-in."""
    raw = outdir / "longform_raw.mp4"
    final = outdir / "longform.mp4"
    try:
        cut_video(args.input, segments, raw)
        if args.no_subtitle or not any(c.strip() for c in captions):
            raw.replace(final)
            return final
        safe_caps = [c.strip() or " " for c in captions]
        render_subtitled(
            cut_path=raw, captions=safe_caps, segments=segments, output=final,
            font_size=args.sub_font_size, margin_v=args.sub_margin_v,
        )
        return final
    finally:
        raw.unlink(missing_ok=True)


def build_one_short(args, spec: dict, stem: str, outdir: Path) -> Path:
    """단일 숏츠: 컷 → 세로 9:16 → (자막) → fade. 중간 임시파일 정리."""
    seg = {"start": spec["start"], "end": spec["end"]}
    raw = outdir / f".{stem}_raw.mp4"
    vert = outdir / f".{stem}_vert.mp4"
    subbed = outdir / f".{stem}_sub.mp4"
    final = outdir / f"{stem}.mp4"
    try:
        cut_video(args.input, [seg], raw)
        reframe_vertical(raw, vert, blur_bg=args.shorts_blur)

        cap = spec.get("caption", "").strip()
        if args.no_subtitle or not cap:
            faded_src = vert
        else:
            # 단일 클립이라 자막 window는 0~dur. segments는 길이만 맞으면 됨.
            # 세로 9:16(폭 1080)에서 자막이 좌우로 잘리지 않도록 줄바꿈 폭 지정.
            render_subtitled(
                cut_path=vert, captions=[cap], segments=[seg], output=subbed,
                font_size=args.sub_font_size + 8, margin_v=args.sub_margin_v + 120,
                max_caption_width=960,
            )
            faded_src = subbed
        apply_fade(faded_src, final, fade_in=0.3, fade_out=0.3)
        return final
    finally:
        for tmp in (raw, vert, subbed):
            tmp.unlink(missing_ok=True)


def build_thumbnail(args, segments: list, outdir: Path) -> list:
    """대표 구간들에서 후보 N장 추출. 1장이면 thumbnail.jpg, 여러 장이면 _NN."""
    times = pick_thumbnail_times(segments, args.thumbnail_count)
    paths = []
    for n, at in enumerate(times, 1):
        name = "thumbnail.jpg" if len(times) == 1 else f"thumbnail_{n:02d}.jpg"
        out = outdir / name
        extract_thumbnail(args.input, out, at, grade=not args.no_grade)
        paths.append(out)
    return paths


def build_intro(args, segments: list, outdir: Path) -> Path:
    """베스트 구간 앞 hook 클립 + fade (풀스크린 타이틀 카드 미사용)."""
    seg = pick_intro_segment(segments, args.intro_seconds)
    raw = outdir / ".intro_raw.mp4"
    final = outdir / "intro.mp4"
    try:
        cut_video(args.input, [seg], raw)
        apply_fade(raw, final, fade_in=0.4, fade_out=0.4)
        return final
    finally:
        raw.unlink(missing_ok=True)


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

    segments, captions = analyze(args, outdir)
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
         lambda: build_longform(args, segments, captions, outdir))
    step(f"[2/4] 숏츠 생성 (적정 길이 우선 상위 {args.shorts_count}개)…", "shorts",
         lambda: [build_one_short(args, s, f"shorts_{n:02d}", outdir)
                  for n, s in enumerate(
                      rank_for_shorts(segments, captions, args.shorts_count,
                                      args.shorts_max_seconds, args.shorts_ideal_seconds), 1)])
    step(f"[3/4] 썸네일 후보 {args.thumbnail_count}장 추출…", "thumbnail",
         lambda: build_thumbnail(args, segments, outdir))
    step("[4/4] 인트로 생성…", "intro",
         lambda: build_intro(args, segments, outdir))

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

    # 썸네일
    parser.add_argument("--thumbnail-count", type=int, default=3, help="썸네일 후보 장수")

    # 인트로
    parser.add_argument("--intro-seconds", type=float, default=4.0, help="인트로 hook 길이(초)")

    # 자막/효과
    parser.add_argument("--no-subtitle", action="store_true", help="자막 burn-in 생략")
    parser.add_argument("--no-grade", action="store_true", help="썸네일 컬러 그레이드 생략")
    parser.add_argument("--sub-font-size", type=int, default=56, help="자막 폰트 크기")
    parser.add_argument("--sub-margin-v", type=int, default=80, help="자막 하단 여백")

    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        sys.exit(str(e))
