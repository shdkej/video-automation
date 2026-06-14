"""자막 PNG 생성 + ffmpeg overlay 합성 (libass 우회).

libass/libfreetype/drawtext 없이 동작하도록 PIL로 자막 PNG를 그리고
ffmpeg overlay 필터의 enable=between(t, start, end)로 시간 구간별 합성.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from probe import probe_resolution


# ============================================================================
# Domain — 자막 한 장 그리기
# ============================================================================

FONT_DIR = Path(__file__).parent / "assets" / "fonts"


def find_korean_font() -> tuple[str, int]:
    """자막 폰트 (경로, ttc 인덱스). 프로젝트 동봉 Pretendard 우선.

    Pretendard Bold(단일 otf, index 0)를 1순위로 쓰고, 없으면 시스템 폰트로
    폴백한다. AppleSDGothicNeo.ttc는 index 6=Bold (0=Regular라 자막엔 약함)."""
    for fname in ("Pretendard-Bold.otf", "Pretendard-ExtraBold.otf"):
        bundled = FONT_DIR / fname
        if bundled.exists():
            return str(bundled), 0
    candidates = [
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 6),
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 0),
    ]
    for p, idx in candidates:
        if Path(p).exists():
            return p, idx
    try:
        out = subprocess.run(
            ["fc-list", ":lang=ko", "file"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        if out:
            return out[0].split(":")[0].strip(), 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    raise RuntimeError("한글 폰트를 찾지 못함. Pretendard 동봉 또는 fc-list :lang=ko 결과 필요.")


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """어절(공백) 단위로 max_width(px)를 넘지 않게 줄바꿈. 세로 숏츠 자막 잘림 방지."""
    words = text.split()
    if not words:
        return [text]
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        bbox = font.getbbox(trial)
        if (bbox[2] - bbox[0]) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_caption_png(
    text: str,
    out_path: Path,
    font_path: str,
    font_size: int = 44,
    pad_x: int = 32,
    pad_y: int = 18,
    bg_rgba: tuple = (0, 0, 0, 0),
    fg_rgba: tuple = (255, 255, 255, 255),
    stroke_width: int = 4,
    stroke_rgba: tuple = (0, 0, 0, 255),
    shadow_offset: tuple = (2, 3),
    shadow_rgba: tuple = (0, 0, 0, 140),
    font_index: int = 0,  # 폰트 weight는 find_korean_font가 결정해 넘긴다
    max_width: int | None = None,
    line_gap: int = 10,
) -> None:
    """캡션을 흰 글씨 + 검은 외곽선 + 그림자 PNG로 저장 (유튜브 숏츠풍).

    bg_rgba 알파를 0보다 크게 주면 글자 뒤 박스도 함께 그린다(둥근 박스 옵션 등).
    max_width(px)를 주면 어절 단위로 자동 줄바꿈(여러 줄)한다.
    세로 9:16 숏츠처럼 폭이 좁은 화면에서 자막이 좌우로 잘리는 것을 막는다.
    """
    try:
        font = ImageFont.truetype(font_path, font_size, index=font_index)
    except (OSError, IndexError):
        font = ImageFont.truetype(font_path, font_size)

    lines = wrap_text(text, font, max_width) if max_width else [text]
    metrics = [font.getbbox(ln) for ln in lines]
    line_hs = [b[3] - b[1] for b in metrics]
    text_w = max((b[2] - b[0]) for b in metrics)
    total_text_h = sum(line_hs) + line_gap * (len(lines) - 1)

    # 외곽선·그림자가 캔버스 밖으로 잘리지 않도록 여백 확보
    sx = stroke_width + max(0, shadow_offset[0])
    sy = stroke_width + max(0, shadow_offset[1])
    W = text_w + pad_x * 2 + sx * 2
    H = total_text_h + pad_y * 2 + 8 + sy * 2
    img = Image.new("RGBA", (W, H), bg_rgba)
    draw = ImageDraw.Draw(img)
    y = pad_y + sy
    for ln, b, lh in zip(lines, metrics, line_hs):
        lw = b[2] - b[0]
        x = (W - lw) // 2 - b[0]  # 줄마다 가운데 정렬
        if shadow_rgba[3] > 0:
            draw.text(
                (x + shadow_offset[0], y - b[1] + shadow_offset[1]),
                ln, font=font, fill=shadow_rgba,
            )
        draw.text(
            (x, y - b[1]), ln, font=font, fill=fg_rgba,
            stroke_width=stroke_width, stroke_fill=stroke_rgba,
        )
        y += lh + line_gap
    img.save(out_path)


# ============================================================================
# Service — ffmpeg overlay 합성
# ============================================================================

def render_subtitled(
    cut_path: Path,
    captions: list[str],
    segments: list[dict],
    output: Path,
    font_path: str | None = None,
    font_size: int = 44,
    margin_v: int = 80,
    bg_rgba: tuple = (0, 0, 0, 0),
    fg_rgba: tuple = (255, 255, 255, 255),
    pad_x: int = 32,
    pad_y: int = 18,
    max_caption_width: int | None = None,
    work_dir: Path | None = None,
    windows: list[tuple[float, float]] | None = None,
) -> dict:
    """컷 영상에 클립별 캡션을 overlay로 burn-in.

    segments는 selection.json 그대로 (start/end는 원본 시점, 합치면 컷 영상 타임라인).
    captions는 segments와 같은 길이.
    windows를 직접 주면 누적 계산을 생략하고 그 (start,end)를 자막 표시 구간으로 쓴다
    (xfade로 클립이 겹쳐 단순 누적과 어긋나는 경우).

    스타일 옵션:
    - font_size: 폰트 크기 (1080p 기준 44 적당)
    - margin_v: 하단 여백
    - bg_rgba/fg_rgba: 박스/글씨 색 (RGBA)
    - pad_x/pad_y: 글씨 주위 박스 패딩

    Returns: {"output", "srt", "png_paths"}
    """
    if len(captions) != len(segments):
        raise ValueError(f"captions({len(captions)}) ≠ segments({len(segments)})")
    if font_path is None:
        font_path, font_index = find_korean_font()
    else:
        font_index = 0
    # 줄바꿈 폭 미지정 시 영상 폭의 88%로 자동 설정 (긴 자막이 화면 밖으로 잘리는 것 방지)
    if max_caption_width is None:
        video_w, _ = probe_resolution(cut_path)
        max_caption_width = int(video_w * 0.88)

    work_dir = work_dir or Path(f"/tmp/vc_subs_{output.stem}")
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1) 자막 PNG N장
        png_paths = []
        for i, cap in enumerate(captions):
            p = work_dir / f"sub_{i:02d}.png"
            render_caption_png(
                cap, p, font_path,
                font_size=font_size, pad_x=pad_x, pad_y=pad_y,
                bg_rgba=bg_rgba, fg_rgba=fg_rgba,
                font_index=font_index,
                max_width=max_caption_width,
            )
            png_paths.append(p)

        # 2) 컷 영상 타임라인 window 계산 (직접 주어지면 그대로 사용)
        if windows is None:
            windows = []
            t = 0.0
            for seg in segments:
                d = seg["end"] - seg["start"]
                windows.append((t, t + d))
                t += d

        # 3) filter_complex 체인 구성
        inputs = ["-i", str(cut_path)]
        for p in png_paths:
            inputs += ["-i", str(p)]
        chain = []
        prev = "[0:v]"
        for i, (start, end) in enumerate(windows):
            out = f"[v{i+1}]"
            chain.append(
                f"{prev}[{i+1}:v]overlay=x=(W-w)/2:y=H-h-{margin_v}:"
                f"enable='between(t,{start:.2f},{end:.2f})'{out}"
            )
            prev = out

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(chain),
            "-map", prev, "-map", "0:a?",
            "-c:a", "copy",
            str(output),
        ]
        subprocess.run(cmd, check=True)

        # 4) SRT도 같이 (외부 플레이어용)
        srt_path = output.with_suffix(".srt")
        srt_path.write_text(_build_srt(windows, captions), encoding="utf-8")

        return {"output": str(output), "srt": str(srt_path), "png_paths": [str(p) for p in png_paths]}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _fmt_srt_ts(s: float) -> str:
    ms = int(s * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _build_srt(windows: list[tuple[float, float]], captions: list[str]) -> str:
    lines = []
    for i, ((start, end), cap) in enumerate(zip(windows, captions), 1):
        lines += [str(i), f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}", cap, ""]
    return "\n".join(lines)


def build_srt_from_events(events: list[dict]) -> str:
    """events([{text, start, end}], 0기준 타임라인) → SRT 문자열.

    자막을 영상에 burn-in하지 않아도(또는 remotion 엔진이어도) 동일 타이밍의
    자막 파일을 영상과 별도로 export하기 위한 공용 빌더.
    """
    lines = []
    n = 0
    for e in events:
        text = (e.get("text") or "").strip()
        if not text:
            continue
        n += 1
        lines += [str(n), f"{_fmt_srt_ts(e['start'])} --> {_fmt_srt_ts(e['end'])}", text, ""]
    return "\n".join(lines)


def _fmt_ass_ts(s: float) -> str:
    """ASS 시간포맷 H:MM:SS.cs (centiseconds)."""
    cs = int(round(s * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    sec, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{sec:02d}.{cs:02d}"


def build_ass_from_events(
    events: list[dict],
    width: int,
    height: int,
    font_size: int = 44,
    margin_v: int = 80,
    font_name: str = "Pretendard",
    outline: int = 4,
) -> str:
    """events → ASS(Advanced SubStation) 문자열.

    SRT와 달리 폰트·색·외곽선·위치 스타일을 담아 캡컷/Premiere에서 룩을 유지한 채
    편집할 수 있다. burn-in이 아니라 export 전용(ffmpeg libass 불요).
    PIL/remotion 자막 룩(흰 글씨·검정 외곽선·하단중앙)과 맞춘다.

    색은 ASS의 &HAABBGGRR 표기: PrimaryColour 흰색, OutlineColour 검정.
    Alignment 2 = 하단중앙, MarginV = 하단여백.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,"
        f"60,60,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for e in events:
        text = (e.get("text") or "").strip()
        if not text:
            continue
        # ASS 줄바꿈은 \N, 원치 않는 줄바꿈 문자는 정리
        text = text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_fmt_ass_ts(e['start'])},{_fmt_ass_ts(e['end'])},"
            f"Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


# ============================================================================
# Controller — CLI
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="컷 영상에 자막 PNG overlay")
    parser.add_argument("cut_path", type=Path, help="컷 영상")
    parser.add_argument("selection_json", type=Path, help="selection.json 경로")
    parser.add_argument("captions_json", type=Path, help='["캡션1", "캡션2", ...] 형식 JSON')
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--font-size", type=int, default=44)
    args = parser.parse_args()

    segments = json.loads(args.selection_json.read_text())
    captions = json.loads(args.captions_json.read_text())
    output = args.output or args.cut_path.with_name(args.cut_path.stem + "_subbed.mp4")

    result = render_subtitled(
        cut_path=args.cut_path,
        captions=captions,
        segments=segments,
        output=output,
        font_size=args.font_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
