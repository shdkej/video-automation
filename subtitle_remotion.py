"""Remotion 기반 애니메이션 자막 오버레이 (subtitle.py의 대안).

subtitle.py는 PIL 정적 PNG를 ffmpeg overlay(enable=between)로 구간별 burn-in 한다.
이 모듈은 대신 Remotion으로 **투명 배경 애니메이션 자막**(페이드/슬라이드)을 한 개의
알파 webm으로 렌더한 뒤, ffmpeg overlay 한 번으로 컷 영상 위에 합성한다.

- 계약은 subtitle.py.render_subtitled와 동일: captions + segments(start/end) → output
- segments는 selection.json 그대로(원본 시점). 합치면 컷 타임라인이므로 누적 길이로 window 계산.
- Remotion 산출물은 VP8 알파 webm (ffprobe상 yuv420p로 보이나 별도 알파 레이어 보유).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

REMOTION_DIR = Path(__file__).parent / "remotion-map"
REMOTION_BIN = REMOTION_DIR / "node_modules" / ".bin" / "remotion"
ENTRY = "src/index.ts"
COMPOSITION = "SubtitleOverlay"

# 오버레이 렌더 해상도 상한(세로 px). footage가 이보다 크면(예: 4K) 비례 축소해
# 렌더한 뒤 합성 단계에서 footage 크기로 업스케일한다. 4K 알파 VP8 인코딩은
# 저코어 머신에서 비현실적으로 느려(0.008x) 사실상 렌더가 끝나지 않기 때문.
OVERLAY_MAX_HEIGHT = 1080


def probe_fps(path: Path) -> float:
    """첫 비디오 스트림의 평균 fps."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=s=,:p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    num, _, den = out.partition("/")
    try:
        return float(num) / float(den) if den else float(num)
    except (ValueError, ZeroDivisionError):
        return 30.0


def probe_resolution(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=,:p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    nums = [p for p in out.replace("\n", ",").split(",") if p.strip().isdigit()]
    return int(nums[0]), int(nums[1])


def probe_duration_sec(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def has_audio_stream(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return bool(out)


def build_events(
    captions: list[str],
    segments: list[dict],
    speakers: list[str] | None = None,
) -> list[dict]:
    """컷 타임라인(초) 기준 자막 이벤트 목록. subtitle.py의 windows 로직과 동일.

    speakers를 주면 captions와 같은 길이여야 하고, 각 이벤트에 speaker(화자색 키)가 붙는다.
    """
    if len(captions) != len(segments):
        raise ValueError(f"captions({len(captions)}) ≠ segments({len(segments)})")
    if speakers is not None and len(speakers) != len(captions):
        raise ValueError(f"speakers({len(speakers)}) ≠ captions({len(captions)})")
    events, t = [], 0.0
    for i, (cap, seg) in enumerate(zip(captions, segments)):
        dur = seg["end"] - seg["start"]
        ev = {"text": cap, "start": round(t, 3), "end": round(t + dur, 3)}
        if speakers and speakers[i]:
            ev["speaker"] = speakers[i]
        events.append(ev)
        t += dur
    return events


def render_overlay_webm(
    events: list[dict],
    width: int,
    height: int,
    fps: float,
    out_webm: Path,
    font_size: int = 44,
    margin_bottom: int = 72,
    style: str = "fade",
    palette: dict[str, str] | None = None,
    hook: str | None = None,
    mode: str = "longform",
    duration_sec: float | None = None,
) -> None:
    """Remotion으로 투명 자막 오버레이 webm(VP8 알파) 렌더."""
    props = {
        "events": events,
        "fontSize": font_size,
        "marginBottom": margin_bottom,
        "width": width,
        "height": height,
        "fps": round(fps),
        "style": style,
        "palette": palette or {},
        "mode": mode,
    }
    if hook:
        props["hook"] = hook
    if duration_sec:
        # 이벤트가 없어도(예: 인트로 훅 배너만) 오버레이가 footage 전체를 덮도록.
        props["durationSec"] = round(duration_sec, 3)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False)
        props_path = f.name

    cmd = [
        str(REMOTION_BIN), "render", ENTRY, COMPOSITION, str(out_webm.resolve()),
        f"--props={props_path}",
        "--codec=vp8", "--image-format=png", "--pixel-format=yuva420p",
        # 고해상도(4K)·저코어 머신에서 모듈 레벨 폰트 delayRender가 기본 30s를 넘겨
        # "load-pretendard not cleared" 로 죽는다. 폰트 로드 여유를 위해 상향.
        "--timeout=120000",
        "--log=error",
    ]
    subprocess.run(cmd, cwd=REMOTION_DIR, check=True)
    Path(props_path).unlink(missing_ok=True)


def render_subtitled_remotion(
    cut_path: Path,
    captions: list[str],
    segments: list[dict],
    output: Path,
    font_size: int = 44,
    margin_bottom: int = 72,
    style: str = "fade",
    speakers: list[str] | None = None,
    palette: dict[str, str] | None = None,
    work_dir: Path | None = None,
    hook: str | None = None,
    mode: str = "longform",
    events: list[dict] | None = None,
) -> dict:
    """컷 영상에 Remotion 애니메이션 자막을 합성. subtitle.render_subtitled 대체.

    style: "fade"(전체 페이드) | "kinetic"(단어별 순차 등장)
    mode: "longform"(fade+키워드강조) | "shorts"(펀치 자막+상단 hook 배너)
    speakers: captions와 같은 길이의 화자 키 목록(옵션) → palette로 색 매핑
    palette: 화자 키 → hex (없는 화자는 이름 해시로 자동 배색)
    hook: 숏츠 상단 후킹 배너 문구(mode='shorts'에서만 표시)
    events: 자막 이벤트를 직접 주면 captions/segments로부터의 build_events를 생략.
            (숏츠 발화별 다중 이벤트처럼 1:1 매핑이 아닌 경우에 사용)

    Returns: {"output", "overlay", "events"}
    """
    if not REMOTION_BIN.exists():
        raise RuntimeError(f"Remotion 미설치: {REMOTION_BIN} 없음. remotion-map에서 npm install 필요.")

    w, h = probe_resolution(cut_path)
    fps = probe_fps(cut_path)
    if events is None:
        events = build_events(captions, segments, speakers=speakers)

    work_dir = work_dir or Path(tempfile.mkdtemp(prefix="vc_subs_remotion_"))
    work_dir.mkdir(parents=True, exist_ok=True)
    overlay = work_dir / "overlay.webm"

    # footage가 상한보다 크면 비례 축소해 렌더한다. fontSize/marginBottom은 절대 px라
    # 같은 비율로 줄여야 업스케일 후 룩이 보존된다. 치수는 yuv420p 요구로 짝수 클램프.
    scale = min(1.0, OVERLAY_MAX_HEIGHT / h)
    ow = round(w * scale) & ~1
    oh = round(h * scale) & ~1
    ofont = max(1, round(font_size * scale))
    omargin = round(margin_bottom * scale)

    # 1) Remotion 투명 오버레이 렌더 (축소 해상도/footage fps)
    # durationSec은 이벤트가 비어있을 때(인트로 훅 배너 전용) footage 길이를 보장한다.
    render_overlay_webm(events, ow, oh, fps, overlay, font_size=ofont, margin_bottom=omargin,
                        style=style, palette=palette, hook=hook, mode=mode,
                        duration_sec=probe_duration_sec(cut_path) if not events else None)

    # 2) ffmpeg overlay 합성 (VP8 알파 디코딩 위해 입력 앞에 -c:v libvpx, 오디오 보존).
    # 축소 렌더한 오버레이를 footage 크기로 업스케일(scale=1이면 무비용 통과) 후 합성.
    # 오버레이 webm은 calculateMetadata의 +0.3 패딩 탓에 footage보다 길 수 있으므로
    # 합성 길이를 footage(첫 입력)에 고정한다 — overlay=shortest=1로 비디오를 자른다.
    # setpts=PTS-STARTPTS가 없으면 출력 frame 0에 오버레이가 안 얹힌다
    # (숏츠는 첫 프레임이 커버라 배너 누락이 치명적).
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(cut_path),
        "-c:v", "libvpx", "-i", str(overlay),
        "-filter_complex",
        f"[1:v]setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos[ov];"
        f"[0:v][ov]overlay=format=auto:shortest=1",
        "-map", "0:a?", "-c:a", "copy",
        str(output),
    ]
    subprocess.run(cmd, check=True)

    return {"output": str(output), "overlay": str(overlay), "events": events}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="컷 영상에 Remotion 애니메이션 자막 합성")
    parser.add_argument("cut_path", type=Path, help="컷 영상")
    parser.add_argument("selection_json", type=Path, help="selection.json (segments: start/end)")
    parser.add_argument("captions_json", type=Path, help='["캡션1", ...] JSON')
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--font-size", type=int, default=44)
    parser.add_argument("--margin-bottom", type=int, default=72)
    parser.add_argument("--style", choices=["fade", "kinetic"], default="fade")
    parser.add_argument("--speakers-json", type=Path, default=None, help='["화자1", ...] captions와 같은 길이')
    parser.add_argument("--palette-json", type=Path, default=None, help='{"화자1": "#ffd166", ...}')
    args = parser.parse_args()

    segments = json.loads(args.selection_json.read_text())
    captions = json.loads(args.captions_json.read_text())
    speakers = json.loads(args.speakers_json.read_text()) if args.speakers_json else None
    palette = json.loads(args.palette_json.read_text()) if args.palette_json else None
    output = args.output or args.cut_path.with_name(args.cut_path.stem + "_subbed_remotion.mp4")

    result = render_subtitled_remotion(
        cut_path=args.cut_path,
        captions=captions,
        segments=segments,
        output=output,
        font_size=args.font_size,
        margin_bottom=args.margin_bottom,
        style=args.style,
        speakers=speakers,
        palette=palette,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
