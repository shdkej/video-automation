#!/usr/bin/env python3
"""자막 스타일 데모 클립 생성 — 결과 화면의 스타일 선택 미리보기용.

그라디언트 배경 2.5초 클립에 실제 렌더러로 4스타일(fade/kinetic/impact/pil)을
입혀 web/static/demos/{style}.mp4 로 저장한다. 렌더러가 바뀌면 다시 돌려 커밋.

실행(프로젝트 루트): python scripts/make_style_demos.py
remotion 환경(node + remotion-map npm ci)이 필요하므로 파드/이미지에서 돌리는 게 쉽다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from subtitle import render_subtitled  # noqa: E402
from subtitle_remotion import render_subtitled_remotion  # noqa: E402

OUT_DIR = ROOT / "web" / "static" / "demos"
DUR = 2.5
W, H = 540, 960  # 9:16 축소판 — 데모는 용량이 우선
TEXT = "자막이 이렇게 나와요"
EVENTS = [{
    "text": TEXT, "start": 0.15, "end": DUR - 0.05,
    "words": [
        {"text": "자막이", "start": 0.0, "end": 0.6},
        {"text": "이렇게", "start": 0.6, "end": 1.3},
        {"text": "나와요", "start": 1.3, "end": 2.2},
    ],
}]


def make_base(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i",
         f"gradients=s={W}x{H}:d={DUR}:c0=#2a2018:c1=#0b0a09:x0=0:y0=0:x1={W}:y1={H}",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={DUR}",
         "-shortest", "-c:v", "libx264", "-preset", "fast", "-crf", "26",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUT_DIR / ".base.mp4"
    make_base(base)
    try:
        for style in ("fade", "kinetic", "impact"):
            out = OUT_DIR / f"{style}.mp4"
            print(f"[demo] {style} → {out}")
            render_subtitled_remotion(
                cut_path=base, captions=[], segments=[], events=EVENTS,
                output=out, style=style, mode="shorts",
                font_size=56, margin_bottom=300,
            )
        out = OUT_DIR / "pil.mp4"
        print(f"[demo] pil → {out}")
        render_subtitled(
            cut_path=base, captions=[TEXT],
            segments=[{"start": 0.15, "end": DUR - 0.05}],
            output=out, font_size=56, margin_v=300, max_caption_width=int(W * 0.88),
        )
    finally:
        base.unlink(missing_ok=True)
        for srt in OUT_DIR.glob("*.srt"):  # 렌더러가 남긴 부산물 — 데모엔 불필요
            srt.unlink()
    for p in sorted(OUT_DIR.glob("*.mp4")):
        print(f"  {p.name}: {p.stat().st_size / 1e3:.0f}KB")


if __name__ == "__main__":
    main()
