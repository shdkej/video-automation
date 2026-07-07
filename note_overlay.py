"""실소재 영상 + 노트 이미지로 NoteOverlay(영상 위에 노트가 떠 있는 연출) 렌더.

subtitle_remotion.py와 같은 계약: props JSON을 만들어 Remotion CLI를 subprocess로 호출.
Remotion staticFile은 렌더 엔트리의 public/ 아래만 읽으므로, 임의 경로의 실소재는
렌더 동안 note-src/<작업명>/ 에 스테이징했다가 끝나면 정리한다.
- 사전 번들(build/)로 렌더할 때는 번들이 public을 복사해 간 build/public이 기준
- Remotion 정적 서버(serve-handler)는 symlink를 기본 404 처리하므로 symlink 불가
  → hardlink(무비용) 우선, 파일시스템이 다르면 복사 폴백

CLI:
    python note_overlay.py video.mp4 pages.json -o out.mp4

pages.json 두 형식 모두 허용:
    [{"src": "notes/tower.png", "start": 0.8, "end": 3.9}, ...]   # 타이밍 직접 지정
    ["notes/tower.png", "notes/bridge.png"]                        # 영상 길이에 균등 배분
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from probe import probe_resolution
from subtitle_remotion import BUNDLE_DIR, ENTRY, REMOTION_BIN, REMOTION_DIR, probe_duration_sec, probe_fps

COMPOSITION = "NoteOverlay"

# 균등 배분 시 페이지 사이 간격(초) — 배경이 잠깐 온전히 보이는 숨 고르기 구간
AUTO_GAP_SEC = 1.0


def _render_entry() -> str:
    return "build" if (BUNDLE_DIR / "index.html").exists() else ENTRY


def _public_root() -> Path:
    """staticFile이 실제로 읽는 public 디렉토리 (사전 번들이면 build/public)."""
    return BUNDLE_DIR / "public" if _render_entry() == "build" else REMOTION_DIR / "public"


def _stage(src: Path, stage_dir: Path, public_root: Path) -> str:
    """소재를 public/ 아래로 스테이징하고 public 기준 상대 경로를 돌려준다."""
    src = src.resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    dst = stage_dir / f"{uuid.uuid4().hex[:8]}_{src.name}"
    try:
        os.link(src, dst)
    except OSError:  # 다른 파일시스템 등 — 복사 폴백
        shutil.copy2(src, dst)
    return str(dst.relative_to(public_root))


def _auto_pages(images: list[Path], duration: float) -> list[dict]:
    """이미지 목록만 받았을 때 영상 길이에 균등 배분. 앞뒤 0.8s 여백."""
    lead, tail = 0.8, 0.5
    usable = max(1.0, duration - lead - tail)
    n = len(images)
    slot = (usable - AUTO_GAP_SEC * (n - 1)) / n
    pages = []
    t = lead
    for img in images:
        pages.append({"src": img, "start": round(t, 3), "end": round(t + slot, 3)})
        t += slot + AUTO_GAP_SEC
    return pages


def render_note_overlay(
    video: Path,
    pages: list[dict | str | Path],
    output: Path,
    page_width_ratio: float = 0.78,
) -> dict:
    """배경 영상 위에 노트 페이지들이 flip으로 떠오르는 영상을 렌더.

    pages: [{"src", "start", "end"}] 또는 이미지 경로 목록(균등 배분).
    Returns: {"output", "props"}
    """
    if not REMOTION_BIN.exists():
        raise RuntimeError(f"Remotion 미설치: {REMOTION_BIN} 없음. remotion-map에서 npm install 필요.")

    duration = probe_duration_sec(video)
    if duration <= 0:
        raise ValueError(f"영상 길이를 읽지 못함: {video}")
    if pages and not isinstance(pages[0], dict):
        pages = _auto_pages([Path(p) for p in pages], duration)

    w, h = probe_resolution(video)
    w, h = w & ~1, h & ~1

    public_root = _public_root()
    stage_dir = public_root / "note-src" / uuid.uuid4().hex[:8]
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        props = {
            "videoSrc": _stage(Path(video), stage_dir, public_root),
            "pages": [
                {
                    "src": _stage(Path(p["src"]), stage_dir, public_root),
                    "start": p["start"],
                    "end": min(p["end"], duration),
                }
                for p in pages
            ],
            "width": w,
            "height": h,
            "fps": round(probe_fps(video)),
            "durationSec": round(duration, 3),
            "pageWidthRatio": page_width_ratio,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(props, f, ensure_ascii=False)
            props_path = f.name

        cmd = [
            str(REMOTION_BIN), "render", _render_entry(), COMPOSITION, str(output.resolve()),
            f"--props={props_path}",
            "--timeout=120000",
            "--log=error",
        ]
        subprocess.run(cmd, cwd=REMOTION_DIR, check=True, timeout=3600)
        Path(props_path).unlink(missing_ok=True)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    return {"output": str(output), "props": props}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="영상 위에 노트 페이지가 떠 있는 연출(NoteOverlay) 렌더")
    parser.add_argument("video", type=Path, help="배경 영상")
    parser.add_argument("pages_json", type=Path,
                        help='[{"src","start","end"}] 또는 ["img1.png", ...] JSON')
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--page-width-ratio", type=float, default=0.78,
                        help="화면 폭 대비 노트 폭 (기본 0.78)")
    args = parser.parse_args()

    raw = json.loads(args.pages_json.read_text())
    # 상대 경로는 pages.json 위치 기준으로 해석
    base = args.pages_json.parent

    def _resolve(p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else base / p

    if raw and isinstance(raw[0], dict):
        pages: list = [{**p, "src": _resolve(p["src"])} for p in raw]
    else:
        pages = [_resolve(p) for p in raw]

    output = args.output or args.video.with_name(args.video.stem + "_note.mp4")
    result = render_note_overlay(args.video, pages, output, page_width_ratio=args.page_width_ratio)
    print(json.dumps(result, ensure_ascii=False, indent=2))
