#!/usr/bin/env python3
"""auto_cut.py — 긴 영상을 LLM 하이라이트 선정으로 자동 컷.

파이프라인: Whisper(트랜스크립트) → Claude(구간 선정) → ffmpeg(컷+concat)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ============================================================================
# Domain — 외부 의존 없는 순수 함수
# ============================================================================

def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def validate_segments(raw_segments: list, video_duration: float) -> list:
    valid = []
    for seg in raw_segments:
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end > video_duration + 0.5 or start >= end:
            continue
        valid.append({
            "start": start,
            "end": min(end, video_duration),
            "reason": str(seg.get("reason", "")),
        })
    valid.sort(key=lambda s: s["start"])
    return valid


def total_duration(segments: list) -> float:
    return sum(s["end"] - s["start"] for s in segments)


def validate_transcript_quality(transcript: dict, min_chars: int = 30, min_chars_per_sec: float = 0.3) -> None:
    """한국어 음성이 충분히 인식되었는지 검사. 부적절하면 ValueError."""
    segs = transcript["segments"]
    duration = transcript["duration"]
    total_text = "".join(s["text"] for s in segs).strip()

    if not segs or len(total_text) < min_chars:
        raise ValueError(
            f"트랜스크립트에 한국어 음성이 거의 인식되지 않았습니다 "
            f"(영상 {duration:.0f}초, 인식 텍스트 {len(total_text)}자). "
            f"무음 영상이거나 한국어가 아닐 수 있습니다."
        )

    char_per_sec = len(total_text) / duration if duration else 0.0
    if char_per_sec < min_chars_per_sec:
        raise ValueError(
            f"트랜스크립트가 너무 희박합니다 ({char_per_sec:.2f}자/초, 임계 {min_chars_per_sec}). "
            f"한국어 음성이 거의 없는 영상일 수 있습니다."
        )

    unique = {s["text"].strip() for s in segs if s["text"].strip()}
    if len(unique) <= 1 and len(segs) >= 3:
        sample = next(iter(unique), "")
        raise ValueError(
            f"같은 텍스트만 {len(segs)}회 반복됩니다 ({sample!r}). Whisper 환각 의심."
        )


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def filter_grounded_segments(llm_segments: list, transcript_segments: list) -> list:
    """LLM이 반환한 구간 중 트랜스크립트 segment 하나라도 겹치는 것만 채택."""
    grounded = []
    for seg in llm_segments:
        for t in transcript_segments:
            if overlaps(seg["start"], seg["end"], t["start"], t["end"]):
                grounded.append(seg)
                break
    return grounded


def extract_json_block(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"JSON 블록을 찾지 못함:\n{text}")
    return json.loads(text[start:end])


# ============================================================================
# Service — 비즈니스 로직 조합
# ============================================================================

def transcribe_video(video_path: Path, model_size: str, language: str) -> dict:
    from faster_whisper import WhisperModel

    print(f"[1/3] 트랜스크립트 추출 (모델={model_size}, 언어={language})…")
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments, info = model.transcribe(
        str(video_path),
        language=language if language != "auto" else None,
    )

    transcript_segments = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in segments
    ]
    return {
        "duration": float(info.duration),
        "language": info.language,
        "segments": transcript_segments,
    }


def detect_provider(model: str) -> str:
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    raise ValueError(
        f"알 수 없는 모델: {model!r} (claude-*, gpt-*, o1/o3/o4-* 만 지원)"
    )


def build_highlight_prompt(transcript: dict, target_minutes: float) -> str:
    transcript_text = "\n".join(
        f"[{format_timestamp(s['start'])}-{format_timestamp(s['end'])}] {s['text']}"
        for s in transcript["segments"]
    )
    target_seconds = target_minutes * 60
    duration = transcript["duration"]
    return f"""다음은 {duration / 60:.1f}분짜리 영상의 트랜스크립트입니다. 각 줄은 [시작-끝] 텍스트 형식입니다.

이 영상에서 핵심적이고 흥미로운 부분만 골라 총 {target_minutes:.1f}분(약 {target_seconds:.0f}초) 분량의 하이라이트를 만들어주세요.

선정 기준:
- 핵심 메시지, 결론, 임팩트 있는 발언, 흥미로운 일화 우선
- 침묵, 잡음, 반복적인 부분, 의미 없는 추임새 제외
- 시간순으로 자연스럽게 이어지도록
- 각 구간은 최소 5초 이상, 너무 잘게 자르지 말 것
- start/end는 트랜스크립트의 타임스탬프(초)를 그대로 사용

응답은 반드시 아래 JSON 포맷만 (다른 설명 없이):
{{
  "segments": [
    {{"start": <시작_초>, "end": <끝_초>, "reason": <짧은_선정_이유>}}
  ]
}}
숫자는 반드시 위 트랜스크립트에 등장한 타임스탬프 범위 안에서 골라야 합니다.

트랜스크립트:
{transcript_text}
"""


def call_anthropic(model: str, prompt: str) -> str:
    from anthropic import Anthropic

    response = Anthropic().messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def call_openai(model: str, prompt: str) -> str:
    from openai import OpenAI

    response = OpenAI().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def select_highlights(transcript: dict, target_minutes: float, model: str) -> list:
    provider = detect_provider(model)
    print(f"[2/3] {provider}/{model}로 하이라이트 선정 (목표={target_minutes}분)…")

    prompt = build_highlight_prompt(transcript, target_minutes)
    text = call_anthropic(model, prompt) if provider == "anthropic" else call_openai(model, prompt)

    data = extract_json_block(text)
    return validate_segments(data.get("segments", []), transcript["duration"])


def cut_video(video_path: Path, segments: list, output_path: Path) -> None:
    print(f"[3/3] ffmpeg로 {len(segments)}개 구간 자르고 합치는 중…")
    tmpdir = output_path.parent / f".{output_path.stem}_tmp"
    tmpdir.mkdir(exist_ok=True)

    try:
        clip_paths = []
        for i, seg in enumerate(segments):
            clip_path = tmpdir / f"clip_{i:03d}.mp4"
            duration = seg["end"] - seg["start"]
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-ss", f"{seg['start']:.3f}",
                    "-i", str(video_path),
                    "-t", f"{duration:.3f}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    "-avoid_negative_ts", "make_zero",
                    str(clip_path),
                ],
                check=True,
            )
            clip_paths.append(clip_path)

        list_file = tmpdir / "concat.txt"
        list_file.write_text("\n".join(f"file '{p.name}'" for p in clip_paths))

        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(output_path),
            ],
            check=True,
        )
    finally:
        for p in tmpdir.glob("*"):
            p.unlink()
        tmpdir.rmdir()


# ============================================================================
# Controller — 비즈니스 플로우
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="긴 영상을 LLM 하이라이트 기반으로 자동 컷",
    )
    parser.add_argument("input", type=Path, help="입력 영상 경로")
    parser.add_argument("-o", "--output", type=Path, help="출력 경로 (기본: <input>_cut.mp4)")
    parser.add_argument("-t", "--target-minutes", type=float, default=10.0, help="목표 길이(분)")
    parser.add_argument(
        "-m", "--whisper-model", default="small",
        help="Whisper 모델: tiny/base/small/medium/large-v3",
    )
    parser.add_argument("--language", default="ko", help="언어 코드 (auto 가능)")
    parser.add_argument(
        "--llm-model", default=None,
        help="하이라이트 선정용 LLM (claude-*/gpt-*/o3-*). "
             "기본: ANTHROPIC_API_KEY 있으면 claude-sonnet-4-6, 아니면 gpt-4o-mini",
    )
    parser.add_argument("--cache", action="store_true", help="트랜스크립트 캐시 재사용")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="컷 단계 생략, 트랜스크립트와 선정 결과만 저장",
    )
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"입력 파일 없음: {args.input}")

    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    if args.llm_model is None:
        if has_anthropic:
            args.llm_model = "claude-sonnet-4-6"
        elif has_openai:
            args.llm_model = "gpt-4o-mini"
        else:
            sys.exit("ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 환경 변수가 필요합니다.")

    provider = detect_provider(args.llm_model)
    if provider == "anthropic" and not has_anthropic:
        sys.exit(f"{args.llm_model} 사용에는 ANTHROPIC_API_KEY가 필요합니다.")
    if provider == "openai" and not has_openai:
        sys.exit(f"{args.llm_model} 사용에는 OPENAI_API_KEY가 필요합니다.")

    output = args.output or args.input.with_name(args.input.stem + "_cut.mp4")
    transcript_path = args.input.with_suffix(".transcript.json")
    selection_path = args.input.with_suffix(".selection.json")

    if args.cache and transcript_path.exists():
        print(f"[1/3] 캐시 로드: {transcript_path}")
        transcript = json.loads(transcript_path.read_text())
    else:
        transcript = transcribe_video(args.input, args.whisper_model, args.language)
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    try:
        validate_transcript_quality(transcript)
    except ValueError as e:
        sys.exit(f"트랜스크립트 품질 미달: {e}")

    raw = select_highlights(transcript, args.target_minutes, args.llm_model)
    segments = filter_grounded_segments(raw, transcript["segments"])
    if not segments:
        sys.exit(
            "선정된 구간이 트랜스크립트와 겹치지 않습니다 (LLM 환각 의심). "
            "원본 응답은 콘솔에서 확인하거나, --llm-model을 더 큰 모델로 바꿔보세요."
        )
    if len(segments) < len(raw):
        print(f"  ⚠ LLM 응답 {len(raw)}개 중 {len(raw) - len(segments)}개를 환각으로 판단해 제외")

    selection_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2))

    actual_minutes = total_duration(segments) / 60
    print(f"  → {len(segments)}개 구간, 총 {actual_minutes:.1f}분 선정")

    if args.dry_run:
        print(f"dry-run 종료. 선정 결과: {selection_path}")
        return

    cut_video(args.input, segments, output)
    print(f"\n완료: {output}")
    print(f"  - 트랜스크립트: {transcript_path}")
    print(f"  - 선정 결과:   {selection_path}")


if __name__ == "__main__":
    main()
