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


# ============================================================================
# Domain — 자막 한 장 그리기
# ============================================================================

def find_korean_font() -> str:
    """시스템 한글 폰트 경로 반환. macOS → AppleSDGothicNeo, 그 외 → fc-list."""
    candidates = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    try:
        out = subprocess.run(
            ["fc-list", ":lang=ko", "file"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        if out:
            return out[0].split(":")[0].strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    raise RuntimeError("한글 폰트를 찾지 못함. AppleSDGothicNeo 또는 fc-list :lang=ko 결과 필요.")


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
    font_size: int = 56,
    pad_x: int = 32,
    pad_y: int = 18,
    bg_rgba: tuple = (0, 0, 0, 200),
    fg_rgba: tuple = (255, 255, 255, 255),
    font_index: int = 8,
    max_width: int | None = None,
    line_gap: int = 10,
) -> None:
    """캡션을 검은 반투명 박스 + 흰 글씨 PNG로 저장.

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

    W = text_w + pad_x * 2
    H = total_text_h + pad_y * 2 + 8
    img = Image.new("RGBA", (W, H), bg_rgba)
    draw = ImageDraw.Draw(img)
    y = pad_y
    for ln, b, lh in zip(lines, metrics, line_hs):
        lw = b[2] - b[0]
        x = (W - lw) // 2 - b[0]  # 줄마다 가운데 정렬
        draw.text((x, y - b[1]), ln, font=font, fill=fg_rgba)
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
    font_size: int = 56,
    margin_v: int = 80,
    bg_rgba: tuple = (0, 0, 0, 200),
    fg_rgba: tuple = (255, 255, 255, 255),
    pad_x: int = 32,
    pad_y: int = 18,
    max_caption_width: int | None = None,
    work_dir: Path | None = None,
) -> dict:
    """컷 영상에 클립별 캡션을 overlay로 burn-in.

    segments는 selection.json 그대로 (start/end는 원본 시점, 합치면 컷 영상 타임라인).
    captions는 segments와 같은 길이.

    스타일 옵션:
    - font_size: 폰트 크기 (1080p 기준 56 적당)
    - margin_v: 하단 여백
    - bg_rgba/fg_rgba: 박스/글씨 색 (RGBA)
    - pad_x/pad_y: 글씨 주위 박스 패딩

    Returns: {"output", "srt", "png_paths"}
    """
    if len(captions) != len(segments):
        raise ValueError(f"captions({len(captions)}) ≠ segments({len(segments)})")
    if font_path is None:
        font_path = find_korean_font()

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
                max_width=max_caption_width,
            )
            png_paths.append(p)

        # 2) 컷 영상 타임라인 window 계산
        durations = [seg["end"] - seg["start"] for seg in segments]
        windows = []
        t = 0.0
        for d in durations:
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


def _build_srt(windows: list[tuple[float, float]], captions: list[str]) -> str:
    def fmt(s: float) -> str:
        ms = int(s * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        sec, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    lines = []
    for i, ((start, end), cap) in enumerate(zip(windows, captions), 1):
        lines += [str(i), f"{fmt(start)} --> {fmt(end)}", cap, ""]
    return "\n".join(lines)


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
    parser.add_argument("--font-size", type=int, default=56)
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
