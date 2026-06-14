#!/usr/bin/env python3
"""selection.json(원본 시점 컷 구간) → FCPXML 내보내기 (의존성 없음).

캡컷·DaVinci Resolve·Premiere가 읽는 FCPXML 1.9를 표준 라이브러리만으로 생성한다.
편집 결정(어디를 잘랐는지)을 영상이 아니라 타임라인 데이터로 넘겨, 후작업 자유도를 높인다.

자막은 FCPXML 자막 호환이 편집툴마다 들쭉날쭉하므로 여기 넣지 않는다(.srt 별도 권장).
컷 구간 name에 caption을 넣어 편집 시 참고용 라벨로만 쓴다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from xml.sax.saxutils import escape


def probe_video(path: Path) -> dict:
    """fps·해상도·길이 추출 (ffprobe)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    w, h, rate, dur = out[0], out[1], out[2], out[3]
    num, den = (int(x) for x in rate.split("/"))
    return {"width": int(w), "height": int(h),
            "fps_num": num, "fps_den": den, "duration": float(dur)}


def _fcpxml_format(meta: dict) -> tuple[str, str]:
    """(frameDuration, timebase 분모) — FCPXML 시간 표기에 쓰는 단위."""
    # frameDuration = fps_den/fps_num s  (예: 29.97 → 1001/30000s)
    return f"{meta['fps_den']}/{meta['fps_num']}s", meta["fps_num"]


def _sec_to_fcp(sec: float, meta: dict) -> str:
    """초 → FCPXML 시간문자열. 프레임 경계로 스냅(분자는 fps_den의 정수배)."""
    frames = round(sec * meta["fps_num"] / meta["fps_den"])
    return f"{frames * meta['fps_den']}/{meta['fps_num']}s"


def build_fcpxml(input_path: Path, segments: list[dict], meta: dict | None = None) -> str:
    """원본 + 컷 구간 리스트 → FCPXML 문자열."""
    meta = meta or probe_video(input_path)
    frame_dur, _ = _fcpxml_format(meta)
    src = f"file://{input_path.resolve()}"
    asset_dur = _sec_to_fcp(meta["duration"], meta)
    name = escape(input_path.stem)

    clips = []
    offset = 0.0  # 타임라인 누적 위치
    for seg in segments:
        d = seg["end"] - seg["start"]
        label = escape((seg.get("caption") or seg.get("reason") or "clip")[:40])
        clips.append(
            f'        <asset-clip ref="a1" name="{label}"'
            f' offset="{_sec_to_fcp(offset, meta)}"'
            f' start="{_sec_to_fcp(seg["start"], meta)}"'
            f' duration="{_sec_to_fcp(d, meta)}"'
            f' format="r1" tcFormat="NDF"/>'
        )
        offset += d
    seq_dur = _sec_to_fcp(offset, meta)

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" name="FFVideoFormat{meta['height']}p"
            frameDuration="{frame_dur}" width="{meta['width']}" height="{meta['height']}"
            colorSpace="1-1-1 (Rec. 709)"/>
    <asset id="a1" name="{name}" start="0s" duration="{asset_dur}"
           hasVideo="1" hasAudio="1" audioSources="1" audioChannels="2" format="r1">
      <media-rep kind="original-media" src="{src}"/>
    </asset>
  </resources>
  <library>
    <event name="auto-cut">
      <project name="{name}-autocut">
        <sequence format="r1" duration="{seq_dur}" tcStart="0s" tcFormat="NDF"
                  audioLayout="stereo" audioRate="48k">
          <spine>
{chr(10).join(clips)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
'''


def export_fcpxml(input_path: Path, segments: list[dict], out_path: Path,
                  meta: dict | None = None) -> Path:
    out_path.write_text(build_fcpxml(input_path, segments, meta), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="selection.json → FCPXML")
    ap.add_argument("input", type=Path, help="원본 영상")
    ap.add_argument("selection", type=Path, help="selection.json (컷 구간)")
    ap.add_argument("-o", "--output", type=Path, default=None)
    a = ap.parse_args()

    segs = json.loads(a.selection.read_text())
    out = a.output or a.input.with_suffix(".fcpxml")
    export_fcpxml(a.input, segs, out)
    print(f"wrote {out}")
