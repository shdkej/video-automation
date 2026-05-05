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


def select_highlights(transcript: dict, target_minutes: float, model: str) -> list:
    from anthropic import Anthropic

    print(f"[2/3] Claude로 하이라이트 선정 (목표={target_minutes}분)…")
    client = Anthropic()

    transcript_text = "\n".join(
        f"[{format_timestamp(s['start'])}-{format_timestamp(s['end'])}] {s['text']}"
        for s in transcript["segments"]
    )
    target_seconds = target_minutes * 60
    duration = transcript["duration"]

    prompt = f"""다음은 {duration / 60:.1f}분짜리 영상의 트랜스크립트입니다. 각 줄은 [시작-끝] 텍스트 형식입니다.

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
    {{"start": 12.3, "end": 45.6, "reason": "핵심 주장 소개"}}
  ]
}}

트랜스크립트:
{transcript_text}
"""

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    data = extract_json_block(text)
    return validate_segments(data.get("segments", []), duration)


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
        "--llm-model", default="claude-sonnet-4-6",
        help="하이라이트 선정용 Claude 모델",
    )
    parser.add_argument("--cache", action="store_true", help="트랜스크립트 캐시 재사용")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="컷 단계 생략, 트랜스크립트와 선정 결과만 저장",
    )
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"입력 파일 없음: {args.input}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY 환경 변수가 필요합니다.")

    output = args.output or args.input.with_name(args.input.stem + "_cut.mp4")
    transcript_path = args.input.with_suffix(".transcript.json")
    selection_path = args.input.with_suffix(".selection.json")

    if args.cache and transcript_path.exists():
        print(f"[1/3] 캐시 로드: {transcript_path}")
        transcript = json.loads(transcript_path.read_text())
    else:
        transcript = transcribe_video(args.input, args.whisper_model, args.language)
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

    segments = select_highlights(transcript, args.target_minutes, args.llm_model)
    if not segments:
        sys.exit("선택된 구간이 없습니다. 트랜스크립트나 프롬프트를 점검하세요.")

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
