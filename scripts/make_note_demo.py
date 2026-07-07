"""NoteOverlay 프로토타입용 placeholder 소재 생성.

- 배경 영상: PIL로 그린 풍경 3장면 → ffmpeg zoompan 슬로우 줌 → concat (12s, 1080x1920)
- 노트 이미지: 종이 질감 + 손그림 느낌 스케치 2장 (탑 / 다리)

산출물은 remotion-map/public/note-demo/ 에 둔다 (staticFile로 참조).
"""

from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "remotion-map" / "public" / "note-demo"

W, H = 1350, 2400  # zoompan 여유분 포함 (출력은 1080x1920)
SCENE_SEC = 4
FPS = 30


def _vgrad(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        t = y / h
        c = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
        for x in range(w):
            px[x, y] = c
    return img


def scene_tower() -> Image.Image:
    """벽돌 급수탑 실루엣 + 하늘."""
    img = _vgrad((W, H), (150, 190, 225), (225, 218, 195))
    d = ImageDraw.Draw(img)
    rnd = random.Random(1)
    for _ in range(6):  # 구름
        cx, cy, r = rnd.randint(100, W - 100), rnd.randint(150, 800), rnd.randint(60, 140)
        for dx in (-r, 0, r):
            d.ellipse([cx + dx - r, cy - r // 2, cx + dx + r, cy + r // 2], fill=(245, 245, 240))
    # 탑
    cx = W // 2
    d.polygon([(cx - 90, 2000), (cx - 70, 1050), (cx + 70, 1050), (cx + 90, 2000)], fill=(160, 80, 55))
    d.rectangle([cx - 130, 850, cx + 130, 1080], fill=(170, 88, 60))
    for i in range(7):  # 총안
        x = cx - 126 + i * 42
        d.rectangle([x, 850, x + 24, 890], fill=(150, 190, 225))
    for y in (1200, 1450, 1700):  # 창
        d.rectangle([cx - 18, y, cx + 18, y + 70], fill=(70, 45, 40))
    # 숲/땅
    for _ in range(24):
        tx, r = rnd.randint(0, W), rnd.randint(60, 130)
        ty = rnd.randint(1850, 2000)
        d.ellipse([tx - r, ty - r, tx + r, ty + r], fill=(58 + rnd.randint(0, 30), 92 + rnd.randint(0, 30), 48))
    d.rectangle([0, 2000, W, H], fill=(95, 125, 62))
    return img.filter(ImageFilter.GaussianBlur(2))


def scene_bridge() -> Image.Image:
    """운하 위 나무다리, 노을."""
    img = _vgrad((W, H), (235, 180, 130), (200, 210, 215))
    d = ImageDraw.Draw(img)
    rnd = random.Random(2)
    d.ellipse([W // 2 - 110, 500, W // 2 + 110, 720], fill=(250, 230, 180))  # 해
    d.rectangle([0, 1600, W, H], fill=(120, 145, 150))  # 물
    for _ in range(40):  # 물결
        x, y = rnd.randint(0, W - 200), rnd.randint(1650, 2300)
        d.line([x, y, x + rnd.randint(80, 180), y], fill=(160, 180, 182), width=6)
    d.rectangle([0, 1450, W, 1620], fill=(110, 78, 52))  # 다리 상판
    for i in range(9):  # 난간
        x = 40 + i * 160
        d.rectangle([x, 1300, x + 26, 1470], fill=(96, 66, 44))
    d.rectangle([0, 1290, W, 1320], fill=(104, 72, 48))
    for i in range(3):  # 새
        x, y = 200 + i * 300, 400 + i * 90
        d.arc([x, y, x + 70, y + 46], 200, 340, fill=(80, 70, 70), width=7)
    return img.filter(ImageFilter.GaussianBlur(2))


def scene_ivy() -> Image.Image:
    """담쟁이 잎 클로즈업 (릴 첫 장면 느낌)."""
    img = Image.new("RGB", (W, H), (52, 82, 44))
    d = ImageDraw.Draw(img)
    rnd = random.Random(3)
    for _ in range(260):
        x, y = rnd.randint(-60, W + 60), rnd.randint(-60, H + 60)
        r = rnd.randint(45, 110)
        g = rnd.randint(90, 160)
        col = (g - 55, g, g - 62) if rnd.random() < 0.75 else (200, 210, 170)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        d.line([x, y - r, x, y + r // 2], fill=(40, 66, 36), width=5)
    return img.filter(ImageFilter.GaussianBlur(3))


# ── 노트 페이지 ──────────────────────────────────────────────


def _paper(size: tuple[int, int]) -> Image.Image:
    img = Image.new("RGB", size, (247, 243, 232))
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(3500):  # 종이 티끌
        x, y = rnd.randint(0, size[0] - 1), rnd.randint(0, size[1] - 1)
        v = rnd.randint(-10, 6)
        d.point((x, y), fill=(247 + v, 243 + v, 232 + v))
    return img


def _stroke(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], rnd: random.Random,
            width: int = 7, jitter: float = 3.5, ink=(35, 32, 30)) -> None:
    """점 목록을 손떨림 있는 선으로 잇는다."""
    prev = None
    for x, y in pts:
        p = (x + rnd.uniform(-jitter, jitter), y + rnd.uniform(-jitter, jitter))
        if prev:
            d.line([prev, p], fill=ink, width=width)
        prev = p


def _frame_border(d: ImageDraw.ImageDraw, w: int, h: int, rnd: random.Random) -> None:
    m = 70
    for corner_pts in (
        [(m, m + rnd.randint(-8, 8)), (w - m, m)],
        [(w - m, m), (w - m, h - m)],
        [(w - m, h - m), (m, h - m + rnd.randint(-8, 8))],
        [(m, h - m), (m, m)],
    ):
        n = 14
        (x0, y0), (x1, y1) = corner_pts
        pts = [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]
        _stroke(d, pts, rnd, width=9, jitter=4)


def note_tower() -> Image.Image:
    w, h = 1200, 1600
    img = _paper((w, h))
    d = ImageDraw.Draw(img)
    rnd = random.Random(11)
    _frame_border(d, w, h, rnd)
    cx = w // 2
    # 탑 몸통·머리
    _stroke(d, [(cx - 70, 1150), (cx - 55, 560)], rnd)
    _stroke(d, [(cx + 70, 1150), (cx + 55, 560)], rnd)
    _stroke(d, [(cx - 110, 560), (cx + 110, 560)], rnd)
    _stroke(d, [(cx - 110, 560), (cx - 110, 380), (cx + 110, 380), (cx + 110, 560)], rnd)
    for i in range(5):  # 총안
        x = cx - 100 + i * 45
        _stroke(d, [(x, 380), (x, 350), (x + 28, 350), (x + 28, 380)], rnd, width=5, jitter=2)
    for y in (650, 800, 950):  # 창
        _stroke(d, [(cx - 14, y), (cx - 14, y + 55), (cx + 14, y + 55), (cx + 14, y), (cx - 14, y)], rnd, width=5, jitter=2)
    # 옆 나무
    _stroke(d, [(240, 1150), (250, 700)], rnd)
    for i in range(8):
        y = 700 + i * 52
        _stroke(d, [(250, y), (250 - rnd.randint(50, 90), y - 35)], rnd, width=5, jitter=4)
        _stroke(d, [(250, y), (250 + rnd.randint(40, 80), y - 30)], rnd, width=5, jitter=4)
    # 지면 수풀
    gx = 160
    while gx < w - 160:
        _stroke(d, [(gx, 1150), (gx + rnd.randint(-14, 14), 1110)], rnd, width=4, jitter=3)
        gx += rnd.randint(28, 60)
    # 캡션 밑줄 느낌
    _stroke(d, [(cx - 200, 1330), (cx + 200, 1340)], rnd, width=5, jitter=3)
    return img


def note_bridge() -> Image.Image:
    w, h = 1200, 1600
    img = _paper((w, h))
    d = ImageDraw.Draw(img)
    rnd = random.Random(13)
    _frame_border(d, w, h, rnd)
    # 다리 아치와 상판
    n = 20
    arc = [(150 + (w - 300) * i / n, 900 - math.sin(math.pi * i / n) * 260) for i in range(n + 1)]
    _stroke(d, arc, rnd, width=8)
    deck = [(150 + (w - 300) * i / n, 620) for i in range(n + 1)]
    _stroke(d, deck, rnd, width=8)
    for i in range(0, n + 1, 2):  # 난간살
        x = 150 + (w - 300) * i / n
        y_arc = 900 - math.sin(math.pi * i / n) * 260
        _stroke(d, [(x, 620), (x, min(y_arc, 900))], rnd, width=5, jitter=2)
    # 물결
    for row in range(4):
        y = 1020 + row * 70
        wave = [(180 + i * 60 + row * 20, y + math.sin(i * 1.3) * 10) for i in range(14)]
        _stroke(d, wave, rnd, width=4, jitter=2)
    # 새
    for bx, by in ((320, 320), (520, 260), (720, 340)):
        _stroke(d, [(bx, by), (bx + 30, by - 22), (bx + 60, by)], rnd, width=5, jitter=2)
    _stroke(d, [(w // 2 - 200, 1330), (w // 2 + 200, 1340)], rnd, width=5, jitter=3)
    return img


def build_bg(scene_paths: list[Path], out: Path) -> None:
    """장면 정지화상 3장 → 슬로우 줌 클립 → concat."""
    tmp = OUT / ".bgtmp"
    tmp.mkdir(exist_ok=True)
    clips = []
    frames = SCENE_SEC * FPS
    for i, p in enumerate(scene_paths):
        clip = tmp / f"clip{i}.mp4"
        zoom = f"zoompan=z='1+0.0009*on':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS}"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(p),
            "-vf", zoom, "-t", str(SCENE_SEC),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(clip),
        ], check=True)
        clips.append(clip)
    concat_txt = tmp / "list.txt"
    concat_txt.write_text("".join(f"file '{c}'\n" for c in clips))
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat_txt), "-c", "copy", str(out),
    ], check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = []
    for name, fn in (("scene_ivy", scene_ivy), ("scene_tower", scene_tower), ("scene_bridge", scene_bridge)):
        p = OUT / f"{name}.png"
        fn().save(p)
        scenes.append(p)
    note_tower().save(OUT / "note_tower.png")
    note_bridge().save(OUT / "note_bridge.png")
    build_bg([scenes[1], scenes[2], scenes[0]], OUT / "bg.mp4")  # 탑 → 다리 → 담쟁이
    print(f"done → {OUT}")


if __name__ == "__main__":
    main()
