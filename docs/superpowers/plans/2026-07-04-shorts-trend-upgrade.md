# 숏츠 트렌드 업그레이드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 숏츠 산출물을 2026 쇼츠/릴스 트렌드 문법(첫 프레임 풀노출, 침묵 제거 점프컷, punch-in 교차, 카라오케 자막, hook 썸네일)에 맞게 업그레이드한다.

**Architecture:** ffmpeg=footage, Remotion=투명 자막 오버레이 역할 분담 유지. 신규 순수 모듈 `shorts_timeline.py`가 keep-interval과 remap의 단일 출처가 되고, footage 빌드(effects)와 자막 이벤트(pipeline)가 모두 이를 소비한다. words 없는 입력(scene/vision/구캐시)은 통짜 interval 폴백으로 기존 동작과 동일.

**Tech Stack:** Python 3 (표준 argparse/subprocess/PIL), ffmpeg, faster-whisper, Remotion(React/TS), pytest.

**Spec:** `docs/superpowers/specs/2026-07-04-shorts-trend-upgrade-design.md`

## Global Constraints

- 침묵 컷 기본값: `min_silence=0.45`, `pad=0.12`, `min_clip=0.6`, 총길이 3초 미만이면 통짜 폴백.
- punch-in: 컷마다 1.0x↔1.08x 교차. 무전사 폴백은 ~4초 주기 가상 컷.
- 숏츠 영상 페이드 전면 제거(첫 프레임 풀노출). 오디오만 끝 0.2초 페이드아웃.
- 구캐시(.transcript.json에 `words` 없음)·scene/vision에서 크래시 없이 현행 수준 산출 폴백.
- A/B 플래그: `--no-shorts-jumpcut`, `--no-shorts-punchin`, `--shorts-silence-min`, `--no-thumb-text`.
- 테스트는 순수 함수만 단위 테스트(외부 프로세스 금지 — tests/test_pure.py 철학 유지). 실행: `.venv` 활성화 후 `pytest tests/ -q`.
- 커밋 메시지는 기존 컨벤션(한국어, `feat:`/`fix:` prefix)을 따르고 Co-Authored-By 트레일러를 붙인다.

---

### Task 1: `shorts_timeline.py` — 타임라인 순수 모듈

**Files:**
- Create: `shorts_timeline.py`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Produces: `flatten_words(transcript_segments: list) -> list[dict]`, `snap_to_words(start, end, words) -> tuple[float, float]`, `cut_silences(start, end, words, min_silence=0.45, pad=0.12, min_clip=0.6) -> list[tuple]`, `Timeline(intervals)` (속성 `intervals`, `duration`, `cut_count`, `removed_sec`, 메서드 `remap(t_src) -> float`), `plan_short(start, end, transcript_segments, min_silence=0.45, jumpcut=True) -> Timeline`, `punch_plan(intervals, period=4.0) -> list[tuple]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_timeline.py` 생성:

```python
"""shorts_timeline 단위 테스트 — 단어 스냅·침묵 컷·remap·punch 계획 (외부 의존 없음)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts_timeline import (  # noqa: E402
    Timeline,
    cut_silences,
    flatten_words,
    plan_short,
    punch_plan,
    snap_to_words,
)

# 발화 2덩어리 사이에 1.0초 무음이 있는 합성 words (절대 시점)
WORDS = [
    {"word": "안녕하세요", "start": 10.0, "end": 10.6},
    {"word": "오늘은", "start": 10.7, "end": 11.1},
    {"word": "핵심만", "start": 11.2, "end": 11.8},
    # --- 1.0초 무음 ---
    {"word": "말씀드리면", "start": 12.8, "end": 13.5},
    {"word": "이겁니다", "start": 13.6, "end": 14.2},
]

SEGMENTS = [
    {"start": 10.0, "end": 11.8, "text": "안녕하세요 오늘은 핵심만",
     "words": WORDS[:3]},
    {"start": 12.8, "end": 14.2, "text": "말씀드리면 이겁니다",
     "words": WORDS[3:]},
]


def test_flatten_words_collects_and_sorts():
    out = flatten_words(SEGMENTS)
    assert [w["word"] for w in out] == ["안녕하세요", "오늘은", "핵심만", "말씀드리면", "이겁니다"]


def test_flatten_words_empty_for_legacy_cache():
    # 구캐시: words 키 없음 → 빈 목록 (크래시 없음)
    assert flatten_words([{"start": 0, "end": 5, "text": "안녕"}]) == []


def test_snap_to_words_expands_mid_word_boundaries():
    # 10.3은 '안녕하세요'(10.0~10.6) 중간 → 10.0으로, 13.9는 '이겁니다' 중간 → 14.2로
    assert snap_to_words(10.3, 13.9, WORDS) == (10.0, 14.2)


def test_snap_to_words_keeps_boundaries_in_gaps():
    # 무음 구간(11.9)과 단어 사이(10.65)는 그대로
    assert snap_to_words(10.65, 11.9, WORDS) == (10.65, 11.9)


def test_cut_silences_removes_long_gap_and_pads():
    out = cut_silences(10.0, 14.2, WORDS, min_silence=0.45, pad=0.12, min_clip=0.6)
    assert len(out) == 2
    (s1, e1), (s2, e2) = out
    assert s1 == 10.0            # 윈도우 시작 클램프 (10.0-0.12 < 10.0)
    assert abs(e1 - 11.92) < 1e-6  # 11.8 + pad
    assert abs(s2 - 12.68) < 1e-6  # 12.8 - pad
    assert e2 == 14.2            # 윈도우 끝 클램프


def test_cut_silences_keeps_short_gaps():
    # 단어 간 0.1초 간격은 무음 아님 → 통짜 1개
    out = cut_silences(10.0, 11.8, WORDS[:3], min_silence=0.45)
    assert out == [(10.0, 11.8 + 0.12)] or out == [(10.0, 11.8)]  # end는 윈도우 클램프
    assert len(out) == 1


def test_cut_silences_no_words_returns_whole():
    assert cut_silences(3.0, 20.0, []) == [(3.0, 20.0)]


def test_cut_silences_falls_back_when_total_too_short():
    # keep 총량 3초 미만 → 통짜 폴백
    words = [{"word": "짧다", "start": 5.0, "end": 5.4}]
    assert cut_silences(0.0, 30.0, words) == [(0.0, 30.0)]


def test_timeline_remap_within_and_across_gaps():
    tl = Timeline([(10.0, 12.0), (13.0, 15.0)])
    assert tl.duration == 4.0
    assert tl.remap(10.0) == 0.0
    assert tl.remap(11.0) == 1.0
    assert tl.remap(12.5) == 2.0   # 잘린 무음 → 다음 keep 시작으로 클램프
    assert tl.remap(14.0) == 3.0
    assert tl.remap(99.0) == 4.0   # 범위 밖 → duration 클램프
    assert tl.cut_count == 1
    assert abs(tl.removed_sec - 1.0) < 1e-6


def test_plan_short_without_words_is_single_interval():
    tl = plan_short(5.0, 30.0, [{"start": 0, "end": 40, "text": "x"}])
    assert tl.intervals == [(5.0, 30.0)]
    assert tl.cut_count == 0


def test_plan_short_jumpcut_false_is_single_interval():
    tl = plan_short(10.0, 14.2, SEGMENTS, jumpcut=False)
    assert tl.intervals == [(10.0, 14.2)]


def test_plan_short_snaps_then_cuts():
    tl = plan_short(10.3, 13.9, SEGMENTS)
    assert tl.cut_count == 1
    assert tl.intervals[0][0] == 10.0   # 스냅 결과
    assert tl.intervals[-1][1] == 14.2


def test_punch_plan_passthrough_for_multiple_intervals():
    ivs = [(0.0, 4.0), (5.0, 9.0)]
    assert punch_plan(ivs) == ivs


def test_punch_plan_splits_long_single_interval():
    out = punch_plan([(0.0, 10.0)], period=4.0)
    assert out[0] == (0.0, 4.0)
    assert out[-1][1] == 10.0
    assert all(e > s for s, e in out)
    # 연속성: 가상 컷은 시간을 버리지 않는다
    for (_, e1), (s2, _) in zip(out, out[1:]):
        assert abs(e1 - s2) < 1e-9


def test_punch_plan_short_single_interval_untouched():
    assert punch_plan([(0.0, 5.0)], period=4.0) == [(0.0, 5.0)]


def test_punch_plan_merges_tiny_tail():
    # 마지막 조각이 1초 미만이면 직전과 병합
    out = punch_plan([(0.0, 8.5)], period=4.0)
    assert out[-1][1] == 8.5
    assert out[-1][1] - out[-1][0] >= 1.0
```

- [ ] **Step 2: 실패 확인**

Run: `cd /home/ubuntu/workspace/video-automation && .venv/bin/python -m pytest tests/test_timeline.py -q` (`.venv`가 없으면 `python3 -m pytest`)
Expected: FAIL — `ModuleNotFoundError: No module named 'shorts_timeline'`

- [ ] **Step 3: 구현**

`shorts_timeline.py` 생성:

```python
"""숏츠 타임라인 계획 — 단어 경계 스냅·침묵 제거 점프컷·시점 remap (순수 함수).

트렌드 문법(dead-air 제거 점프컷)을 위해 숏츠 윈도우를 keep-interval 목록으로
쪼개고, footage(ffmpeg)와 자막(Remotion)이 같은 타임라인을 공유하도록 remap을
단일 출처로 제공한다. words가 없으면(scene/vision/구캐시 transcript) 통짜
interval 1개로 폴백해 기존 동작과 동일하다.
"""

from __future__ import annotations

MIN_TOTAL_SEC = 3.0  # 점프컷 결과 총길이가 이보다 짧으면 통짜로 폴백


def flatten_words(transcript_segments: list) -> list:
    """transcript segments → 절대 시점 단어 목록. words 없는 구캐시는 빈 목록."""
    words = []
    for seg in transcript_segments or []:
        for w in seg.get("words", []):
            text = str(w.get("word", "")).strip()
            if text and float(w["end"]) > float(w["start"]):
                words.append({"word": text, "start": float(w["start"]), "end": float(w["end"])})
    words.sort(key=lambda w: w["start"])
    return words


def snap_to_words(start: float, end: float, words: list) -> tuple:
    """윈도우 경계가 단어 중간에 걸리면 그 단어를 온전히 포함하도록 확장 스냅."""
    for w in words:
        if w["start"] < start < w["end"]:
            start = w["start"]
        if w["start"] < end < w["end"]:
            end = w["end"]
    return start, end


def cut_silences(
    start: float, end: float, words: list,
    min_silence: float = 0.45, pad: float = 0.12, min_clip: float = 0.6,
) -> list:
    """[start, end] 안에서 발화를 덮는 keep-interval 목록. min_silence+ 무음만 제거.

    단어를 무음 기준으로 클러스터링 → 각 클러스터에 pad를 두르고 윈도우로 클램프
    → min_clip 미만은 연장 → 겹치면 병합. keep 총량이 MIN_TOTAL_SEC 미만이면
    점프컷을 포기하고 통짜를 돌려준다 (산출물이 안 나오는 것보다 현행 수준이 낫다).
    """
    inside = [w for w in words if w["start"] < end and w["end"] > start]
    if not inside:
        return [(start, end)]

    clusters = [[inside[0]["start"], inside[0]["end"]]]
    for w in inside[1:]:
        if w["start"] - clusters[-1][1] < min_silence:
            clusters[-1][1] = max(clusters[-1][1], w["end"])
        else:
            clusters.append([w["start"], w["end"]])

    intervals: list = []
    for s, e in clusters:
        s = max(start, s - pad)
        e = min(end, e + pad)
        if e - s < min_clip:
            e = min(end, s + min_clip)
        if intervals and s <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], e))
        else:
            intervals.append((s, e))

    if sum(e - s for s, e in intervals) < MIN_TOTAL_SEC:
        return [(start, end)]
    return intervals


class Timeline:
    """keep-interval들이 이어붙은 새 타임라인. 원본 시점 → 새 시점 remap의 단일 출처."""

    def __init__(self, intervals: list):
        self.intervals = [(float(s), float(e)) for s, e in intervals]
        self._offsets = []
        t = 0.0
        for s, e in self.intervals:
            self._offsets.append(t)
            t += e - s
        self.duration = t

    def remap(self, t_src: float) -> float:
        """원본 시점 → 새 타임라인 시점. 잘린 무음 구간은 다음 keep 시작으로 클램프."""
        for (s, e), off in zip(self.intervals, self._offsets):
            if t_src < s:
                return off
            if t_src <= e:
                return off + (t_src - s)
        return self.duration

    @property
    def cut_count(self) -> int:
        return len(self.intervals) - 1

    @property
    def removed_sec(self) -> float:
        span = self.intervals[-1][1] - self.intervals[0][0]
        return max(0.0, span - self.duration)


def plan_short(
    start: float, end: float, transcript_segments: list | None,
    min_silence: float = 0.45, jumpcut: bool = True,
) -> Timeline:
    """숏츠 윈도우 → Timeline. words 없거나 jumpcut=False면 통짜 1개."""
    words = flatten_words(transcript_segments or [])
    if not words or not jumpcut:
        return Timeline([(start, end)])
    start, end = snap_to_words(start, end, words)
    return Timeline(cut_silences(start, end, words, min_silence=min_silence))


def punch_plan(intervals: list, period: float = 4.0) -> list:
    """punch-in 교차용 서브클립 경계. 점프컷이 있으면 그 경계를 그대로 쓰고,
    통짜 1개가 period*1.5보다 길면 period 간격의 가상 컷으로 쪼갠다(시간 손실 없음).
    """
    if len(intervals) != 1:
        return list(intervals)
    s, e = intervals[0]
    if e - s < period * 1.5:
        return [(s, e)]
    clips = []
    t = s
    while t < e - 1e-9:
        clips.append((t, min(e, t + period)))
        t += period
    if len(clips) >= 2 and clips[-1][1] - clips[-1][0] < 1.0:
        clips[-2] = (clips[-2][0], clips[-1][1])
        clips.pop()
    return clips
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_timeline.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 기존 테스트 회귀 확인 후 커밋**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS

```bash
git add shorts_timeline.py tests/test_timeline.py
git commit -m "feat(shorts): 타임라인 순수 모듈 — 단어 스냅·침묵 컷·remap·punch 계획

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Whisper 단어 타임스탬프

**Files:**
- Modify: `auto_cut.py:292-312` (`transcribe_video`)

**Interfaces:**
- Produces: transcript segment에 `words: [{"word", "start", "end"}]` 키 추가(단어 인식 시). 구 스키마 소비자는 `.get("words")`로 접근하므로 하위 호환.

- [ ] **Step 1: `transcribe_video` 수정**

`auto_cut.py`의 `transcribe_video`에서 `model.transcribe(...)` 호출과 segments 변환을 다음으로 교체:

```python
    segments, info = model.transcribe(
        str(video_path),
        language=language if language != "auto" else None,
        word_timestamps=True,  # 숏츠 카라오케 자막·점프컷용 단어 경계
    )

    transcript_segments = []
    for s in segments:
        item = {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        if s.words:
            item["words"] = [
                {"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                for w in s.words if w.word.strip()
            ]
        transcript_segments.append(item)
```

(기존 리스트 컴프리헨션 삭제. `return {...}` 부분은 그대로.)

- [ ] **Step 2: 임포트 스모크 + 기존 테스트**

Run: `.venv/bin/python -c "import auto_cut" && .venv/bin/python -m pytest tests/ -q`
Expected: import 에러 없음, 전부 PASS (transcribe는 외부 의존이라 단위 테스트 없음 — E2E는 Task 9)

- [ ] **Step 3: 커밋**

```bash
git add auto_cut.py
git commit -m "feat(speech): Whisper word_timestamps 활성화 — transcript에 words 추가

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: footage 빌드 + 오디오 페이드 (effects.py)

**Files:**
- Modify: `effects.py` (파일 끝 CLI 블록 앞에 함수 2개 추가)

**Interfaces:**
- Consumes: 없음 (ffmpeg 직접 호출, `probe.py` 헬퍼)
- Produces: `build_short_footage(input_path: Path, clips: list[tuple], output_path: Path, punchin: bool = True, punch_scale: float = 1.08) -> None`, `apply_audio_fade_out(input_path: Path, output_path: Path, fade: float = 0.2) -> None`

- [ ] **Step 1: 함수 추가**

`effects.py`의 `# BGM mux` 섹션 앞에 추가:

```python
# ============================================================================
# Shorts footage — 점프컷 서브클립 + punch-in 교차 (페이드 없음: 첫 프레임=커버)
# ============================================================================

def build_short_footage(
    input_path: Path,
    clips: list[tuple],
    output_path: Path,
    punchin: bool = True,
    punch_scale: float = 1.08,
) -> None:
    """clips(원본 시점 (s, e) 목록)를 잘라 concat. 홀수번째 클립에 punch-in.

    점프컷 경계와 punch 가상 컷이 모두 clips로 들어온다. 컷 경계에서
    1.0x↔punch_scale 크롭 줌을 교차해 점프컷을 의도된 리듬으로 보이게 한다.
    페이드인 없음 — 숏츠 첫 프레임이 곧 커버(트렌드 표준).
    """
    w, h = probe_resolution(input_path)
    w -= w % 2
    h -= h % 2
    tmpdir = output_path.parent / f".{output_path.stem}_short_tmp"
    tmpdir.mkdir(exist_ok=True)
    try:
        paths = []
        for i, (s, e) in enumerate(clips):
            cp = tmpdir / f"clip_{i:03d}.mp4"
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-ss", f"{s:.3f}", "-i", str(input_path), "-t", f"{e - s:.3f}"]
            if punchin and i % 2 == 1:
                cmd += ["-vf", f"crop=iw/{punch_scale}:ih/{punch_scale},scale={w}:{h}"]
            else:
                cmd += ["-vf", f"scale={w}:{h}"]  # concat을 위한 해상도 통일
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
```

- [ ] **Step 2: ffmpeg 스모크 (합성 영상으로 직접 검증)**

```bash
cd /home/ubuntu/workspace/video-automation
mkdir -p /tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/fx
S=/tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/fx
ffmpeg -y -loglevel error \
  -f lavfi -i "testsrc=size=640x360:rate=30:duration=12" \
  -f lavfi -i "sine=frequency=440:duration=12" \
  -c:v libx264 -c:a aac -pix_fmt yuv420p "$S/src.mp4"
.venv/bin/python - <<'EOF'
from pathlib import Path
from effects import build_short_footage, apply_audio_fade_out
from probe import probe_duration
S = Path("/tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/fx")
build_short_footage(S/"src.mp4", [(0.0, 3.0), (5.0, 8.0), (9.0, 11.0)], S/"foot.mp4")
d = probe_duration(S/"foot.mp4")
assert abs(d - 8.0) < 0.3, d
apply_audio_fade_out(S/"foot.mp4", S/"final.mp4")
assert abs(probe_duration(S/"final.mp4") - d) < 0.2
print("OK", d)
EOF
```

Expected: `OK 8.0…` (keep 3+3+2초 = 8초, punch 클립 포함 concat 성공)

- [ ] **Step 3: 커밋**

```bash
git add effects.py
git commit -m "feat(shorts): 점프컷 footage 빌더(punch-in 교차) + 오디오 전용 페이드아웃

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: shorts_events 재작성 — Timeline·카라오케 words (pipeline.py)

**Files:**
- Modify: `pipeline.py:341-363` (`shorts_events`)
- Test: `tests/test_pure.py:195-233` (기존 shorts_events 테스트 4개 수정 + 신규)

**Interfaces:**
- Consumes: Task 1의 `Timeline` (`intervals`, `duration`, `remap`)
- Produces: `shorts_events(spec: dict, transcript: dict | None, timeline: Timeline) -> list[dict]` — 이벤트는 `{"text", "start", "end"}` + 카라오케 가능 시 `"words": [{"text", "start", "end"}]`(이벤트 상대시간)

- [ ] **Step 1: 실패하는 테스트로 기존 테스트 교체**

`tests/test_pure.py`의 `shorts_events` 테스트 4개(195~233행 부근)를 다음으로 교체하고, 파일 상단 import에 `from shorts_timeline import Timeline` 추가:

```python
def _tl(*intervals):
    return Timeline(list(intervals))


def test_shorts_events_remaps_transcript_to_multiple_events():
    transcript = {"segments": [
        {"start": 10, "end": 13, "text": "첫 발화"},
        {"start": 13, "end": 16, "text": "둘째 발화"},
    ]}
    spec = {"start": 10.0, "end": 16.0, "caption": "폴백"}
    events = shorts_events(spec, transcript, _tl((10.0, 16.0)))
    assert len(events) == 2
    assert events[0]["start"] == 0.0 and events[0]["text"] == "첫 발화"
    assert events[1]["start"] == 3.0
    # 마지막 이벤트 end는 숏츠 끝까지 연장
    assert events[1]["end"] >= 6.0 - 0.05 - 1e-9


def test_shorts_events_jumpcut_timeline_shifts_later_events():
    # 12~14가 잘린 타임라인: 14초의 발화가 새 타임라인 2초로 당겨짐
    transcript = {"segments": [
        {"start": 10, "end": 12, "text": "앞"},
        {"start": 14, "end": 16, "text": "뒤"},
    ]}
    spec = {"start": 10.0, "end": 16.0, "caption": ""}
    events = shorts_events(spec, transcript, _tl((10.0, 12.0), (14.0, 16.0)))
    assert events[0]["start"] == 0.0
    assert events[1]["start"] == 2.0   # remap(14.0)


def test_shorts_events_karaoke_words_relative_to_event():
    transcript = {"segments": [
        {"start": 10, "end": 12, "text": "안녕 하세요",
         "words": [
             {"word": "안녕", "start": 10.2, "end": 10.8},
             {"word": "하세요", "start": 11.0, "end": 11.8},
         ]},
    ]}
    spec = {"start": 10.0, "end": 12.0, "caption": ""}
    [ev] = shorts_events(spec, transcript, _tl((10.0, 12.0)))
    assert ev["text"] == "안녕 하세요"      # text는 words에서 파생 (단일 출처)
    assert ev["start"] == 0.2               # 첫 단어 시작
    ws = ev["words"]
    assert ws[0] == {"text": "안녕", "start": 0.0, "end": 0.6}
    assert ws[1]["start"] == 0.8            # 11.0 remap → 1.0, 이벤트 상대 → 0.8


def test_shorts_events_words_strip_leading_fillers():
    transcript = {"segments": [
        {"start": 0, "end": 3, "text": "어 그러니까 본론은",
         "words": [
             {"word": "어", "start": 0.0, "end": 0.3},
             {"word": "그러니까", "start": 0.4, "end": 1.0},
             {"word": "본론은", "start": 1.2, "end": 2.0},
         ]},
    ]}
    spec = {"start": 0.0, "end": 3.0, "caption": ""}
    [ev] = shorts_events(spec, transcript, _tl((0.0, 3.0)))
    assert ev["text"] == "본론은"
    assert len(ev["words"]) == 1


def test_shorts_events_falls_back_to_caption_without_transcript():
    spec = {"start": 5.0, "end": 15.0, "caption": "캡션 폴백"}
    events = shorts_events(spec, None, _tl((5.0, 15.0)))
    assert events == [{"text": "캡션 폴백", "start": 0.0, "end": 10.0}]


def test_shorts_events_empty_when_no_transcript_and_no_caption():
    spec = {"start": 5.0, "end": 15.0, "caption": ""}
    assert shorts_events(spec, None, _tl((5.0, 15.0))) == []


def test_shorts_events_falls_back_when_transcript_has_no_overlap():
    transcript = {"segments": [{"start": 100, "end": 110, "text": "다른 데"}]}
    spec = {"start": 5.0, "end": 15.0, "caption": "캡션"}
    events = shorts_events(spec, transcript, _tl((5.0, 15.0)))
    assert events == [{"text": "캡션", "start": 0.0, "end": 10.0}]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_pure.py -q -k shorts_events`
Expected: FAIL (TypeError — timeline 인자 없음)

- [ ] **Step 3: `shorts_events` 재작성**

`pipeline.py`에서 기존 `shorts_events`를 다음으로 교체. import에 `from shorts_timeline import plan_short, punch_plan` 추가, auto_cut import 목록에서 `remap_transcript_to_cuts` 제거(이 교체로 pipeline에서 미사용 — auto_cut 자체와 test_pure의 직접 import는 그대로):

```python
def _event_words(tseg: dict, lo: float, hi: float) -> list:
    """이벤트에 넣을 단어 목록 — 윈도우와 겹치는 단어에서 선두 추임새 제거.

    text와 words가 어긋나지 않도록, words가 있으면 text도 words에서 파생한다.
    """
    ws = [w for w in tseg.get("words", []) if w["start"] < hi and w["end"] > lo]
    while ws and ws[0]["word"].strip() in _FILLERS:
        ws = ws[1:]
    return ws


def shorts_events(spec: dict, transcript: dict | None, timeline) -> list:
    """숏츠 타임라인(점프컷 반영) 기준 자막 이벤트. words가 있으면 카라오케 타이밍 포함.

    모든 시점은 timeline.remap을 통과한다 — footage와 자막의 단일 출처.
    transcript 없음(scene/vision)·구캐시(words 없음)·겹침 없음 어느 쪽도
    크래시하지 않고 caption 단일 이벤트 또는 균일 stagger로 폴백한다.
    """
    lo = timeline.intervals[0][0]
    hi = timeline.intervals[-1][1]
    events = []
    for t in (transcript or {}).get("segments") or []:
        if t["end"] <= lo or t["start"] >= hi:
            continue
        ws = _event_words(t, lo, hi)
        if ws:
            ns = timeline.remap(ws[0]["start"])
            ne = timeline.remap(ws[-1]["end"])
            if ne - ns < 0.1:
                continue
            ev_words = [
                {"text": w["word"].strip(),
                 "start": round(max(0.0, timeline.remap(w["start"]) - ns), 3),
                 "end": round(max(0.0, timeline.remap(w["end"]) - ns), 3)}
                for w in ws
            ]
            events.append({
                "text": " ".join(w["text"] for w in ev_words),
                "start": round(ns, 3), "end": round(ne, 3), "words": ev_words,
            })
        else:
            text = strip_leading_fillers(t["text"].strip())
            if not text:
                continue
            ns = timeline.remap(max(t["start"], lo))
            ne = timeline.remap(min(t["end"], hi))
            if ne - ns < 0.1:
                continue
            events.append({"text": text, "start": round(ns, 3), "end": round(ne, 3)})
    if events:
        events[-1]["end"] = max(events[-1]["end"], round(timeline.duration - _SHORTS_PADDED_TAIL, 3))
        return events
    cap = spec.get("caption", "").strip()
    if cap:
        return [{"text": cap, "start": 0.0, "end": round(timeline.duration, 3)}]
    return []
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add pipeline.py tests/test_pure.py
git commit -m "feat(shorts): 자막 이벤트를 Timeline remap 기반으로 — 카라오케 words 포함

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: build_one_short 재작성 — 점프컷·punch·페이드 제거·측정 (pipeline.py)

**Files:**
- Modify: `pipeline.py:366-402` (`build_one_short`), `pipeline.py:559-583` (argparse 옵션), `pipeline.py` import 블록

**Interfaces:**
- Consumes: Task 1 `plan_short`/`punch_plan`, Task 3 `build_short_footage`/`apply_audio_fade_out`, Task 4 `shorts_events(spec, transcript, timeline)`
- Produces: 신규 args 속성 — `shorts_silence_min: float`, `no_shorts_jumpcut: bool`, `no_shorts_punchin: bool` (Task 8의 web도 이 이름을 그대로 채움)

- [ ] **Step 1: import 및 build_one_short 교체**

`pipeline.py`의 effects import에 `build_short_footage, apply_audio_fade_out` 추가, `apply_fade`는 인트로가 계속 쓰므로 유지. `build_one_short`를 다음으로 교체:

```python
def build_one_short(args, spec: dict, stem: str, outdir: Path, transcript=None) -> Path:
    """단일 숏츠: 타임라인 계획(점프컷) → footage(punch-in) → 세로 → 자막 → 오디오 페이드.

    영상 페이드는 넣지 않는다 — 첫 프레임이 곧 커버(트렌드). 측정치(무음 제거·컷 수·
    초당 화면변화)를 출력해 A/B 비교의 근거를 남긴다.
    """
    tl = plan_short(
        spec["start"], spec["end"],
        (transcript or {}).get("segments"),
        min_silence=args.shorts_silence_min,
        jumpcut=not args.no_shorts_jumpcut,
    )
    clips = list(tl.intervals) if args.no_shorts_punchin else punch_plan(tl.intervals)

    raw = outdir / f".{stem}_raw.mp4"
    vert = outdir / f".{stem}_vert.mp4"
    subbed = outdir / f".{stem}_sub.mp4"
    final = outdir / f"{stem}.mp4"
    try:
        build_short_footage(args.input, clips, raw, punchin=not args.no_shorts_punchin)
        reframe_vertical(raw, vert, blur_bg=args.shorts_blur)

        events = shorts_events(spec, transcript, tl)
        hook = (spec.get("hook") or spec.get("caption", "")).strip() or None
        if args.no_subtitle or not events:
            src = vert
        elif args.sub_engine == "remotion":
            render_subtitled_remotion(
                cut_path=vert, output=subbed, captions=[], segments=[],
                events=events, hook=hook, mode="shorts",
                font_size=_SHORTS_FONT_SIZE, margin_bottom=_SHORTS_MARGIN_BOTTOM,
            )
            src = subbed
        else:
            print("  ⚠ PIL 엔진은 숏츠 펀치 자막/hook 배너를 지원하지 않습니다(정적 캡션).")
            cap = events[0]["text"]
            seg0 = {"start": 0.0, "end": tl.duration}
            render_subtitled(
                cut_path=vert, captions=[cap], segments=[seg0], output=subbed,
                font_size=args.sub_font_size + 8, margin_v=args.sub_margin_v + 120,
                max_caption_width=960,
            )
            src = subbed
        apply_audio_fade_out(src, final)

        changes = (len(clips) - 1) + len(events)
        print(f"  {stem}: {tl.duration:.1f}s | 무음 제거 {tl.removed_sec:.1f}s · "
              f"점프컷 {tl.cut_count} · 화면변화 {changes / max(tl.duration, 0.1):.1f}/s")
        return final
    finally:
        for tmp in (raw, vert, subbed):
            tmp.unlink(missing_ok=True)
```

- [ ] **Step 2: argparse 옵션 추가**

`# 숏츠` 옵션 블록에 추가:

```python
    parser.add_argument("--shorts-silence-min", type=float, default=0.45,
                        help="점프컷으로 제거할 최소 무음 길이(초)")
    parser.add_argument("--no-shorts-jumpcut", action="store_true",
                        help="침묵 제거 점프컷 끄기 (A/B 비교용)")
    parser.add_argument("--no-shorts-punchin", action="store_true",
                        help="컷 경계 punch-in 줌 끄기 (A/B 비교용)")
```

- [ ] **Step 3: 회귀 + CLI 파싱 스모크**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/python pipeline.py --help | grep -A1 shorts-silence`
Expected: 테스트 전부 PASS, 새 옵션이 help에 노출

- [ ] **Step 4: 커밋**

```bash
git add pipeline.py
git commit -m "feat(shorts): 점프컷+punch-in 조립 라인 — 페이드인 제거, A/B 플래그, 측정 출력

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Remotion — 카라오케 단어 타이밍 + hook 배너 frame 0 노출

**Files:**
- Modify: `remotion-map/src/SubtitleOverlay.tsx`

**Interfaces:**
- Consumes: Task 4 이벤트 스키마 `words?: {text, start, end}[]` (이벤트 상대 초)
- Produces: 없음 (렌더 결과만)

- [ ] **Step 1: SubEvent 타입에 words 추가**

```tsx
export type SubEvent = {
  text: string;
  start: number;
  end: number;
  speaker?: string; // 화자 키(색 매핑). 없으면 기본색
  style?: 'fade' | 'kinetic'; // 이벤트별 스타일 override
  words?: { text: string; start: number; end: number }[]; // 카라오케 단어 타이밍(이벤트 상대 초)
};
```

- [ ] **Step 2: shorts 모드 Caption을 실제 발화 타이밍으로**

`Caption` 컴포넌트의 `if (mode === 'shorts')` 블록을 다음으로 교체 (words 없으면 현행 균일 stagger 유지):

```tsx
  // 숏츠: 카라오케 — 실제 발화 시점에 단어 등장(words). 없으면 균일 stagger 폴백.
  if (mode === 'shorts') {
    const boxIn = spring({ frame, fps, config: { damping: 20, mass: 0.5 }, durationInFrames: 8 });
    const stagger = 2;
    const words: { text: string; startFrame: number }[] = ev.words?.length
      ? ev.words.map((w) => ({ text: w.text, startFrame: Math.round(w.start * fps) }))
      : ev.text.split(/\s+/).filter(Boolean).map((t, i) => ({ text: t, startFrame: i * stagger }));
    return (
      <Pill fontSize={fontSize} marginBottom={marginBottom}
        containerOpacity={Math.min(boxIn, exit)} containerY={interpolate(boxIn, [0, 1], [20, 0])}>
        {words.map((w, i) => {
          const wf = frame - w.startFrame;
          // 스케일 펀치: 0.7 → 1.06 → 1.0 안착 (~8프레임), 1.1배 초과 금지
          const sc = interpolate(wf, [0, 5, 8], [0.7, 1.06, 1.0], {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
          });
          const op = interpolate(wf, [0, 4], [0, 1], {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
          });
          const hot = isAccentToken(w.text);
          return (
            <span key={i} style={{
              display: 'inline-block', marginRight: '0.28em',
              opacity: op, transform: `scale(${Math.min(sc, 1.1)})`,
              ...(hot ? { color: ACCENT } : accent ? { color: accent } : {}),
            }}>
              {w.text}
            </span>
          );
        })}
      </Pill>
    );
  }
```

- [ ] **Step 3: HookBanner를 frame 0 완전 노출로**

`HookBanner`에서 spring 슬라이드인 제거 — `useCurrentFrame`/`useVideoConfig`/`spring` 사용 부분과 `opacity`/`transform` 라인을 삭제해 정적 렌더로:

```tsx
const HookBanner: React.FC<{ hook: string; fontSize: number; height: number }> = ({
  hook, fontSize, height,
}) => {
  // 첫 프레임이 곧 커버 — 슬라이드인 없이 frame 0부터 완전 노출 (트렌드 표준)
  const top = Math.round(height * 0.13); // 상단 10% 세이프존 비우고 13% 지점
  const lineHeight = 1.2;
  return (
    <AbsoluteFill style={{ justifyContent: 'flex-start', alignItems: 'center' }}>
      <div
        style={{
          position: 'absolute',
          top,
          maxWidth: 'calc(100% - 100px)',
          background: '#FFE14D',
          borderRadius: 16,
          padding: '18px 32px',
          color: '#111',
          fontFamily: FONT,
          fontSize,
          fontWeight: 800,
          lineHeight,
          textAlign: 'center',
          boxShadow: '0 6px 20px rgba(0,0,0,0.35)',
        }}
      >
        <span
          style={{
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            maxHeight: `${Math.round(fontSize * lineHeight * 2)}px`,
          }}
        >
          {highlightSpans(hook, 99).map((s, i) => (
            <span key={i} style={s.accent ? { color: BANNER_HIGHLIGHT, whiteSpace: 'nowrap' } : undefined}>{s.word}</span>
          ))}
        </span>
      </div>
    </AbsoluteFill>
  );
};
```

- [ ] **Step 4: 타입체크**

Run: `cd remotion-map && npx tsc --noEmit && cd ..`
Expected: 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add remotion-map/src/SubtitleOverlay.tsx
git commit -m "feat(remotion): 숏츠 카라오케 자막(단어별 실제 발화 타이밍) + hook 배너 frame0 노출

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 썸네일 hook 텍스트 + 숏츠 세로 커버

**Files:**
- Modify: `effects.py` (`extract_thumbnail` 아래에 `overlay_hook_text` 추가), `pipeline.py` (`pick_thumbnail_hook` 신규, `build_thumbnail` 수정, `build_one_short` 끝에 커버 추출, `--no-thumb-text` 옵션)
- Test: `tests/test_pure.py` (`pick_thumbnail_hook`)

**Interfaces:**
- Consumes: `subtitle.find_korean_font()` (이미 effects가 import), Task 5의 `build_one_short`
- Produces: `overlay_hook_text(image_path: Path, text: str) -> None` (in-place burn-in), `pick_thumbnail_hook(segments: list, captions: list) -> str`

- [ ] **Step 1: 실패하는 테스트 (pick_thumbnail_hook)**

`tests/test_pure.py`에 추가 (import에 `pick_thumbnail_hook` 추가):

```python
def test_pick_thumbnail_hook_prefers_top_score_hook():
    segments = [
        {"start": 0, "end": 10, "score": 40, "hook": "낮은 훅"},
        {"start": 20, "end": 30, "score": 90, "hook": "강한 훅"},
    ]
    assert pick_thumbnail_hook(segments, ["a", "b"]) == "강한 훅"


def test_pick_thumbnail_hook_falls_back_to_caption_then_empty():
    segments = [{"start": 0, "end": 10}]
    assert pick_thumbnail_hook(segments, ["캡션"]) == "캡션"
    assert pick_thumbnail_hook([{"start": 0, "end": 1}], [""]) == ""
```

Run: `.venv/bin/python -m pytest tests/test_pure.py -q -k thumbnail_hook`
Expected: FAIL (ImportError)

- [ ] **Step 2: 구현 — effects.overlay_hook_text**

`effects.py`의 `extract_thumbnail` 아래에 추가:

```python
def overlay_hook_text(image_path: Path, text: str) -> None:
    """썸네일에 hook 문구를 burn-in — 숏츠 자막과 같은 룩(ExtraBold+검정 외곽선).

    하단 12% 여백 위에 중앙 정렬, 폭 88%를 넘으면 단어 단위 줄바꿈(최대 2줄).
    """
    if not text.strip():
        return
    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    font_path, font_index = find_korean_font()
    size = max(28, W // 14)
    try:
        font = ImageFont.truetype(font_path, size, index=font_index)
    except (OSError, IndexError):
        font = ImageFont.truetype(font_path, size)
    draw = ImageDraw.Draw(img)

    max_w = int(W * 0.88)
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
        if len(lines) == 2:
            break
    if cur and len(lines) < 2:
        lines.append(cur)

    stroke = max(2, size // 12)
    line_h = int(size * 1.25)
    y = H - int(H * 0.12) - line_h * len(lines)
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text(((W - tw) / 2, y), line, font=font, fill=(255, 255, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h
    img.save(image_path, quality=92)
```

- [ ] **Step 3: 구현 — pipeline 연결**

`pipeline.py`의 `pick_thumbnail_times` 아래에 추가:

```python
def pick_thumbnail_hook(segments: list, captions: list) -> str:
    """썸네일에 얹을 hook 문구 — 최고 점수 구간의 hook 우선, 없으면 첫 비어있지 않은 캡션."""
    scored = [s for s in segments if s.get("score") is not None and s.get("hook")]
    if scored:
        return max(scored, key=lambda s: float(s["score"]))["hook"]
    for s in segments:
        if s.get("hook"):
            return s["hook"]
    for c in captions:
        if c.strip():
            return c.strip()
    return ""
```

`build_thumbnail`을 다음으로 교체 (effects import에 `overlay_hook_text` 추가):

```python
def build_thumbnail(args, segments: list, captions: list, outdir: Path) -> list:
    """대표 구간들에서 후보 N장 추출 + hook 문구 burn-in(--no-thumb-text로 끔)."""
    times = pick_thumbnail_times(segments, args.thumbnail_count)
    hook = "" if args.no_thumb_text else pick_thumbnail_hook(segments, captions)
    paths = []
    for n, at in enumerate(times, 1):
        name = "thumbnail.jpg" if len(times) == 1 else f"thumbnail_{n:02d}.jpg"
        out = outdir / name
        extract_thumbnail(args.input, out, at, grade=not args.no_grade)
        if hook:
            overlay_hook_text(out, hook)
        paths.append(out)
    return paths
```

`run()`의 썸네일 step 호출을 `build_thumbnail(args, segments, captions, outdir)`로 수정.

`build_one_short`의 `apply_audio_fade_out(src, final)` 다음 줄에 세로 커버 추출 추가:

```python
        # 릴스 커버용 세로 썸네일 — hook 배너가 이미 박힌 첫 장면
        extract_thumbnail(final, outdir / f"{stem}_cover.jpg", at_sec=0.1, grade=False)
```

argparse `# 썸네일` 블록에 추가:

```python
    parser.add_argument("--no-thumb-text", action="store_true", help="썸네일 hook 문구 burn-in 생략")
```

- [ ] **Step 4: 테스트 + PIL 스모크**

```bash
.venv/bin/python -m pytest tests/ -q
S=/tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/fx
.venv/bin/python - <<'EOF'
from pathlib import Path
from PIL import Image
from effects import overlay_hook_text
S = Path("/tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/fx")
p = S / "thumb.jpg"
Image.new("RGB", (1280, 720), (40, 60, 90)).save(p)
overlay_hook_text(p, "이 한 문장이 조회수를 바꿉니다 진짜로")
print("OK", Image.open(p).size)
EOF
```

Expected: 테스트 PASS, `OK (1280, 720)` — 이미지를 열어 텍스트가 하단 중앙에 외곽선과 함께 보이는지 확인

- [ ] **Step 5: 커밋**

```bash
git add effects.py pipeline.py tests/test_pure.py
git commit -m "feat(thumbnail): hook 문구 burn-in + 숏츠별 세로 커버(shorts_NN_cover.jpg)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: web args + README 문서화

**Files:**
- Modify: `web/app.py:99-115` (`_args_from_opts`), `README.md` (숏츠 산출 행·주요 옵션·자막 엔진 섹션)

**Interfaces:**
- Consumes: Task 5·7의 args 속성 이름 (`shorts_silence_min`, `no_shorts_jumpcut`, `no_shorts_punchin`, `no_thumb_text`)

- [ ] **Step 1: `_args_from_opts`에 신규 기본값 추가**

`SimpleNamespace(...)` 인자에 추가 (없으면 pipeline에서 AttributeError):

```python
        shorts_silence_min=0.45, no_shorts_jumpcut=False, no_shorts_punchin=False,
        no_thumb_text=False,
```

- [ ] **Step 2: README 갱신**

- `shorts_NN.mp4` 행을 다음으로 교체:
  `| shorts_NN.mp4 | 세로 9:16 | 임팩트 상위 N개, **침묵 제거 점프컷+punch-in 교차**, 카라오케 자막, 첫 프레임 풀노출(페이드인 없음) |`
- 썸네일 행에 `hook 문구 burn-in` 추가, 산출 표 아래에 `shorts_NN_cover.jpg`(릴스 커버) 한 줄 추가.
- 주요 옵션 목록에 `--shorts-silence-min`(기본 0.45), `--no-shorts-jumpcut`, `--no-shorts-punchin`, `--no-thumb-text` 추가.
- 자막 엔진 섹션의 숏츠 설명에 "words(단어 타임스탬프)가 있으면 실제 발화 타이밍 카라오케, 구캐시는 균일 등장 폴백" 한 줄 추가.

- [ ] **Step 3: 웹 임포트 스모크 + 커밋**

Run: `.venv/bin/python -c "import sys; sys.path.insert(0,'web'); import app" 2>&1 | tail -1` (FastAPI 미설치 환경이면 skip하고 문법만: `.venv/bin/python -m py_compile web/app.py`)
Expected: 에러 없음

```bash
git add web/app.py README.md
git commit -m "docs+web: 숏츠 트렌드 옵션 문서화 및 웹 기본값 연결

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: E2E 스모크 — scene 모드 폴백 경로 검증

**Files:**
- 없음 (검증 전용 — 산출물은 scratchpad)

**Interfaces:**
- Consumes: 전체 파이프라인

- [ ] **Step 1: 씬 체인지가 있는 합성 영상 생성**

```bash
S=/tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/e2e
mkdir -p "$S"
ffmpeg -y -loglevel error \
  -f lavfi -i "color=red:size=1280x720:rate=30:duration=8" \
  -f lavfi -i "color=blue:size=1280x720:rate=30:duration=8" \
  -f lavfi -i "color=green:size=1280x720:rate=30:duration=8" \
  -f lavfi -i "sine=frequency=440:duration=24" \
  -filter_complex "[0:v][1:v][2:v]concat=n=3:v=1[v]" \
  -map "[v]" -map 3:a -c:v libx264 -c:a aac -pix_fmt yuv420p "$S/synthetic.mp4"
```

- [ ] **Step 2: scene 모드 숏츠 생성 (무전사 폴백 + punch 주기 + 페이드 제거 경로)**

```bash
cd /home/ubuntu/workspace/video-automation
.venv/bin/python pipeline.py "$S/synthetic.mp4" --mode scene --scene-threshold 0.1 \
  --only shorts --shorts-count 1 -t 0.5 -o "$S/out"
```

Expected: `shorts_01.mp4`·`shorts_01_cover.jpg` 생성, 측정 라인(`무음 제거 0.0s · 점프컷 0 · 화면변화 …`) 출력, exit 0

- [ ] **Step 3: 첫 프레임이 검정이 아닌지 확인 (페이드인 제거 검증)**

```bash
ffmpeg -y -loglevel error -i "$S/out/shorts_01.mp4" -frames:v 1 "$S/first.png"
.venv/bin/python - <<'EOF'
from pathlib import Path
from PIL import Image
import os
S = Path(os.environ.get("S", "/tmp/claude-1001/-home-ubuntu-workspace-video-automation/1b0c6fd2-c92d-4dab-861a-5a877b7bd257/scratchpad/e2e"))
img = Image.open(S / "first.png").convert("RGB")
px = img.resize((1, 1)).getpixel((0, 0))
assert sum(px) > 60, f"첫 프레임이 사실상 검정: {px}"  # 페이드인이면 (0,0,0) 근처
print("first frame OK:", px)
EOF
```

Expected: `first frame OK: (…)` — 색상 프레임

- [ ] **Step 4: A/B 플래그 경로 확인**

```bash
.venv/bin/python pipeline.py "$S/synthetic.mp4" --mode scene --scene-threshold 0.1 \
  --only shorts --shorts-count 1 -t 0.5 --no-shorts-punchin --no-shorts-jumpcut \
  --cache -o "$S/out_ab"
```

Expected: 정상 생성 (레거시 동작 경로 생존)

- [ ] **Step 5: 전체 테스트 최종 확인**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 전부 PASS. 이후 verify 스킬로 종합 검증.
