#!/usr/bin/env python3
"""auto_cut.py — 긴 영상을 자동 컷.

speech 모드: Whisper(트랜스크립트) → LLM(구간 선정) → ffmpeg(컷+concat)
scene 모드:  ffmpeg scene 감지 → 점수 상위 컷 선택 → ffmpeg(컷+concat)
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


def parse_scene_metadata(stderr_text: str) -> list:
    """ffmpeg metadata=print 출력에서 (pts_time, scene_score) 쌍 추출."""
    scenes = []
    current_time = None
    for line in stderr_text.splitlines():
        if "pts_time:" in line:
            try:
                current_time = float(line.split("pts_time:")[1].split()[0])
            except (IndexError, ValueError):
                current_time = None
        elif "lavfi.scene_score=" in line and current_time is not None:
            try:
                score = float(line.split("lavfi.scene_score=")[1].strip())
                scenes.append((current_time, score))
            except ValueError:
                pass
            current_time = None
    return scenes


def pick_scene_segments(
    scenes: list,
    video_duration: float,
    target_seconds: float,
    clip_seconds: float,
) -> list:
    """씬 체인지 상위 점수부터 clip_seconds 길이 클립 선택. 겹치면 스킵."""
    picked = []
    total = 0.0
    for ts, score in sorted(scenes, key=lambda s: -s[1]):
        if total >= target_seconds:
            break
        start = max(0.0, ts - clip_seconds / 2)
        end = min(video_duration, start + clip_seconds)
        if end - start < 1.0:
            continue
        if any(overlaps(start, end, p["start"], p["end"]) for p in picked):
            continue
        picked.append({"start": start, "end": end, "reason": f"scene_score={score:.3f}"})
        total += end - start
    picked.sort(key=lambda s: s["start"])
    return picked


def format_mmss(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def calculate_mosaic_layout(duration: float) -> tuple:
    """영상 길이 → (cols, rows, interval_sec). 약 25~64 셀, 정사각 근사."""
    target_cells = max(16, min(64, int(duration / 20)))
    cols = max(4, int(target_cells ** 0.5))
    rows = (target_cells + cols - 1) // cols
    interval = duration / (cols * rows)
    return cols, rows, interval


def sample_mosaic_times(cols: int, rows: int, interval: float) -> list:
    """각 셀의 중심 시점(초) 리스트, 좌상→우하 순서."""
    return [(i + 0.5) * interval for i in range(cols * rows)]


def build_vision_prompt(
    duration: float, cols: int, rows: int, times: list, target_picks: int,
) -> str:
    grid_desc = "\n".join(
        f"행 {r+1}: " + ", ".join(
            format_mmss(times[r * cols + c]) for c in range(cols)
        )
        for r in range(rows)
    )
    return f"""아래 이미지는 {duration/60:.1f}분짜리 영상의 시점별 스냅샷을 {cols}열×{rows}행 그리드로 합친 것입니다.
좌상단부터 왼→오, 위→아래 순서로 각 셀의 중심 시점은:

{grid_desc}

이 영상에서 가장 흥미롭거나 핵심적인 장면 {target_picks}개를 골라주세요.
- 시각적으로 변화가 있거나, 인물/객체가 명확하거나, 구도가 인상적인 장면 우선
- 중복되거나 비슷한 장면은 제외
- 시간순으로 자연스럽게 분포

응답은 반드시 아래 JSON 포맷만 (다른 설명 없이):
{{
  "picks": [
    {{"time_sec": <초>, "reason": "<짧은_이유>"}}
  ]
}}
time_sec은 위에 명시된 시점을 초 단위로 환산한 값이어야 합니다 (예: 행1 두번째 칸이 0:30이면 30).
"""


def pick_vision_segments(picks: list, video_duration: float, clip_seconds: float) -> list:
    """LLM picks → 클립 구간 (겹치면 스킵)."""
    picked = []
    for p in picks:
        try:
            ts = float(p["time_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts < 0 or ts > video_duration:
            continue
        start = max(0.0, ts - clip_seconds / 2)
        end = min(video_duration, start + clip_seconds)
        if end - start < 1.0:
            continue
        if any(overlaps(start, end, x["start"], x["end"]) for x in picked):
            continue
        picked.append({
            "start": start, "end": end,
            "reason": str(p.get("reason", "")),
        })
    picked.sort(key=lambda s: s["start"])
    return picked


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


def get_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def detect_scene_changes(video_path: Path, threshold: float) -> list:
    print(f"[1/2] ffmpeg로 씬 체인지 감지 (threshold={threshold})…")
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path),
         "-filter:v", f"select='gt(scene,{threshold})',metadata=print",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return parse_scene_metadata(result.stderr)


def build_mosaic_image(video_path: Path, output_path: Path, cols: int, rows: int, interval: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(video_path),
         "-vf", f"fps=1/{interval},scale=320:180,tile={cols}x{rows}",
         "-frames:v", "1", "-q:v", "3",
         str(output_path)],
        check=True,
    )


def call_anthropic_vision(model: str, prompt: str, image_path: Path) -> str:
    import base64
    from anthropic import Anthropic

    image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    response = Anthropic().messages.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": image_data,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.content[0].text


def call_openai_vision(model: str, prompt: str, image_path: Path) -> str:
    import base64
    from openai import OpenAI

    image_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    response = OpenAI().chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_data}",
                }},
            ],
        }],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def select_vision_segments(
    video_path: Path, duration: float, model: str,
    target_minutes: float, clip_seconds: float,
) -> tuple:
    cols, rows, interval = calculate_mosaic_layout(duration)
    times = sample_mosaic_times(cols, rows, interval)
    target_picks = max(3, int((target_minutes * 60) / clip_seconds))

    mosaic_path = video_path.with_suffix(".mosaic.jpg")
    print(f"[1/2] 모자이크 추출 ({cols}×{rows}={cols*rows}컷, {interval:.1f}초 간격) → {mosaic_path.name}")
    build_mosaic_image(video_path, mosaic_path, cols, rows, interval)

    provider = detect_provider(model)
    print(f"[2/2] {provider}/{model} 비전으로 {target_picks}개 장면 선정…")
    prompt = build_vision_prompt(duration, cols, rows, times, target_picks)
    text = (
        call_anthropic_vision(model, prompt, mosaic_path) if provider == "anthropic"
        else call_openai_vision(model, prompt, mosaic_path)
    )
    data = extract_json_block(text)
    return pick_vision_segments(data.get("picks", []), duration, clip_seconds), mosaic_path


def cut_video(video_path: Path, segments: list, output_path: Path) -> None:
    print(f"ffmpeg로 {len(segments)}개 구간 자르고 합치는 중…")
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

def resolve_llm_model(args) -> None:
    """args.llm_model 결정 + API key 검증."""
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


def run_vision_mode(args, output: Path, selection_path: Path) -> None:
    resolve_llm_model(args)
    duration = get_video_duration(args.input)
    segments, mosaic_path = select_vision_segments(
        args.input, duration, args.llm_model,
        args.target_minutes, args.clip_seconds,
    )
    if not segments:
        sys.exit("LLM이 선정한 장면이 없습니다. 모델을 더 큰 것으로 바꿔보세요.")

    selection_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2))
    actual_minutes = total_duration(segments) / 60
    print(f"  → {len(segments)}개 장면 선정, 총 {actual_minutes:.1f}분")

    if args.dry_run:
        print(f"dry-run 종료. 선정 결과: {selection_path}, 모자이크: {mosaic_path}")
        return

    cut_video(args.input, segments, output)
    print(f"\n완료: {output}")
    print(f"  - 모자이크:    {mosaic_path}")
    print(f"  - 선정 결과:   {selection_path}")


def run_scene_mode(args, output: Path, selection_path: Path) -> None:
    duration = get_video_duration(args.input)
    scenes = detect_scene_changes(args.input, args.scene_threshold)
    segments = pick_scene_segments(
        scenes, duration, args.target_minutes * 60, args.clip_seconds,
    )
    if not segments:
        sys.exit(
            f"씬 체인지가 감지되지 않습니다 (threshold={args.scene_threshold}). "
            f"--scene-threshold를 더 낮게 (예: 0.1) 시도해보세요."
        )

    selection_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2))
    actual_minutes = total_duration(segments) / 60
    print(f"  → {len(scenes)}개 씬 중 {len(segments)}개 선정, 총 {actual_minutes:.1f}분")

    if args.dry_run:
        print(f"dry-run 종료. 선정 결과: {selection_path}")
        return

    cut_video(args.input, segments, output)
    print(f"\n완료: {output}")
    print(f"  - 선정 결과: {selection_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="긴 영상을 LLM 하이라이트 기반으로 자동 컷",
    )
    parser.add_argument("input", type=Path, help="입력 영상 경로")
    parser.add_argument("-o", "--output", type=Path, help="출력 경로 (기본: <input>_cut.mp4)")
    parser.add_argument("-t", "--target-minutes", type=float, default=10.0, help="목표 길이(분)")
    parser.add_argument(
        "--mode", choices=["speech", "scene", "vision"], default="speech",
        help="speech: 음성+LLM, scene: 씬 체인지 감지, vision: 모자이크 그리드 + 비전 LLM",
    )
    parser.add_argument(
        "--scene-threshold", type=float, default=0.3,
        help="scene 모드: ffmpeg scene 점수 임계값 (0~1, 낮을수록 민감)",
    )
    parser.add_argument(
        "--clip-seconds", type=float, default=6.0,
        help="scene 모드: 각 클립 길이(초)",
    )
    parser.add_argument(
        "-m", "--whisper-model", default="small",
        help="Whisper 모델: tiny/base/small/medium/large-v3 (speech 모드)",
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

    output = args.output or args.input.with_name(args.input.stem + "_cut.mp4")
    selection_path = args.input.with_suffix(".selection.json")

    if args.mode == "scene":
        run_scene_mode(args, output, selection_path)
        return
    if args.mode == "vision":
        run_vision_mode(args, output, selection_path)
        return

    resolve_llm_model(args)
    transcript_path = args.input.with_suffix(".transcript.json")

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
