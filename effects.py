"""영상 편집 효과 모음 — color grade, Ken Burns, title card, fade, BGM.

ffmpeg 자체 필터만 사용 (libass/libfreetype 불필요).
타이틀/엔딩 카드 텍스트 렌더링은 PIL.
"""

from __future__ import annotations

import json
import math
import random
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
    font_path, font_index = find_korean_font()
    try:
        font = ImageFont.truetype(font_path, font_size, index=font_index)
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
            sfont = ImageFont.truetype(font_path, sub_font_size, index=font_index)
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


HOOK_POSITIONS = frozenset(
    f"{v}-{h}" for v in ("top", "middle", "bottom") for h in ("left", "center", "right")
)

# 썸네일 타이틀 폰트 — 전부 동봉(OFL/SIL), 키는 API·프론트와 공유
_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
THUMB_FONTS = {
    "pretendard": ("Pretendard-ExtraBold.otf", "프리텐다드"),
    "blackhan": ("BlackHanSans-Regular.ttf", "블랙한산스"),
    "dohyeon": ("DoHyeon-Regular.ttf", "도현"),
    "jua": ("Jua-Regular.ttf", "주아"),
    "nanumpen": ("NanumPenScript-Regular.ttf", "나눔손글씨"),
}


# 타이틀 굵기 — 외곽선 두께로 표현(단일 웨이트 폰트 공통), Pretendard는 파일도 교체
THUMB_WEIGHTS = {"normal": 16, "bold": 12, "heavy": 8}  # stroke = size // 값

# 타이틀 배경 효과 — 텍스트 뒤에 그려지는 정적 장식
THUMB_EFFECTS = ("none", "fireworks", "fire", "sparkle")

# 썸네일 타이틀 기본값 — 프론트·API·파이프라인 단일 출처
DEFAULT_THUMB_POS = "top-center"
DEFAULT_THUMB_SCALE = 1.5

# 커스텀(수동 조합)의 바탕 스타일 — 흰 글씨 + 검정 외곽선(현행 기본 룩).
# 좌표·두께류 값은 전부 폰트 크기(size) 비례의 em 단위.
DEFAULT_THUMB_STYLE = {
    "font": "pretendard",
    "weight": "bold",
    "fill": (255, 255, 255),              # 단색 RGB 또는 {"gradient": (상단색, 하단색)}
    "stroke": {"color": (0, 0, 0)},       # None이면 외곽선 없음, "k"로 두께(size//k) 지정
    "shadow": None,                       # {"dx","dy","blur"(em), "color","alpha"} — dx=dy=0이면 글로우
    "bg": None,                           # 줄 단위 배경 바 {"color"(RGBA), "pad_x","pad_y","radius"(em)}
    "tilt": 0,                            # 텍스트 블록 회전(도, 반시계 +)
    "effect": "none",                     # THUMB_EFFECTS — 텍스트 뒤 장식
    "scale_mul": 1.0,                     # 템플릿 고유 크기 보정 (사용자 scale에 곱)
}

# 썸네일 타이틀 템플릿 — 릴스·숏츠 트렌드의 스타일 번들 (폰트·색·외곽선·그림자·배경·틸트).
# 키는 API·프론트와 공유. 템플릿 선택 시 font/weight/effect 개별 값은 무시된다.
THUMB_TEMPLATES = {
    "highlight": {   # 형광펜 — 검정 글씨 + 옐로 하이라이트 바 (예능·정보 공용 국룰)
        "label": "형광펜", "font": "pretendard", "weight": "heavy",
        "fill": (20, 18, 14), "stroke": None,
        "bg": {"color": (255, 226, 0, 240), "pad_x": 0.24, "pad_y": 0.10, "radius": 0.08},
    },
    "variety": {     # 예능 정석 — 흰 글씨 + 두꺼운 검정 외곽선 + 낙하 그림자 (토크 예능 기본형)
        "label": "예능", "font": "jua", "weight": "heavy",
        "fill": (255, 255, 255), "stroke": {"color": (18, 14, 10), "k": 8},
        "shadow": {"dx": 0.05, "dy": 0.07, "blur": 0.02, "color": (0, 0, 0), "alpha": 230},
    },
    "yellow": {      # 옐로 후킹 — 형광 옐로 + 검정, 살짝 기울임 (유튜브 최고 대비 조합)
        "label": "옐로 후킹", "font": "blackhan", "weight": "heavy",
        "fill": (255, 225, 0), "stroke": {"color": (16, 14, 8)},
        "shadow": {"dx": 0.04, "dy": 0.06, "blur": 0.04, "color": (0, 0, 0), "alpha": 220},
        "tilt": -4, "scale_mul": 1.05,
    },
    "impact": {      # 임팩트 레드 — 초대형 레드 + 흰 외곽선 (충격·후킹형)
        "label": "임팩트 레드", "font": "blackhan", "weight": "heavy",
        "fill": (230, 28, 28), "stroke": {"color": (255, 255, 255)},
        "shadow": {"dx": 0.045, "dy": 0.06, "blur": 0.05, "color": (0, 0, 0), "alpha": 210},
        "scale_mul": 1.12,
    },
    "neon": {        # 네온 글로우 — 흰 코어 + 시안 발광 (게임·테크·나이트)
        "label": "네온", "font": "pretendard", "weight": "bold",
        "fill": (238, 255, 255), "stroke": {"color": (0, 190, 255), "k": 24},
        "shadow": {"dx": 0, "dy": 0, "blur": 0.16, "color": (0, 205, 255), "alpha": 255},
    },
    "sunset": {      # 선셋 그라디언트 — 오렌지→핑크 세로 그라디언트
        "label": "선셋", "font": "pretendard", "weight": "heavy",
        "fill": {"gradient": ((255, 158, 46), (255, 60, 120))}, "stroke": None,
        "shadow": {"dx": 0.03, "dy": 0.05, "blur": 0.07, "color": (30, 8, 20), "alpha": 200},
    },
    "vlog": {        # 감성 손글씨 — 외곽선 없이 여린 그림자 (브이로그·일상)
        "label": "감성 손글씨", "font": "nanumpen", "weight": "normal",
        "fill": (255, 252, 244), "stroke": None,
        "shadow": {"dx": 0.02, "dy": 0.03, "blur": 0.10, "color": (20, 16, 12), "alpha": 180},
        "tilt": -2, "scale_mul": 1.15,   # 손글씨는 같은 px에서 작아 보인다
    },
    "banner": {      # 자막바 — 반투명 검정 바 + 흰 글씨 (뉴스·정보형)
        "label": "자막바", "font": "dohyeon", "weight": "bold",
        "fill": (255, 255, 255), "stroke": None,
        "bg": {"color": (10, 9, 8, 205), "pad_x": 0.30, "pad_y": 0.14, "radius": 0.06},
    },
    "sticker": {     # Y2K 스티커 — 핑크 + 두꺼운 흰 다이컷 테두리 + 틸트 (밈·팬 콘텐츠)
        "label": "스티커", "font": "jua", "weight": "heavy",
        "fill": (255, 82, 148), "stroke": {"color": (255, 255, 255), "k": 7},
        "shadow": {"dx": 0.04, "dy": 0.06, "blur": 0.03, "color": (40, 8, 24), "alpha": 190},
        "tilt": -3,
    },
    "minimal": {     # 미니멀 — 가는 흰 글씨 + 부드러운 그림자 (감성·시네마틱)
        "label": "미니멀", "font": "pretendard", "weight": "normal",
        "fill": (255, 255, 255), "stroke": None,
        "shadow": {"dx": 0, "dy": 0.02, "blur": 0.09, "color": (0, 0, 0), "alpha": 160},
        "scale_mul": 0.85,
    },
    "comic": {       # 만화 팝 — 흰 글씨 + 검정 외곽선 + 딱딱한 3D 압출 그림자
        "label": "만화", "font": "jua", "weight": "heavy",
        "fill": (255, 255, 255), "stroke": {"color": (20, 16, 12), "k": 9},
        "shadow": {"dx": 0.09, "dy": 0.11, "blur": 0, "color": (20, 16, 12), "alpha": 255, "steps": 7},
        "tilt": 2,
    },
    "retro": {       # Y2K 레트로 — 크림 + 네이비/핑크 이중 외곽선 + 하드 섀도
        "label": "레트로", "font": "dohyeon", "weight": "bold",
        "fill": (255, 244, 210), "stroke": {"color": (44, 42, 96), "k": 14},
        "stroke2": {"color": (255, 120, 190), "k": 6},
        "shadow": {"dx": 0.07, "dy": 0.08, "blur": 0, "color": (44, 42, 96), "alpha": 235},
        "tilt": -2,
    },
    "fire": {        # 파이어 — 옐로→레드 그라디언트 + 불꽃 장식 (매운맛·도전)
        "label": "파이어", "font": "blackhan", "weight": "heavy",
        "fill": {"gradient": ((255, 232, 90), (255, 48, 24))},
        "stroke": {"color": (60, 10, 4), "k": 18},
        "effect": "fire", "scale_mul": 1.05,
    },
    "festa": {       # 축포 — 흰 글씨 + 골드 외곽선 + 폭죽 (축하·공개·기념)
        "label": "축포", "font": "pretendard", "weight": "heavy",
        "fill": (255, 255, 255), "stroke": {"color": (212, 160, 60), "k": 14},
        "shadow": {"dx": 0.02, "dy": 0.04, "blur": 0.06, "color": (60, 40, 8), "alpha": 180},
        "effect": "fireworks",
    },
    "ice": {         # 아이스 — 화이트→아이스블루 그라디언트 + 시안 글로우 (청량·겨울)
        "label": "아이스", "font": "blackhan", "weight": "bold",
        "fill": {"gradient": ((255, 255, 255), (140, 220, 255))},
        "stroke": {"color": (20, 90, 130), "k": 20},
        "shadow": {"dx": 0, "dy": 0, "blur": 0.14, "color": (120, 215, 255), "alpha": 240},
    },
    "grape": {       # 퍼플 네온 — 라벤더→퍼플 가로 그라디언트 + 마젠타 글로우 (뷰티·나이트)
        "label": "퍼플 네온", "font": "pretendard", "weight": "heavy",
        "fill": {"gradient": ((240, 220, 255), (186, 120, 255)), "dir": "h"},
        "stroke": {"color": (120, 40, 200), "k": 22},
        "shadow": {"dx": 0, "dy": 0, "blur": 0.15, "color": (255, 60, 220), "alpha": 235},
    },
    "breaking": {    # 속보 — 화면 풀폭 레드 바 + 흰 글씨 (뉴스·긴급)
        "label": "속보", "font": "dohyeon", "weight": "bold",
        "fill": (255, 255, 255), "stroke": None,
        "bg": {"color": (196, 24, 24, 235), "pad_x": 0.30, "pad_y": 0.14, "radius": 0, "full": True},
    },
    "polaroid": {    # 폴라로이드 — 흰 라벨지 + 검정 손글씨 + 틸트 (일상·기록)
        "label": "폴라로이드", "font": "nanumpen", "weight": "normal",
        "fill": (30, 26, 22), "stroke": None,
        "bg": {"color": (255, 253, 248, 240), "pad_x": 0.34, "pad_y": 0.10, "radius": 0.05},
        "tilt": -2, "scale_mul": 1.1,
    },
    "mint": {        # 민트 — 민트 + 딥틸 외곽선 + 흰 하드 섀도 (여름·리프레시)
        "label": "민트", "font": "jua", "weight": "heavy",
        "fill": (64, 232, 190), "stroke": {"color": (8, 68, 60), "k": 12},
        "shadow": {"dx": 0.05, "dy": 0.06, "blur": 0, "color": (255, 255, 255), "alpha": 235},
        "tilt": 2,
    },
    "poster": {      # 롱섀도 포스터 — 흰 헤비 + 길게 뻗는 대각 그림자 (모던·시네마)
        "label": "롱섀도", "font": "pretendard", "weight": "heavy",
        "fill": (255, 255, 255), "stroke": None,
        "shadow": {"dx": 0.16, "dy": 0.16, "blur": 0, "color": (10, 8, 6), "alpha": 170, "steps": 12},
    },
}


def resolve_thumb_style(template: str, font: str = "pretendard",
                        weight: str = "bold", effect: str = "none") -> dict:
    """템플릿 키 → 렌더 스타일. custom(빈 값 포함)은 개별 font/weight/effect를 쓰고,
    템플릿은 번들 값이 개별 값을 대체한다. 미지 키는 custom으로 폴백."""
    style = dict(DEFAULT_THUMB_STYLE)
    tpl = THUMB_TEMPLATES.get(template) if template else None
    if tpl is None:
        style.update({"font": font, "weight": weight, "effect": effect})
    else:
        style.update(tpl)
    return style


def _star(d: "ImageDraw.ImageDraw", x: float, y: float, r: float, slim: float, color: tuple) -> None:
    """4갈래 반짝이 별 — 팔이 가늘게 테이퍼되는 8점 폴리곤."""
    w = max(1.0, r * slim)
    d.polygon([(x, y - r), (x + w, y - w), (x + r, y), (x + w, y + w),
               (x, y + r), (x - w, y + w), (x - r, y), (x - w, y - w)], fill=color)


def draw_thumb_effect(img: Image.Image, effect: str, box: tuple, seed: int = 7) -> Image.Image:
    """타이틀 텍스트 블록(box=(x0,y0,x1,y1)) 뒤에 효과를 합성한 이미지를 반환.

    2배 슈퍼샘플로 그려 절반 축소(안티앨리어스) + 블러 글로우를 샤프 레이어 아래
    깔아 발광감을 낸다. 시드 고정 — 재생성해도 같은 그림.
    """
    from PIL import ImageFilter

    if effect not in THUMB_EFFECTS or effect == "none":
        return img
    rng = random.Random(seed)
    W, H = img.size
    S = 2  # 슈퍼샘플 배율
    x0, y0, x1, y1 = (c * S for c in box)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bw, bh = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    if effect == "fireworks":
        # 샴페인 골드 버스트 — 혜성 꼬리(바깥으로 갈수록 잘고 옅어지는 점열) + 코어 글로우
        palette = [(255, 219, 130), (255, 186, 96), (255, 244, 214)]
        bursts = [
            (x0 - bw * 0.10, y0 - bh * 1.1, bh * 2.2),
            (x1 + bw * 0.10, y0 - bh * 0.7, bh * 1.7),
            (cx + bw * 0.08, y0 - bh * 2.0, bh * 1.2),
        ]
        for bx, by, radius in bursts:
            # 텍스트가 상단이면 버스트가 화면 밖으로 나간다 — 캔버스 안으로 클램프
            bx = min(max(bx, radius * 0.55), W * S - radius * 0.55)
            by = min(max(by, radius * 0.55), H * S - radius * 0.55)
            color = rng.choice(palette)
            for i in range(rng.randint(17, 22)):
                ang = i / 20 * 2 * math.pi + rng.uniform(-0.10, 0.10)
                curl = rng.uniform(-0.12, 0.12)
                reach = rng.uniform(0.7, 1.0)
                steps = 9
                for s_i in range(steps):
                    f = 0.22 + 0.78 * s_i / (steps - 1)
                    a = ang + curl * f
                    px = bx + radius * reach * f * math.cos(a)
                    py = by + radius * reach * f * math.sin(a)
                    r = max(S, bh * 0.05 * (1.1 - f))
                    alpha = int(240 * (1.08 - f))
                    d.ellipse([px - r, py - r, px + r, py + r], fill=color + (alpha,))
            core = bh * 0.10
            d.ellipse([bx - core, by - core, bx + core, by + core], fill=(255, 252, 240, 250))
        glow = layer.filter(ImageFilter.GaussianBlur(bh * 0.10))
        layer = Image.alpha_composite(glow, layer)

    elif effect == "fire":
        # 텍스트에 밀착한 화염 — 겹층 글로우(적→주황→노랑)는 블록 폭만큼,
        # 불꽃 혀는 블록 위로 또렷하게(약한 블러) 솟는다
        for grow, color, alpha, blur in (
            (1.00, (190, 32, 8), 135, 0.20),
            (0.72, (255, 105, 18), 155, 0.13),
            (0.46, (255, 205, 70), 175, 0.09),
        ):
            g = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
            gd = ImageDraw.Draw(g)
            gx = (bw * 0.62 + bh * 0.4) * grow
            up, down = bh * 1.0 * grow, bh * 0.55 * grow
            gd.ellipse([cx - gx, cy - up, cx + gx, cy + down], fill=color + (alpha,))
            # 불꽃 혀 — 블록 상단에서 위로 흔들리며 솟는 삼각 실루엣
            for _ in range(6):
                tx = cx + rng.uniform(-0.8, 0.8) * gx * 0.8
                th = bh * grow * rng.uniform(0.7, 1.6)
                tw = bh * 0.22 * grow * rng.uniform(0.7, 1.3)
                base = cy - up * 0.45
                gd.polygon([(tx - tw, base), (tx + tw, base),
                            (tx + rng.uniform(-0.6, 0.6) * tw, base - th)],
                           fill=color + (alpha,))
            layer = Image.alpha_composite(layer, g.filter(ImageFilter.GaussianBlur(bh * blur)))
        d = ImageDraw.Draw(layer)
        for _ in range(22):  # 불티 — 위로 갈수록 잘아지는 점 + 짧은 궤적
            ex = cx + rng.uniform(-0.7, 0.7) * bw
            ey = y0 - rng.uniform(0.3, 2.4) * bh
            r = max(S, bh * rng.uniform(0.015, 0.05) * (1.0 - (y0 - ey) / (2.6 * bh)))
            color = rng.choice([(255, 210, 90), (255, 150, 40), (255, 100, 25)])
            tail = r * rng.uniform(2.0, 4.0)
            d.line([(ex, ey), (ex + rng.uniform(-r, r), ey + tail)],
                   fill=color + (110,), width=max(1, int(r)))
            d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=color + (rng.randint(170, 235),))
        glow = layer.filter(ImageFilter.GaussianBlur(bh * 0.05))
        layer = Image.alpha_composite(glow, layer)

    elif effect == "sparkle":
        # 샴페인 반짝이 — 큰 별엔 가로 플레어, 배경엔 보케 점광
        tones = [(255, 255, 255), (255, 238, 170), (255, 216, 110)]
        for _ in range(12):  # 보케 (뒤에 깔리는 흐릿한 점광)
            ex = cx + rng.uniform(-1.05, 1.05) * bw * 0.85
            ey = cy + rng.uniform(-2.6, 1.9) * bh
            r = bh * rng.uniform(0.06, 0.16)
            d.ellipse([ex - r, ey - r, ex + r, ey + r],
                      fill=rng.choice(tones) + (rng.randint(50, 110),))
        layer = layer.filter(ImageFilter.GaussianBlur(bh * 0.06))
        d = ImageDraw.Draw(layer)
        stars = []
        for _ in range(13):
            ex = cx + rng.uniform(-0.9, 0.9) * bw * 0.8
            ey = cy + rng.uniform(-2.3, 1.7) * bh
            r = bh * rng.uniform(0.12, 0.34)
            stars.append((ex, ey, r, rng.choice(tones)))
        sharp = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sharp)
        for ex, ey, r, tone in stars:
            if r > bh * 0.26:  # 큰 별 — 가로 플레어
                sd.ellipse([ex - r * 2.6, ey - r * 0.10, ex + r * 2.6, ey + r * 0.10],
                           fill=tone + (120,))
            _star(sd, ex, ey, r, 0.13, tone + (rng.randint(200, 255),))
            _star(sd, ex, ey, r * 0.45, 0.22, (255, 255, 255, 255))
        glow = sharp.filter(ImageFilter.GaussianBlur(bh * 0.07))
        layer = Image.alpha_composite(layer, Image.alpha_composite(glow, sharp))

    layer = layer.resize((W, H), Image.LANCZOS)
    return Image.alpha_composite(img.convert("RGBA"), layer)


def thumb_font_path(key: str, weight: str = "bold") -> Path | None:
    """폰트 키 → 동봉 파일 경로. 미지원 키·파일 없음은 None (호출부가 기본 폰트로)."""
    entry = THUMB_FONTS.get(key)
    if entry is None:
        return None
    fname = entry[0]
    if key == "pretendard" and weight == "normal":
        fname = "Pretendard-Bold.otf"  # 보통 굵기는 ExtraBold 대신 Bold
    p = _FONT_DIR / fname
    return p if p.is_file() else None


def hook_anchor_y(v: str, height: int, block_h: int) -> int:
    """타이틀 블록의 y 시작 — top 8% / middle 중앙 / bottom 하단 12% 여백."""
    if v == "top":
        return int(height * 0.08)
    if v == "middle":
        return int((height - block_h) / 2)
    return height - int(height * 0.12) - block_h


def hook_anchor_x(h: str, width: int, line_w: float) -> int:
    """각 줄의 x 시작 — left/right는 6% 여백, center는 중앙."""
    if h == "left":
        return int(width * 0.06)
    if h == "right":
        return int(width - width * 0.06 - line_w)
    return int((width - line_w) / 2)


def wrap_hook_lines(text: str, measure, max_w: int, max_lines: int = 3) -> list[str]:
    """수동 줄바꿈(\\n) 우선, 각 줄 안에서 measure(px) 기준 단어 단위 줄바꿈."""
    lines: list[str] = []
    for part in text.split("\n"):
        cur = ""
        for word in part.split():
            trial = f"{cur} {word}".strip()
            if measure(trial) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
            if len(lines) == max_lines:
                return lines
        if cur:
            lines.append(cur)
        if len(lines) == max_lines:
            break
    return lines


def _linear_gradient(size: tuple, a0: int, a1: int, start: tuple, end: tuple,
                     horizontal: bool = False) -> Image.Image:
    """블록 a0~a1 구간(세로는 y, 가로는 x)에서 start→end로 보간되는 그라디언트 캔버스."""
    W, H = size
    n = W if horizontal else H
    strip = Image.new("RGB", (n, 1) if horizontal else (1, n))
    span = max(1, a1 - a0)
    for i in range(n):
        f = min(1.0, max(0.0, (i - a0) / span))
        color = tuple(int(a + (b - a) * f) for a, b in zip(start, end))
        strip.putpixel((i, 0) if horizontal else (0, i), color)
    return strip.resize((W, H))


def overlay_hook_text(
    image_path: Path, text: str, pos: str = "bottom-center", font: str = "pretendard",
    scale: float = 1.0, weight: str = "bold", effect: str = "none",
    template: str = "custom",
) -> None:
    """썸네일에 타이틀 문구를 burn-in — 템플릿(스타일 번들) 또는 커스텀 조합.

    pos는 "top|middle|bottom-left|center|right", scale은 기본 크기(폭/14) 배율.
    template이 THUMB_TEMPLATES 키면 폰트·색·외곽선·그림자·배경·틸트를 번들로 쓰고
    font/weight/effect 개별 값은 무시한다. custom이면 현행 흰 글씨+검정 외곽선.
    폭 88%를 넘으면 단어 단위 줄바꿈(최대 3줄), 수동 \\n도 존중한다.
    """
    if not text.strip():
        return
    from PIL import ImageFilter

    v, _, h = pos.partition("-")
    if pos not in HOOK_POSITIONS:
        v, h = "bottom", "center"
    style = resolve_thumb_style(template, font, weight, effect)
    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    picked = thumb_font_path(style["font"], style["weight"])
    if picked is not None:
        font_path, font_index = str(picked), 0
    else:
        font_path, font_index = find_korean_font()
    size = max(24, int(W / 14 * scale * style.get("scale_mul", 1.0)))
    try:
        fnt = ImageFont.truetype(font_path, size, index=font_index)
    except (OSError, IndexError):
        fnt = ImageFont.truetype(font_path, size)
    measure = ImageDraw.Draw(img)

    lines = wrap_hook_lines(text, lambda s: measure.textlength(s, font=fnt), int(W * 0.88))
    st = style.get("stroke")
    stroke = max(2, size // st.get("k", THUMB_WEIGHTS.get(style["weight"], 12))) if st else 0
    line_h = int(size * 1.25)
    block_h = line_h * len(lines)
    y0 = hook_anchor_y(v, H, block_h)

    # 텍스트 블록 bbox — 배경 효과·회전 중심의 기준 좌표
    widths = [measure.textlength(ln, font=fnt) for ln in lines]
    xs = [hook_anchor_x(h, W, tw) for tw in widths]
    box = (min(xs), y0, max(x + int(tw) for x, tw in zip(xs, widths)), y0 + block_h)
    img = draw_thumb_effect(img, style["effect"], box).convert("RGB")

    # 배경 바·그림자·텍스트는 투명 레이어에 그려 회전까지 마친 뒤 합성
    em = lambda k: int(size * k)  # noqa: E731
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)

    def text_pass(target: "ImageDraw.ImageDraw", color: tuple, width: int,
                  dx: int = 0, dy: int = 0, fill_color: tuple | None = None) -> None:
        yy = y0
        for line, x in zip(lines, xs):
            target.text((x + dx, yy + dy), line, font=fnt, fill=fill_color or color,
                        stroke_width=width, stroke_fill=color if width else None)
            yy += line_h

    bg = style.get("bg")
    if bg:
        for x, tw, ly in zip(xs, widths, range(y0, y0 + block_h, line_h)):
            bx0, bx1 = ((0, W) if bg.get("full")
                        else (x - em(bg["pad_x"]), x + tw + em(bg["pad_x"])))
            ld.rounded_rectangle(
                [bx0, ly + em(0.02) - em(bg["pad_y"]), bx1, ly + em(1.08) + em(bg["pad_y"])],
                radius=em(bg["radius"]), fill=tuple(bg["color"]))

    sh = style.get("shadow")
    if sh:
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        color = tuple(sh["color"]) + (sh["alpha"],)
        # steps>1이면 오프셋을 잘게 나눠 겹쳐 그린 압출(3D extrude) 그림자
        steps = sh.get("steps", 1)
        for i in range(steps, 0, -1):
            f = i / steps
            text_pass(sd, color, stroke, int(em(sh["dx"]) * f), int(em(sh["dy"]) * f))
        if sh["blur"]:
            shadow = shadow.filter(ImageFilter.GaussianBlur(em(sh["blur"])))
        layer = Image.alpha_composite(layer, shadow)
        ld = ImageDraw.Draw(layer)

    st2 = style.get("stroke2")
    if st2:  # 이중 외곽선 — 안쪽 외곽선 바깥에 한 겹 더
        text_pass(ld, tuple(st2["color"]), stroke + max(2, size // st2.get("k", 6)))

    fill = style["fill"]
    if isinstance(fill, dict):  # 그라디언트 — 실루엣(외곽선 포함) 위에 마스크로 채움
        if stroke:
            text_pass(ld, tuple(st["color"]), stroke)
        mask = Image.new("L", (W, H), 0)
        text_pass(ImageDraw.Draw(mask), (255,), 0, fill_color=255)
        start, end = fill["gradient"]
        horizontal = fill.get("dir") == "h"
        a0, a1 = (box[0], box[2]) if horizontal else (y0, y0 + block_h)
        grad = _linear_gradient((W, H), a0, a1, start, end, horizontal)
        layer.paste(grad, (0, 0), mask)
    else:
        text_pass(ld, tuple(st["color"]) if st else (0, 0, 0), stroke, fill_color=tuple(fill))

    tilt = style.get("tilt", 0)
    if tilt:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        layer = layer.rotate(tilt, resample=Image.BICUBIC, center=(cx, cy))

    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    img.save(image_path, quality=92)


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

        # filter_complex로 xfade(영상) + acrossfade(오디오) chain.
        # 무음 원본(드론·타임랩스 등 scene/vision 대표 케이스)은 오디오 체인을 통째로 생략.
        has_audio = has_audio_stream(input_path)
        inputs = []
        for cp in clip_paths:
            inputs += ["-i", str(cp)]
        n = len(clip_paths)
        clip_durs = [s["end"] - s["start"] for s in segments]
        chain = []
        vprev, aprev = "[0:v]", "[0:a]"
        offset = clip_durs[0] - tdur
        for i in range(1, n):
            vout = f"[v{i}]" if i < n - 1 else "[vout]"
            aout = f"[a{i}]" if i < n - 1 else "[aout]"
            chain.append(
                f"{vprev}[{i}:v]xfade=transition={transition}:duration={tdur}:offset={offset:.3f}{vout}"
            )
            if has_audio:
                # acrossfade는 두 입력 경계를 겹쳐 크로스페이드 → 영상 xfade와 총길이 일치
                chain.append(f"{aprev}[{i}:a]acrossfade=d={tdur}{aout}")
            vprev, aprev = vout, aout
            offset += clip_durs[i] - tdur

        audio_args = (["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
                      if has_audio else [])
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             *inputs,
             "-filter_complex", ";".join(chain),
             "-map", "[vout]", *audio_args,
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             str(output_path)],
            check=True,
        )
    finally:
        for p in tmpdir.glob("*"):
            p.unlink()
        tmpdir.rmdir()


# ============================================================================
# Shorts footage — 점프컷 서브클립 + punch-in 교차 (페이드 없음: 첫 프레임=커버)
# ============================================================================

def build_short_footage(
    input_path: Path,
    clips: list[tuple],
    output_path: Path,
    punchin: bool = True,
    punch_scale: float = 1.08,
    vertical: bool = False,
    blur_bg: bool = False,
    focus: str = "center",
    target_w: int = 1080,
    target_h: int = 1920,
) -> None:
    """clips(원본 시점 (s, e) 목록)를 잘라 concat. 홀수번째 클립에 punch-in.

    점프컷 경계와 punch 가상 컷이 모두 clips로 들어온다. 컷 경계에서
    1.0x↔punch_scale 크롭 줌을 교차해 점프컷을 의도된 리듬으로 보이게 한다.
    페이드인 없음 — 숏츠 첫 프레임이 곧 커버(트렌드 표준).

    vertical=True면 세로 9:16 변환까지 클립 인코딩에 합친다 — 별도
    리프레임 패스(풀 인코딩 1회)가 사라진다. focus(left/center/right)는
    가로 영상에서 어느 쪽을 살릴지, blur_bg는 흐린 배경 레터박스 대체.
    """
    w, h = probe_resolution(input_path)
    w -= w % 2
    h -= h % 2
    vx = {"left": "0", "right": "iw-ow"}.get(focus, "(iw-ow)/2")
    tmpdir = output_path.parent / f".{output_path.stem}_short_tmp"
    tmpdir.mkdir(exist_ok=True)
    try:
        paths = []
        for i, (s, e) in enumerate(clips):
            cp = tmpdir / f"clip_{i:03d}.mp4"
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-ss", f"{s:.3f}", "-i", str(input_path), "-t", f"{e - s:.3f}"]
            punch = f"crop=iw/{punch_scale}:ih/{punch_scale}," if (punchin and i % 2 == 1) else ""
            if vertical and blur_bg:
                fc = (
                    f"[0:v]{punch}split[a][b];"
                    f"[a]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                    f"crop={target_w}:{target_h},boxblur=24:2[bg];"
                    f"[b]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
                )
                cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a?"]
            elif vertical:
                vf = (f"{punch}scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                      f"crop={target_w}:{target_h}:{vx}:(ih-oh)/2")
                cmd += ["-vf", vf, "-map", "0:v:0", "-map", "0:a?"]
            else:
                cmd += ["-vf", f"{punch}scale={w}:{h}"]  # concat을 위한 해상도 통일
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-avoid_negative_ts", "make_zero", str(cp)]
            subprocess.run(cmd, check=True)
            paths.append(cp)

        list_file = tmpdir / "concat.txt"
        list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in paths))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", str(output_path)],
            check=True,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def apply_audio_fade_out(input_path: Path, output_path: Path, fade: float = 0.2) -> None:
    """영상은 그대로, 오디오만 끝 fade초 페이드아웃(컷 팝 방지). 오디오 없으면 복사."""
    if not has_audio_stream(input_path):
        shutil.copy(input_path, output_path)
        return
    total = probe_duration(input_path)
    st = max(0.0, total - fade)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(input_path),
         "-af", f"afade=out:st={st:.3f}:d={fade}",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         str(output_path)],
        check=True,
    )


# ============================================================================
# BGM mux — 영상에 배경음 + (선택) 페이드아웃
# ============================================================================

def add_sfx(
    input_path: Path,
    events: list[tuple[float, Path]],
    output_path: Path,
    gain: float = 0.9,
) -> None:
    """(초, 파일) 이벤트들의 시점에 효과음을 오버레이 믹싱. 원본 오디오·길이 유지.

    영상 길이를 넘는 이벤트는 버린다. BGM보다 먼저 돌려야 BGM 페이드가 전체에 걸린다.
    """
    vdur = probe_duration(input_path)
    events = [(t, p) for t, p in events if 0 <= t < vdur]
    if not events:
        return
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for i, (t, p) in enumerate(events, start=1):
        inputs += ["-i", str(p)]
        ms = int(t * 1000)
        filters.append(f"[{i}:a]volume={gain},adelay={ms}|{ms}[s{i}]")
        labels.append(f"[s{i}]")
    if has_audio_stream(input_path):
        base = "[0:a]"
    else:
        filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={vdur:.3f}[base]")
        base = "[base]"
    n = len(events) + 1
    filter_complex = ";".join(filters) + \
        f";{base}{''.join(labels)}amix=inputs={n}:duration=first:normalize=0:dropout_transition=0[aout]"

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-i", str(input_path), *inputs,
         "-filter_complex", filter_complex,
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         str(output_path)],
        check=True,
    )


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
        # normalize=0 — 기본 정규화는 BGM·원본을 절반으로 깎아 설정 볼륨이 무의미해진다
        filter_complex = (
            f"[1:a]volume={bgm_volume},"
            f"afade=out:st={bgm_fade_start:.3f}:d={fade_out},"
            f"atrim=duration={vdur:.3f}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[aout]"
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
