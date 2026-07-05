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


def parse_resolution_csv(out: str) -> tuple[int, int]:
    """ffprobe csv 출력 → (width, height). 회전 side data가 있는 폰 영상은
    여분 필드/행이 붙을 수 있어 숫자 토큰만 추려 앞의 두 개를 쓴다."""
    nums = [p for p in out.replace("\n", ",").split(",") if p.strip().isdigit()]
    if len(nums) < 2:
        raise ValueError(f"해상도 파싱 실패: {out!r}")
    return int(nums[0]), int(nums[1])


def probe_resolution(path: Path) -> tuple[int, int]:
    """첫 비디오 스트림의 (width, height)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=,:p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return parse_resolution_csv(out)
