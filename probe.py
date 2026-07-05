"""probe.py — ffprobe 래퍼 모음.

영상/오디오 메타데이터 조회를 한 곳으로 모은다. auto_cut/effects/pipeline에
흩어져 있던 중복 ffprobe 호출을 통합해, 옵션 변경·버그 수정을 한 군데서 한다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def probe_duration(path: Path) -> float:
    """컨테이너 전체 길이(초)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def has_audio_stream(path: Path) -> bool:
    """입력에 오디오 스트림이 있는지. 무음/무오디오 영상에서 오디오 필터 회피용."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(probe.stdout.strip())


def has_video_stream(path: Path) -> bool:
    """실제 동영상 스트림이 있는지. .webm/.mkv 처럼 오디오만 담길 수 있는
    컨테이너를 영상/오디오로 정확히 가르기 위함. 정지 커버아트(attached_pic)는 영상이 아니다."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    for line in probe.stdout.splitlines():
        parts = line.split(",")
        # codec_type=video 이고 attached_pic(=1)이 아니면 실제 동영상
        if parts and parts[0] == "video" and (len(parts) < 2 or parts[1].strip() != "1"):
            return True
    return False


def resolution_from_probe(data: dict) -> tuple[int, int]:
    """ffprobe json → 표시 기준 (width, height).

    폰 촬영 영상은 가로로 저장하고 회전 메타(±90/270)로 세로 표시하는 경우가
    많다. ffmpeg 처리는 autorotate된 표시 기준으로 돌므로 회전을 반영해
    스왑한다 — 안 하면 세로 영상이 가로로 취급돼 자막·판별이 전부 어긋난다.
    """
    streams = data.get("streams") or []
    if not streams or "width" not in streams[0]:
        raise ValueError(f"해상도 파싱 실패: {data!r}")
    st = streams[0]
    w, h = int(st["width"]), int(st["height"])
    rotation = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(sd["rotation"])
            except (TypeError, ValueError):
                pass
            break
    if rotation == 0:  # 레거시 rotate 태그 폴백
        try:
            rotation = int((st.get("tags") or {}).get("rotate", 0))
        except (TypeError, ValueError):
            pass
    if abs(rotation) % 180 == 90:
        w, h = h, w
    return w, h


def probe_resolution(path: Path) -> tuple[int, int]:
    """첫 비디오 스트림의 표시 기준 (width, height) — 회전 메타 반영."""
    import json

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return resolution_from_probe(json.loads(result.stdout))
