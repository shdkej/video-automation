# 몽타주 콘텐츠 인지 트림 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 몽타주 숏츠의 트림을 "고정 2초 + 모션"에서 "숏츠 예산 역산 + 비전 LLM 핵심 순간(peak/keep) + 단어 경계 스냅"으로 바꾼다.

**Architecture:** 순수 함수 2개(예산 역산 `plan_montage_lengths`, peak 지원 `trim_montage_segments`)를 auto_cut.py에 두고, 기존 장면 자막 비전 콜을 클립당 3프레임으로 확장해 peak/keep을 함께 받는다. pipeline.py 몽타주 분기는 "비전 콜(트림 전) → 예산 트림 → (speech면) 단어 스냅" 순서로 재배선한다. LLM 키 없음/실패는 현행 모션 폴백 그대로.

**Tech Stack:** Python 3 (외부 의존 없는 순수 함수 위주), pytest, ffmpeg(기존 함수 재사용), React(웹 옵션 기본값만).

**Spec:** `docs/superpowers/specs/2026-07-13-montage-content-aware-trim-design.md`

## Global Constraints

- 예산 기본값: `shorts_ideal_seconds`=25.0 (전체 유지 임계), `shorts_max_seconds`=45.0 (상한), 클립 최소 창 1.5초.
- `montage_seconds` 의미: **미지정/음수=auto(예산 역산)**, 0=트림 없음, 양수=고정 길이(구버전 모션 동작 재현 — A/B용).
- 폴백 계층: LLM 키 없음/콜 실패 → 모션 트림(현행). 응답 필드 무효(peak 범위 밖, keep 미지 값) → 해당 클립만 모션 폴백.
- 데이터 계약 불변: `clip_start`/`clip_end`(편집기 트림 바), `reason="montage(전체 유지)"` 마커, captions↔segments 1:1 정렬.
- 테스트는 `.venv` 활성화 후 `pytest tests/ -q` (외부 의존 없는 순수 함수만 추가).
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: 예산 역산 순수 함수 `plan_montage_lengths`

**Files:**
- Modify: `auto_cut.py` (Domain 섹션, `trim_montage_segments` 아래)
- Test: `tests/test_pure.py`

**Interfaces:**
- Consumes: 없음 (순수 함수 신규)
- Produces: `plan_montage_lengths(segments: list, ideal_sec: float, max_sec: float, min_clip: float = 1.5) -> list | None` — None이면 전체 유지(무트림), 아니면 클립별 유지 길이(초) 리스트. `segments[i].get("keep") == "whole"`이면 그 클립은 원 길이 유지. Task 4가 소비.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pure.py` 끝(`test_montage_sfx_events_cumulative_offsets` 뒤)에 추가:

```python
# ---------- 몽타주 예산 역산 (plan_montage_lengths) ----------

from auto_cut import plan_montage_lengths  # noqa: E402


def test_plan_none_when_total_fits_budget():
    # 10초 영상 1개 — 예산(25초) 이내라 자를 이유가 없다 → 전체 유지
    segs = [{"start": 0.0, "end": 10.0}]
    assert plan_montage_lengths(segs, ideal_sec=25.0, max_sec=45.0) is None


def test_plan_divides_budget_when_over():
    segs = [{"start": i * 5.0, "end": i * 5.0 + 5.0} for i in range(10)]  # 총 50초
    lengths = plan_montage_lengths(segs, 25.0, 45.0)
    assert lengths == [2.5] * 10  # ideal 25 ÷ 10클립


def test_plan_respects_keep_whole():
    segs = [{"start": 0.0, "end": 5.0, "keep": "whole"}] + [
        {"start": 5.0 + i * 5.0, "end": 10.0 + i * 5.0} for i in range(9)
    ]  # 총 50초
    lengths = plan_montage_lengths(segs, 25.0, 45.0)
    assert lengths[0] == 5.0   # whole은 원 길이 유지
    assert lengths[1] == 2.5


def test_plan_scales_down_to_max_cap():
    # whole 남발로 상한(45초) 초과 → 비례 축소
    segs = [{"start": i * 10.0, "end": (i + 1) * 10.0, "keep": "whole"} for i in range(6)]
    lengths = plan_montage_lengths(segs, 25.0, 45.0)
    assert sum(lengths) <= 45.0 + 1e-6
    assert all(ln >= 1.5 for ln in lengths)


def test_plan_min_clip_floor():
    # 25 ÷ 20 = 1.25 < 1.5 → 바닥값 1.5로
    segs = [{"start": i * 2.0, "end": i * 2.0 + 2.0} for i in range(20)]  # 총 40초
    lengths = plan_montage_lengths(segs, 25.0, 45.0)
    assert lengths == [1.5] * 20
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_pure.py -q -k plan_`
Expected: FAIL — `ImportError: cannot import name 'plan_montage_lengths'`

- [ ] **Step 3: 구현**

`auto_cut.py`의 `trim_montage_segments` 바로 아래에 추가:

```python
def plan_montage_lengths(
    segments: list, ideal_sec: float, max_sec: float, min_clip: float = 1.5,
) -> list | None:
    """몽타주 예산 역산 — 클립별 유지 길이. None이면 전체 유지(트림 불필요).

    총량이 ideal_sec 이하면 자를 이유가 없다(None). 초과하면 ideal_sec을
    클립 수로 나눈 창을 기본으로 하되, keep=whole 클립(내용이 이어져 통으로
    필요)은 원 길이를 지킨다. 그 합이 max_sec을 넘으면 전 클립을 비례 축소해
    상한을 지킨다(min_clip 바닥, 원 길이 초과 금지).
    """
    durs = [s["end"] - s["start"] for s in segments]
    total = sum(durs)
    if total <= ideal_sec:
        return None
    per_clip = max(min_clip, ideal_sec / len(durs))
    lengths = [
        d if s.get("keep") == "whole" else min(d, per_clip)
        for d, s in zip(durs, segments)
    ]
    over = sum(lengths)
    if over > max_sec:
        scale = max_sec / over
        lengths = [min(d, max(min_clip, ln * scale)) for d, ln in zip(durs, lengths)]
    return [round(ln, 3) for ln in lengths]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_pure.py -q -k plan_`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add auto_cut.py tests/test_pure.py
git commit -m "feat(montage): 예산 역산 plan_montage_lengths — 고정 2초 트림 대체 준비

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `trim_montage_segments` 확장 — 클립별 길이 + peak 중심 배치

**Files:**
- Modify: `auto_cut.py:247-275` (`trim_montage_segments`)
- Test: `tests/test_pure.py` (기존 trim 테스트 3개 시그니처 수정 + 신규 5개)

**Interfaces:**
- Consumes: 없음
- Produces: `trim_montage_segments(segments: list, motion: list, lengths: list | float, use_peak: bool = True) -> list` — `lengths`가 float면 전 클립 공통(기존 `max_len` 동작), list면 클립별. 창 배치 우선순위 ①`seg["peak"]`(0~1, use_peak=True일 때) ②모션 최대 창 ③40% 지점. 반환 seg에 `clip_start`/`clip_end` 보존(기존 계약). Task 4가 소비.

- [ ] **Step 1: 기존 테스트 시그니처 수정 + 신규 테스트 작성**

`tests/test_pure.py`의 기존 3개 테스트에서 `max_len=2.0` → `2.0` (위치 인자)로 변경:

```python
def test_trim_picks_high_motion_window():
    seg = [{"start": 0.0, "end": 6.0, "reason": "montage(전체 유지)"}]
    # 4~6초 구간에 움직임 집중
    motion = [(t / 10, 0.9 if t >= 40 else 0.01) for t in range(60)]
    out = trim_montage_segments(seg, motion, 2.0)
    assert len(out) == 1
    assert out[0]["end"] - out[0]["start"] == pytest.approx(2.0, abs=0.01)
    assert out[0]["start"] >= 3.5  # 고모션 창 쪽으로 이동


def test_trim_keeps_short_segments():
    seg = [{"start": 0.0, "end": 1.5, "reason": "montage(전체 유지)"}]
    out = trim_montage_segments(seg, [], 2.0)
    assert (out[0]["start"], out[0]["end"]) == (0.0, 1.5)          # 구간은 그대로
    assert (out[0]["clip_start"], out[0]["clip_end"]) == (0.0, 1.5)  # 편집기 트림 바용 경계


def test_trim_fallback_center_biased_without_motion():
    seg = [{"start": 10.0, "end": 15.0, "reason": "montage(전체 유지)"}]
    out = trim_montage_segments(seg, [], 2.0)
    assert out[0]["start"] == 11.2  # 10 + (5-2)*0.4
    assert out[0]["end"] == 13.2
```

이어서 신규 테스트 추가:

```python
def test_trim_uses_llm_peak_center():
    seg = [{"start": 0.0, "end": 6.0, "peak": 0.75}]
    out = trim_montage_segments(seg, [], 2.0)
    assert out[0]["start"] == pytest.approx(3.5)  # 중심 4.5 - 1.0
    assert out[0]["end"] == pytest.approx(5.5)


def test_trim_peak_clamped_to_clip_bounds():
    seg = [{"start": 0.0, "end": 6.0, "peak": 1.0}]
    out = trim_montage_segments(seg, [], 2.0)
    assert (out[0]["start"], out[0]["end"]) == (4.0, 6.0)


def test_trim_ignores_peak_when_disabled():
    # --montage-seconds 명시(A/B) → 구버전 모션 동작 재현
    seg = [{"start": 10.0, "end": 15.0, "peak": 0.9}]
    out = trim_montage_segments(seg, [], 2.0, use_peak=False)
    assert out[0]["start"] == 11.2  # 40% 지점 폴백


def test_trim_per_clip_lengths():
    segs = [{"start": 0.0, "end": 6.0, "keep": "whole"}, {"start": 6.0, "end": 12.0}]
    out = trim_montage_segments(segs, [], [6.0, 2.0])
    assert (out[0]["start"], out[0]["end"]) == (0.0, 6.0)  # whole — 통 유지
    assert out[1]["end"] - out[1]["start"] == pytest.approx(2.0)


def test_trim_invalid_peak_falls_back_to_motion():
    seg = [{"start": 0.0, "end": 6.0, "peak": 1.7}]  # 범위 밖 — 무시
    motion = [(t / 10, 0.9 if t >= 40 else 0.01) for t in range(60)]
    out = trim_montage_segments(seg, motion, 2.0)
    assert out[0]["start"] >= 3.5
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_pure.py -q -k trim_`
Expected: 신규 5개 중 4개 FAIL (`TypeError` 또는 peak 미반영 assert 실패), 기존 3개 PASS. `test_trim_invalid_peak_falls_back_to_motion`은 구버전도 모션 동작이라 구현 전에도 PASS — 회귀 방어용.

- [ ] **Step 3: 구현**

`auto_cut.py`의 `trim_montage_segments` 전체 교체:

```python
def trim_montage_segments(
    segments: list, motion: list, lengths: list | float, use_peak: bool = True,
) -> list:
    """몽타주 구간을 클립별 목표 길이 창으로 다듬는다.

    창 배치 우선순위: ①LLM peak(핵심 순간의 0~1 상대 위치) 중심 ②모션 최대 창
    ③시작 40% 지점(촬영 시작·끝 흔들림을 피하는 보수 폴백).
    lengths가 float면 전 클립 공통, use_peak=False면 peak를 무시하고
    구버전(모션) 동작을 재현한다 — --montage-seconds 명시 A/B용.
    """
    if isinstance(lengths, (int, float)):
        lengths = [float(lengths)] * len(segments)
    trimmed = []
    for seg, max_len in zip(segments, lengths):
        s, e = seg["start"], seg["end"]
        seg = {**seg, "clip_start": s, "clip_end": e}  # 원본 클립 경계 — 편집기 트림 바용
        if e - s <= max_len:
            trimmed.append(seg)
            continue
        peak = seg.get("peak") if use_peak else None
        if isinstance(peak, (int, float)) and 0.0 <= float(peak) <= 1.0:
            center = s + (e - s) * float(peak)
            start = min(max(s, center - max_len / 2), e - max_len)
        else:
            # 구간 시작 직후 0.3초는 제외 — 씬 컷 자체의 점수 스파이크가
            # '움직임'으로 잡혀 모든 창이 시작점으로 쏠린다
            pts = [(t, sc) for t, sc in motion if s + 0.3 <= t <= e]
            if pts:
                best_t, best_sum = s, -1.0
                t0 = s
                while t0 <= e - max_len + 1e-9:
                    w = sum(sc for t, sc in pts if t0 <= t <= t0 + max_len)
                    if w > best_sum:
                        best_sum, best_t = w, t0
                    t0 += 0.1
                start = best_t
            else:
                start = s + (e - s - max_len) * 0.4
        trimmed.append({**seg, "start": round(start, 3), "end": round(start + max_len, 3)})
    return trimmed
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_pure.py -q -k trim_`
Expected: 8 passed (기존 3 + 신규 5)

- [ ] **Step 5: Commit**

```bash
git add auto_cut.py tests/test_pure.py
git commit -m "feat(montage): trim_montage_segments에 클립별 길이·LLM peak 중심 배치

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 비전 콜 확장 — 클립당 3프레임 + peak/keep 응답

**Files:**
- Modify: `auto_cut.py:612-711` (`build_segment_mosaic`, `build_scene_caption_prompt`, `generate_scene_captions`, `merge_scene_captions`)
- Test: `tests/test_pure.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `montage_frame_times(segments: list, frames_per_clip: int) -> list` — 1이면 중앙점(기존), 3이면 클립당 25%/50%/75% 시점.
  - `build_segment_mosaic(video_path: Path, times: list, output_path: Path, cols: int | None = None) -> None` — cols 미지정 시 기존 `min(4, len(times))`.
  - `build_scene_caption_prompt(n: int, frames_per_clip: int = 1) -> str`
  - `generate_scene_captions(video_path: Path, segments: list, model: str, frames_per_clip: int = 1) -> tuple` — 기존과 동일하게 `(captions, mood)` 반환, `peak`/`keep`/`hook`/`score`는 segments에 병합.
  - `merge_scene_captions(segments, data)` — 기존 + `peak`(float 0~1만)·`keep`("whole"/"trim"만) 병합. Task 4가 소비.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pure.py`에 추가 (merge 테스트 옆):

```python
def test_merge_scene_captions_peak_and_keep():
    segs = [{"start": 0.0, "end": 6.0}, {"start": 6.0, "end": 12.0}, {"start": 12.0, "end": 18.0}]
    data = {"scenes": [
        {"idx": 1, "caption": "a", "peak": 0.25, "keep": "trim"},
        {"idx": 2, "caption": "b", "peak": 1.7, "keep": "maybe"},  # 둘 다 무효 — 병합 안 함
        {"idx": 3, "caption": "c", "keep": "whole"},
    ]}
    merge_scene_captions(segs, data)
    assert segs[0]["peak"] == 0.25 and segs[0]["keep"] == "trim"
    assert "peak" not in segs[1] and "keep" not in segs[1]
    assert segs[2]["keep"] == "whole" and "peak" not in segs[2]


def test_montage_frame_times_three_per_clip():
    from auto_cut import montage_frame_times
    segs = [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 8.0}]
    assert montage_frame_times(segs, 3) == [1.0, 2.0, 3.0, 5.0, 6.0, 7.0]


def test_montage_frame_times_single_is_midpoint():
    from auto_cut import montage_frame_times
    segs = [{"start": 0.0, "end": 6.0}]
    assert montage_frame_times(segs, 1) == [3.0]


def test_scene_caption_prompt_mentions_peak_for_multiframe():
    from auto_cut import build_scene_caption_prompt
    p1 = build_scene_caption_prompt(4)
    p3 = build_scene_caption_prompt(4, frames_per_clip=3)
    assert "peak" not in p1
    assert "peak" in p3 and "keep" in p3 and "초반" in p3
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_pure.py -q -k "peak_and_keep or frame_times or prompt_mentions"`
Expected: 4 FAIL (import/assert)

- [ ] **Step 3: 구현**

`auto_cut.py` 변경 4곳.

(a) `build_segment_mosaic` 시그니처에 cols 추가 — `cols = min(4, len(times))` 줄을 교체:

```python
def build_segment_mosaic(video_path: Path, times: list, output_path: Path, cols: int | None = None) -> None:
```

```python
        cols = cols or min(4, len(times))
```

(b) `build_segment_mosaic` 위에 신규 함수:

```python
def montage_frame_times(segments: list, frames_per_clip: int) -> list:
    """장면 자막 그리드에 넣을 프레임 시각. 1이면 중앙점, N이면 클립당 균등 N점.

    frames_per_clip=3 → 각 클립의 25%/50%/75% 지점 — 비전 LLM이 클립의
    흐름(초·중·후반)을 보고 핵심 순간(peak)과 통유지 여부(keep)를 정한다.
    """
    if frames_per_clip <= 1:
        return [(s["start"] + s["end"]) / 2 for s in segments]
    fracs = [(i + 1) / (frames_per_clip + 1) for i in range(frames_per_clip)]
    return [s["start"] + (s["end"] - s["start"]) * f for s in segments for f in fracs]
```

(c) `build_scene_caption_prompt` 전체 교체:

```python
def build_scene_caption_prompt(n: int, frames_per_clip: int = 1) -> str:
    if frames_per_clip <= 1:
        grid_desc = (f"이 이미지는 한 영상에서 선정한 {n}개 장면을 왼쪽→오른쪽, "
                     "위→아래 순서로 배열한 그리드다. i번째 칸이 i번째 장면이다.")
        trim_fields = ""
        trim_example = ""
    else:
        grid_desc = (f"이 이미지는 한 영상의 {n}개 클립을 행으로 배열한 그리드다. "
                     "i번째 행이 i번째 클립이고, 각 행의 왼쪽→오른쪽은 그 클립의 "
                     "초반(25%)·중반(50%)·후반(75%) 시점이다.")
        trim_fields = """
- peak: 이 클립에서 가장 핵심적인 순간의 상대 위치 0.0~1.0. 초반 프레임이 핵심이면 0.25, 후반이면 0.75처럼.
- keep: 클립 내용이 처음부터 끝까지 이어져 통으로 살려야 하면 whole, 한 순간이면 충분하면 trim."""
        trim_example = ' "peak": 0.5, "keep": "trim",'
    return f"""{grid_desc}

무발화 영상에 화면 자막을 입힌다. 요즘 릴스/숏츠 트렌드처럼 장면을 설명하지 말고, 보는 사람의 감정을 건드리는 짧은 한 줄을 쓴다.

각 장면마다:
- caption: 화면 하단에 얹을 자막. 한국어 구어체 반말, 8~18자 한 줄. 상황·감정·여운 중심 (예: 이 골목에서 한참 서 있었다 / 오늘의 하이라이트는 이거).
- hook: 그 장면으로 숏츠를 만들 때 상단 배너에 띄울 후킹 문구. 15자 이내, 의문·숫자·반전 중 하나.
- score: 숏폼 임팩트 0~100.{trim_fields}

그리고 영상 전체에 어울리는 BGM 무드를 하나 고른다:
- mood: calm(잔잔한 풍경·감성) / upbeat(활기찬 이동·시티) / cinematic(웅장한 하이라이트) / warm(따뜻한 일상·음식) / tension(긴박·반전) 중 하나.

규칙: 이모지·특수문자·따옴표 금지. 이미지에 보이는 것만 근거로 하고 없는 사실을 지어내지 않는다.

JSON만 출력: {{"mood": "calm", "scenes": [{{"idx": 1, "caption": "...", "hook": "...", "score": 50,{trim_example}}}, ...]}}"""
```

(d) `generate_scene_captions` — times 계산과 mosaic 호출부 교체:

```python
def generate_scene_captions(
    video_path: Path, segments: list, model: str, frames_per_clip: int = 1,
) -> tuple:
    """무발화(scene/vision) 구간에 릴스 톤 화면 자막 생성.

    구간 대표 프레임 그리드 1장 + 비전 LLM 1콜. (captions, mood)를 반환하고,
    hook/score(+frames_per_clip>1이면 peak/keep)는 segments에 직접 병합한다
    (selection.json 캐시에 함께 보존). mood는 BGM 자동 선곡용 — 유효하지 않으면 None.
    """
    times = montage_frame_times(segments, frames_per_clip)
    mosaic = video_path.with_suffix(".captions.jpg")
    build_segment_mosaic(video_path, times, mosaic,
                         cols=frames_per_clip if frames_per_clip > 1 else None)
    provider = detect_provider(model)
    prompt = build_scene_caption_prompt(len(segments), frames_per_clip)
```

(이하 try/finally·merge 호출은 기존 그대로)

(e) `merge_scene_captions`의 for 루프 안, score 병합 아래에 추가:

```python
        peak = sc.get("peak")
        if isinstance(peak, (int, float)) and 0.0 <= float(peak) <= 1.0:
            segments[i]["peak"] = float(peak)
        keep = str(sc.get("keep", "")).strip()
        if keep in ("whole", "trim"):
            segments[i]["keep"] = keep
```

docstring도 갱신: "idx가 범위를 벗어나거나 형식이 어긋난 항목은 조용히 버린다" 문장 뒤에 "peak(0~1 밖)·keep(whole/trim 외)도 무효면 병합하지 않는다 — 해당 클립은 모션 폴백." 추가.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_pure.py -q`
Expected: all passed (기존 merge 테스트 2개 포함 회귀 없음)

- [ ] **Step 5: Commit**

```bash
git add auto_cut.py tests/test_pure.py
git commit -m "feat(montage): 장면 자막 비전 콜 확장 — 클립당 3프레임 + peak/keep 응답

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: pipeline 몽타주 분기 재배선 — 비전→예산 트림→단어 스냅 + 판단 로그

**Files:**
- Modify: `pipeline.py` (import, `_scene_captions_safe`, `analyze()` 몽타주 분기 429-456행, 신규 헬퍼 2개)
- Test: `tests/test_pure.py`

**Interfaces:**
- Consumes: Task 1 `plan_montage_lengths`, Task 2 `trim_montage_segments(segments, motion, lengths, use_peak)`, Task 3 `_scene_captions_safe(..., frames_per_clip=3)` 경유 peak/keep 병합, 기존 `snap_to_word_bounds(clip, transcript)`.
- Produces: `_trim_montage(args, segments, transcript) -> list`, `_snap_trim_to_words(seg, transcript) -> dict` (pipeline 내부). analyze()의 반환 계약 `(segments, captions, transcript)` 불변.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pure.py`에 추가:

```python
def test_snap_trim_to_words_expands_to_word_bounds():
    from pipeline import _snap_trim_to_words
    transcript = {"segments": [{"words": [
        {"word": "안녕", "start": 1.8, "end": 2.4},
        {"word": "하세요", "start": 2.4, "end": 3.1},
    ]}]}
    seg = {"start": 2.0, "end": 3.0, "clip_start": 0.0, "clip_end": 5.0}
    out = _snap_trim_to_words(seg, transcript)
    assert out["start"] == 1.8 and out["end"] == 3.1  # 단어 경계로 확장, 말 안 끊김


def test_snap_trim_to_words_clamps_to_clip_bounds():
    from pipeline import _snap_trim_to_words
    transcript = {"segments": [{"words": [
        {"word": "넘어감", "start": 4.7, "end": 5.4},  # 클립 끝(5.0) 밖으로 스냅 시도
    ]}]}
    seg = {"start": 3.0, "end": 5.0, "clip_start": 0.0, "clip_end": 5.0}
    out = _snap_trim_to_words(seg, transcript)
    assert out["end"] <= 5.0  # 클립 경계 밖으로 못 나감


def test_snap_trim_to_words_noop_for_untrimmed():
    from pipeline import _snap_trim_to_words
    seg = {"start": 0.0, "end": 5.0, "clip_start": 0.0, "clip_end": 5.0}
    assert _snap_trim_to_words(seg, {"segments": []}) == seg
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_pure.py -q -k snap_trim`
Expected: FAIL — `ImportError: cannot import name '_snap_trim_to_words'`

- [ ] **Step 3: 구현**

(a) `pipeline.py` import에 `plan_montage_lengths` 추가 (auto_cut import 블록, 알파벳 순서 위치):

```python
    plan_montage_lengths,
```

(b) `_scene_captions_safe` 시그니처·전달 확장:

```python
def _scene_captions_safe(args, segments: list, outdir: Path | None = None,
                         frames_per_clip: int = 1) -> list:
```

내부 `generate_scene_captions` 호출을:

```python
        captions, mood = generate_scene_captions(args.input, segments, args.llm_model,
                                                 frames_per_clip=frames_per_clip)
```

(c) `snap_to_word_bounds` 아래에 헬퍼 2개 추가:

```python
def _snap_trim_to_words(seg: dict, transcript: dict | None) -> dict:
    """트림된 몽타주 창을 단어 경계로 스냅 — 말이 중간에 잘린 채 시작/끝나는 것 방지.

    트림 안 된 클립(창=클립 전체)은 그대로. 스냅이 클립 경계(clip_start/clip_end)
    밖으로 나가면 경계로 클램프하고, 그 결과가 뒤집히면 스냅을 포기한다.
    """
    cs = seg.get("clip_start", seg["start"])
    ce = seg.get("clip_end", seg["end"])
    if seg["end"] - seg["start"] >= ce - cs:
        return seg
    snapped = snap_to_word_bounds(seg, transcript)
    start = max(cs, snapped["start"])
    end = min(ce, snapped["end"])
    if end - start < 0.8:
        return seg
    return {**seg, "start": round(start, 3), "end": round(end, 3)}


def _trim_montage(args, segments: list, transcript: dict | None) -> list:
    """몽타주 클립별 트림 — 기본은 숏츠 예산 역산(auto), --montage-seconds 명시 시 고정.

    auto: 총량 ≤ shorts_ideal_seconds면 전체 유지. 초과 시 예산 분배 창을
    LLM peak 중심(무발화)·모션 폴백으로 배치하고, 발화가 있으면 단어 경계 스냅.
    0이면 무트림, 양수면 구버전(모션·고정 길이) 재현 — A/B 비교용.
    """
    msec = getattr(args, "montage_seconds", None)
    auto = msec is None or float(msec) < 0
    if not auto and float(msec) == 0:
        return segments
    if auto:
        lengths = plan_montage_lengths(
            segments, args.shorts_ideal_seconds, args.shorts_max_seconds)
        if lengths is None:
            print(f"  전체 유지: 총 {total_duration(segments):.1f}초 ≤ "
                  f"예산 {args.shorts_ideal_seconds:.0f}초 — 트림 없음")
            return segments
    else:
        lengths = float(msec)
    trimmed = trim_montage_segments(
        segments, frame_motion_scores(args.input), lengths, use_peak=auto)
    if transcript:
        trimmed = [_snap_trim_to_words(s, transcript) for s in trimmed]
    n_peak = sum(1 for s in trimmed if isinstance(s.get("peak"), (int, float))) if auto else 0
    n_whole = sum(1 for s in trimmed if s.get("keep") == "whole") if auto else 0
    print(f"  클립별 트림: {len(trimmed)}개 → 총 {total_duration(trimmed):.1f}초 "
          f"(LLM 핵심 {n_peak} · 통유지 {n_whole} · 모션 {len(trimmed) - n_peak})")
    return trimmed
```

(d) `analyze()`의 몽타주 분기(현재 429-456행, `if duration <= args.target_minutes * 60 + 1.0:` 블록) 전체 교체:

```python
    if duration <= args.target_minutes * 60 + 1.0:
        print(f"[분석] 총 {duration:.0f}초 ≤ 목표 {args.target_minutes * 60:.0f}초 "
              "— 컷 선택 생략, 전체 유지(몽타주)")
        scenes = detect_scene_changes(args.input, args.scene_threshold)
        segments = full_coverage_segments(scenes, duration)
        _load_or_detect_beats(args, outdir)  # 펀치인·인트로·비트싱크가 캐시를 읽는다

        transcript = None
        if args.mode == "speech":
            # 자막용 트랜스크립트는 그대로 뽑되, 품질 미달이어도 몽타주는 진행
            transcript_path = args.input.with_suffix(".transcript.json")
            if args.cache and transcript_path.exists():
                transcript = json.loads(transcript_path.read_text())
            else:
                transcript = transcribe_video(args.input, args.whisper_model, args.language)
                transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
            try:
                validate_transcript_quality(transcript)
            except ValueError:
                transcript = None  # 무발화 몽타주로 진행 (비전 캡션 경로)

        captions = None
        if transcript is None:
            # 비전 콜은 트림 전에 — 클립 전체(초·중·후반 3프레임)를 보고
            # 핵심 순간(peak)·통유지(keep)를 정한다. 트림은 구간 수·순서를
            # 보존하므로 captions 1:1 정렬이 유지된다.
            captions = _scene_captions_safe(args, segments, outdir, frames_per_clip=3)
        segments = _trim_montage(args, segments, transcript)
        if transcript is not None:
            captions = [caption_for_segment(s, transcript["segments"]) for s in segments]
        return segments, captions, transcript
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/ -q`
Expected: all passed

- [ ] **Step 5: 몽타주 경로 스모크 (LLM 키 없이 — 폴백 경로 검증)**

```bash
cd /home/ubuntu/workspace/video-automation && . .venv/bin/activate
S=/tmp/claude-1001/-home-ubuntu-workspace-video-automation/d22a37c6-47fa-495c-b346-54ca45402205/scratchpad
mkdir -p "$S/smoke"
for i in 1 2 3; do
  ffmpeg -y -loglevel error -f lavfi -i "testsrc2=size=640x360:rate=30:duration=5" \
    -f lavfi -i "sine=frequency=$((300*i)):duration=5" -shortest \
    -c:v libx264 -c:a aac "$S/smoke/clip$i.mp4"
done
python pipeline.py \
  "$S"/smoke/clip1.mp4 "$S"/smoke/clip2.mp4 "$S"/smoke/clip3.mp4 \
  --only shorts --mode scene --no-scene-captions -o "$S/smoke/out"
```

(`--mode scene`은 Whisper 생략용, `--no-scene-captions`는 `.env`에 LLM 키가 있어도 비전 콜이 안 나가게 — peak/keep 없는 모션 폴백 경로가 곧 검증 대상)

Expected: 총 15초 ≤ 예산 25초 → 로그에 "전체 유지: 총 15.0초 ≤ 예산 25초 — 트림 없음", `shorts_01.mp4` 길이 ≈ 15초 (`ffprobe -v error -show_entries format=duration -of csv=p=0 "$S/smoke/out/shorts_01.mp4"`). 구버전이라면 2초×3=6초였을 케이스.

- [ ] **Step 6: Commit**

```bash
git add pipeline.py tests/test_pure.py
git commit -m "feat(montage): 예산 역산 트림 + 비전 peak/keep + 단어 스냅 — 몽타주 분기 재배선

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: CLI 플래그 + 웹 기본값 auto

**Files:**
- Modify: `pipeline.py` (argparse, 분석 그룹)
- Modify: `web/app.py:171,598,609`
- Modify: `web/ui/src/screens/UploadScreen.tsx:109,190,505-506`

**Interfaces:**
- Consumes: Task 4의 `_trim_montage` (msec None/음수=auto 해석)
- Produces: CLI `--montage-seconds`(default None=auto), 웹 Form default -1.0(=auto), UI 빈칸=자동.

- [ ] **Step 1: pipeline.py argparse 추가**

`--no-beat-sync` 인자 정의 바로 아래에:

```python
    parser.add_argument(
        "--montage-seconds", type=float, default=None,
        help="몽타주 클립당 유지 길이(초). 미지정=자동(숏츠 예산 역산), "
             "0=트림 없음, 양수=고정 길이(구버전 모션 동작 재현, A/B용)",
    )
```

- [ ] **Step 2: web/app.py 기본값 변경 (3곳)**

171행:

```python
        montage_seconds=float(opts.get("montage_seconds", -1.0)),
```

598행:

```python
    montage_seconds: float = Form(-1.0),
```

609행:

```python
    montage_seconds = min(10.0, montage_seconds) if montage_seconds >= 0 else -1.0
```

- [ ] **Step 3: UploadScreen.tsx — 빈칸=자동**

109행 `montage_sec: '2'` → `montage_sec: ''`.

190행을 조건부로:

```tsx
    if (adv.montage_sec !== '') fd.append('montage_seconds', adv.montage_sec);
```

505-506행 라벨·placeholder:

```tsx
                    <label className="space-y-2">
                      <Label>몽타주 클립 (초·빈칸=자동)</Label>
                      <Input type="number" min="0" max="10" step="0.5" placeholder="자동" value={adv.montage_sec} onChange={(e) => setAdv((p) => ({ ...p, montage_sec: e.target.value }))} className="bg-background" />
                    </label>
```

- [ ] **Step 4: 검증**

```bash
python pipeline.py --help | grep -A2 montage-seconds
cd web/ui && npx tsc --noEmit -p tsconfig.app.json && cd ../..
pytest tests/ -q
```

Expected: help에 새 플래그 표기, tsc 에러 없음, 테스트 전부 통과. (UI 번들은 CI가 빌드 — 로컬 빌드 불필요)

- [ ] **Step 5: Commit**

```bash
git add pipeline.py web/app.py web/ui/src/screens/UploadScreen.tsx
git commit -m "feat(montage): montage_seconds 기본값 auto — CLI 플래그 + 웹 빈칸=자동

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 고정 모드 A/B 스모크 + 문서 갱신

**Files:**
- Modify: `README.md` (몽타주/montage_seconds 언급부 — grep로 위치 확인)
- Test: 수동 스모크 (Task 4 Step 5의 산출물 재사용)

**Interfaces:**
- Consumes: Task 4 스모크 입력(`$S/smoke/`), Task 5 CLI 플래그
- Produces: 없음 (검증·문서)

- [ ] **Step 1: 고정 모드(구버전 재현) A/B 스모크**

```bash
cd /home/ubuntu/workspace/video-automation && . .venv/bin/activate
S=/tmp/claude-1001/-home-ubuntu-workspace-video-automation/d22a37c6-47fa-495c-b346-54ca45402205/scratchpad
python pipeline.py \
  "$S"/smoke/clip1.mp4 "$S"/smoke/clip2.mp4 "$S"/smoke/clip3.mp4 \
  --only shorts --mode scene --no-scene-captions \
  --montage-seconds 2 -o "$S/smoke/out_fixed"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$S/smoke/out_fixed/shorts_01.mp4"
```

Expected: 로그에 "클립별 트림" + shorts_01.mp4 ≈ 6초 (2초×3클립, 구버전 동작 재현 확인). auto 결과(≈15초)와 대비가 곧 A/B 근거.

- [ ] **Step 2: README 갱신**

`grep -n "montage\|몽타주" README.md`로 위치를 찾아, 몽타주 트림 설명이 있으면 "기본은 숏츠 예산 역산(총량 ≤ 25초면 전체 유지, 초과 시 LLM이 클립별 핵심 순간을 골라 트림). `--montage-seconds N`으로 고정 길이(구버전), 0으로 무트림."으로 갱신. 언급이 없으면 옵션 표/사용법 섹션에 같은 내용 1-2줄 추가.

- [ ] **Step 3: 전체 테스트 최종 확인**

Run: `pytest tests/ -q`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: 몽타주 트림 auto(예산 역산) 동작 설명

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
