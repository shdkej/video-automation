"""영상 편집 효과 모음 — color grade, Ken Burns, title card, fade, BGM.

ffmpeg 자체 필터만 사용 (libass/libfreetype 불필요).
타이틀/엔딩 카드 텍스트 렌더링은 PIL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from probe import has_audio_stream, has_video_stream, probe_duration, probe_resolution
from subtitle import find_korean_font

__all__ = ["has_audio_stream", "has_video_stream", "probe_duration", "probe_resolution"]


# ============================================================================
# Multi-source concat — 여러 소스를 공통 규격으로 정규화 후 이어붙임
# ============================================================================

def concat_sources(inputs: list[Path], output: Path, fps: int = 30) -> None:
    """여러 영상 소스를 순서대로 이어붙인다.

    소스마다 해상도/fps/코덱/오디오 유무가 달라도 안전하도록, 각 소스를
    첫 소스 해상도에 맞춰 scale+pad·fps 통일·오디오 보장(없으면 무음 트랙)으로
    정규화한 뒤 concat demuxer로 합친다.
    """
    if len(inputs) == 1:
        shutil.copy(inputs[0], output)
        return

    w, h = probe_resolution(inputs[0])
    w -= w % 2  # libx264 yuv420p는 짝수 해상도 필요
    h -= h % 2

    tmpdir = output.parent / f".{output.stem}_src_tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1"
        )
        norm_paths = []
        for i, src in enumerate(inputs):
            norm = tmpdir / f"norm_{i:03d}.mp4"
            if has_audio_stream(src):
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(src), "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2",
                    str(norm),
                ]
            else:
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(src),
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-shortest",
                    str(norm),
                ]
            subprocess.run(cmd, check=True)
            norm_paths.append(norm)

        # 모두 동일 규격이라 재인코딩 없이 concat
        list_file = tmpdir / "concat.txt"
        list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in norm_paths))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", str(output)],
            check=True,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================================
# Color grade — 채도/감마/비네팅
# ============================================================================

def apply_color_grade(
    input_path: Path,
    output_path: Path,
    saturation: float = 1.15,
    gamma: float = 1.05,
    contrast: float = 1.03,
    vignette: bool = True,
) -> None:
    """따뜻한 룩: 채도 상승 + 감마 보정 + 가장자리 비네팅."""
    eq = f"eq=saturation={saturation}:gamma={gamma}:contrast={contrast}"
    vf = eq + (",vignette=PI/5" if vignette else "")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(input_path), "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "copy",
         str(output_path)],
        check=True,
    )


# ============================================================================
# Ken Burns — 마지막 N초에 슬로우 줌인
# ============================================================================

def apply_ken_burns_last(
    input_path: Path,
    output_path: Path,
    last_n_sec: float = 6.0,
    zoom_to: float = 1.08,
) -> None:
    """마지막 N초만 분리해 zoompan으로 줌인 후 재결합."""
    total = probe_duration(input_path)
    if total < last_n_sec + 0.5:
        # 너무 짧으면 전체에 적용
        head_path = None
        tail_start = 0.0
        tail_dur = total
    else:
        head_path = output_path.parent / f".{output_path.stem}_head.mp4"
        tail_start = total - last_n_sec
        tail_dur = last_n_sec
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(input_path), "-t", f"{tail_start:.3f}",
             "-c", "copy", str(head_path)],
            check=True,
        )

    tail_path = output_path.parent / f".{output_path.stem}_tail.mp4"
    fps = 30
    frames = int(tail_dur * fps)
    # zoompan: 첫 프레임 1.0 → 마지막 zoom_to까지 선형 증가
    z_expr = f"min(zoom+{(zoom_to-1.0)/frames:.6f},{zoom_to})"
    vf = (
        f"zoompan=z='{z_expr}':d={frames}:"
        f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
        f"s=1920x1080:fps={fps}"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{tail_start:.3f}", "-i", str(input_path),
         "-t", f"{tail_dur:.3f}",
         "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "copy",
         str(tail_path)],
        check=True,
    )

    if head_path:
        concat_videos([head_path, tail_path], output_path)
        head_path.unlink()
    else:
        tail_path.rename(output_path)
    if tail_path.exists():
        tail_path.unlink(missing_ok=True)


# ============================================================================
# Title/Outro card — PIL로 PNG 생성 후 ffmpeg loop로 mp4 클립
# ============================================================================

def make_text_card(
    text: str,
    output_path: Path,
    duration: float = 1.5,
    width: int = 1920,
    height: int = 1080,
    bg_rgb: tuple = (15, 15, 15),
    fg_rgb: tuple = (250, 235, 200),
    font_size: int = 110,
    subtitle: str = "",
    sub_font_size: int = 48,
    sub_fg_rgb: tuple = (180, 180, 180),
    fps: int = 30,
) -> None:
    """텍스트만 있는 풀스크린 카드 mp4 생성."""
    font_path = find_korean_font()
    try:
        font = ImageFont.truetype(font_path, font_size, index=8)
    except (OSError, IndexError):
        font = ImageFont.truetype(font_path, font_size)

    img = Image.new("RGB", (width, height), bg_rgb)
    draw = ImageDraw.Draw(img)
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    y = (height - th) // 2 - bbox[1]
    if subtitle:
        y -= 30
    draw.text(((width - tw) // 2 - bbox[0], y), text, font=font, fill=fg_rgb)

    if subtitle:
        try:
            sfont = ImageFont.truetype(font_path, sub_font_size, index=8)
        except (OSError, IndexError):
            sfont = ImageFont.truetype(font_path, sub_font_size)
        sbbox = sfont.getbbox(subtitle)
        stw = sbbox[2] - sbbox[0]
        sy = y + th + 30 - sbbox[1]
        draw.text(((width - stw) // 2 - sbbox[0], sy), subtitle, font=sfont, fill=sub_fg_rgb)

    png_path = output_path.with_suffix(".png")
    img.save(png_path)

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-loop", "1", "-i", str(png_path),
         "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
         "-t", f"{duration:.3f}",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", str(fps),
         "-c:a", "aac", "-shortest",
         str(output_path)],
        check=True,
    )
    png_path.unlink()


# ============================================================================
# Concat — concat demuxer (재인코딩 없음). 코덱 일치 필요시 filter_complex로
# ============================================================================

def concat_videos(clips: list[Path], output_path: Path, reencode: bool = True) -> None:
    """여러 mp4 합치기. 코덱이 다를 가능성 있으면 reencode=True."""
    if reencode:
        n = len(clips)
        inputs = []
        for c in clips:
            inputs += ["-i", str(c)]
        parts = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
        filter_complex = f"{parts}concat=n={n}:v=1:a=1[v][a]"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             *inputs,
             "-filter_complex", filter_complex,
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac",
             str(output_path)],
            check=True,
        )
    else:
        list_file = output_path.parent / f".{output_path.stem}_list.txt"
        list_file.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(output_path)],
            check=True,
        )
        list_file.unlink()


# ============================================================================
# Vertical reframe — 가로 영상을 숏폼 9:16로 (center-crop / blur 배경)
# ============================================================================

def reframe_vertical(
    input_path: Path,
    output_path: Path,
    target_w: int = 1080,
    target_h: int = 1920,
    blur_bg: bool = False,
) -> None:
    """가로(또는 임의 비율) 영상을 세로 9:16로 변환.

    blur_bg=False: 9:16을 가득 채우도록 확대 후 가운데를 crop (좌우가 잘림).
    blur_bg=True:  원본을 흐린 배경 위에 폭 맞춰 얹음 (잘림 없음, 레터박스 대체).
    """
    audio_map = ["-map", "0:a?"]  # 오디오 있으면 매핑, 없으면 조용히 생략
    if blur_bg:
        fc = (
            f"[0:v]split[a][b];"
            f"[a]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=24:2[bg];"
            f"[b]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
        )
        video_args = ["-filter_complex", fc, "-map", "[vout]", *audio_map]
    else:
        vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )
        video_args = ["-vf", vf, "-map", "0:v:0", *audio_map]
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(input_path), *video_args,
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "copy",
         str(output_path)],
        check=True,
    )


# ============================================================================
# Thumbnail — 특정 시점 프레임 1장을 JPG로
# ============================================================================

def extract_thumbnail(
    input_path: Path,
    output_path: Path,
    at_sec: float,
    grade: bool = True,
) -> None:
    """at_sec 시점의 프레임 1장을 고화질 JPG로 저장. grade=True면 채도/대비 살짝 상승."""
    vf = "eq=saturation=1.2:contrast=1.05:gamma=1.03" if grade else None
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{at_sec:.3f}", "-i", str(input_path)]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-frames:v", "1", "-q:v", "2", str(output_path)]
    subprocess.run(cmd, check=True)


# ============================================================================
# Fade in/out
# ============================================================================

def apply_fade(
    input_path: Path,
    output_path: Path,
    fade_in: float = 0.5,
    fade_out: float = 1.0,
) -> None:
    """시작 fade_in초 페이드인, 끝 fade_out초 페이드아웃."""
    total = probe_duration(input_path)
    # 클립이 짧으면 페이드가 전체를 덮지 않도록 길이의 40%로 상한
    budget = total * 0.4
    if fade_in + fade_out > budget:
        scale = budget / (fade_in + fade_out)
        fade_in, fade_out = fade_in * scale, fade_out * scale
    out_start = max(0.0, total - fade_out)
    vf = f"fade=in:st=0:d={fade_in},fade=out:st={out_start:.3f}:d={fade_out}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-i", str(input_path), "-vf", vf]
    if has_audio_stream(input_path):
        af = f"afade=in:st=0:d={fade_in},afade=out:st={out_start:.3f}:d={fade_out}"
        cmd += ["-af", af, "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", str(output_path)]
    subprocess.run(cmd, check=True)


# ============================================================================
# xfade — 클립 사이 부드러운 트랜지션 (자막 windows 재계산 필요)
# ============================================================================

def compute_xfade_windows(segments: list[dict], tdur: float = 0.3) -> list[tuple]:
    """xfade로 합쳤을 때 각 클립의 가시 시점(시작, 끝) 리스트.

    클립 i의 가시 시작 = i*(clip_dur - tdur), 가시 끝 = +clip_dur
    클립 길이가 가변일 때도 동작.
    """
    windows = []
    offset = 0.0
    for i, seg in enumerate(segments):
        dur = seg["end"] - seg["start"]
        windows.append((offset, offset + dur))
        offset += dur - tdur
    return windows


def cut_with_xfade(
    input_path: Path,
    segments: list[dict],
    output_path: Path,
    transition: str = "fade",
    tdur: float = 0.3,
) -> None:
    """원본에서 segments 자르고 xfade로 합침."""
    tmpdir = output_path.parent / f".{output_path.stem}_xfade_tmp"
    tmpdir.mkdir(exist_ok=True)
    try:
        clip_paths = []
        for i, seg in enumerate(segments):
            cp = tmpdir / f"clip_{i:03d}.mp4"
            dur = seg["end"] - seg["start"]
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", f"{seg['start']:.3f}", "-i", str(input_path),
                 "-t", f"{dur:.3f}",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-c:a", "aac", "-b:a", "192k",
                 str(cp)],
                check=True,
            )
            clip_paths.append(cp)

        # filter_complex로 xfade chain
        inputs = []
        for cp in clip_paths:
            inputs += ["-i", str(cp)]
        n = len(clip_paths)
        clip_durs = [s["end"] - s["start"] for s in segments]
        chain = []
        prev = "[0:v]"
        offset = clip_durs[0] - tdur
        for i in range(1, n):
            out = f"[v{i}]" if i < n - 1 else "[vout]"
            chain.append(
                f"{prev}[{i}:v]xfade=transition={transition}:duration={tdur}:offset={offset:.3f}{out}"
            )
            prev = out
            offset += clip_durs[i] - tdur

        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             *inputs,
             "-filter_complex", ";".join(chain),
             "-map", "[vout]",
             "-an",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             str(output_path)],
            check=True,
        )
    finally:
        for p in tmpdir.glob("*"):
            p.unlink()
        tmpdir.rmdir()


# ============================================================================
# BGM mux — 영상에 배경음 + (선택) 페이드아웃
# ============================================================================

def add_bgm(
    input_path: Path,
    bgm_path: Path,
    output_path: Path,
    bgm_volume: float = 0.3,
    fade_out: float = 2.0,
) -> None:
    """영상 길이에 맞춰 BGM 자르고 볼륨 조절 후 mux. 영상 오디오가 있으면 amix."""
    vdur = probe_duration(input_path)
    bgm_fade_start = max(0.0, vdur - fade_out)

    if has_audio_stream(input_path):
        filter_complex = (
            f"[1:a]volume={bgm_volume},"
            f"afade=out:st={bgm_fade_start:.3f}:d={fade_out},"
            f"atrim=duration={vdur:.3f}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    else:
        filter_complex = (
            f"[1:a]volume={bgm_volume},"
            f"afade=out:st={bgm_fade_start:.3f}:d={fade_out},"
            f"atrim=duration={vdur:.3f}[aout]"
        )

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(input_path), "-i", str(bgm_path),
         "-filter_complex", filter_complex,
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest",
         str(output_path)],
        check=True,
    )


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="영상 효과 단일 실행")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("grade"); p.add_argument("input", type=Path); p.add_argument("output", type=Path)
    p = sub.add_parser("kenburns"); p.add_argument("input", type=Path); p.add_argument("output", type=Path); p.add_argument("--last-n", type=float, default=6.0); p.add_argument("--zoom-to", type=float, default=1.08)
    p = sub.add_parser("card"); p.add_argument("text"); p.add_argument("output", type=Path); p.add_argument("--duration", type=float, default=1.5); p.add_argument("--subtitle", default="")
    p = sub.add_parser("fade"); p.add_argument("input", type=Path); p.add_argument("output", type=Path); p.add_argument("--fade-in", type=float, default=0.5); p.add_argument("--fade-out", type=float, default=1.0)
    p = sub.add_parser("bgm"); p.add_argument("input", type=Path); p.add_argument("bgm", type=Path); p.add_argument("output", type=Path); p.add_argument("--volume", type=float, default=0.3)
    p = sub.add_parser("concat"); p.add_argument("output", type=Path); p.add_argument("clips", type=Path, nargs="+")

    args = parser.parse_args()
    if args.cmd == "grade":
        apply_color_grade(args.input, args.output)
    elif args.cmd == "kenburns":
        apply_ken_burns_last(args.input, args.output, last_n_sec=args.last_n, zoom_to=args.zoom_to)
    elif args.cmd == "card":
        make_text_card(args.text, args.output, duration=args.duration, subtitle=args.subtitle)
    elif args.cmd == "fade":
        apply_fade(args.input, args.output, fade_in=args.fade_in, fade_out=args.fade_out)
    elif args.cmd == "bgm":
        add_bgm(args.input, args.bgm, args.output, bgm_volume=args.volume)
    elif args.cmd == "concat":
        concat_videos(args.clips, args.output)
    print(f"완료: {args.output if hasattr(args, 'output') else ''}")
