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


def punch_plan(intervals: list, period: float = 4.0, beats: list | None = None) -> list:
    """punch-in 교차용 서브클립 경계. 점프컷이 있으면 그 경계를 그대로 쓰고,
    통짜 1개가 길면 가상 컷으로 쪼갠다(시간 손실 없음).

    beats가 있으면 가상 컷을 고정 간격 대신 비트 시각에 놓는다 — 줌 전환이
    음악 박자에 떨어진다. 컷 최소 간격 max(1.5, period/2)로 과다 컷을 막는다.
    """
    if len(intervals) != 1:
        return list(intervals)
    s, e = intervals[0]
    if beats:
        min_gap = max(1.5, period / 2)
        cuts, last = [], s
        for b in beats:
            if s + 1.0 <= b <= e - 1.0 and b - last >= min_gap:
                cuts.append(b)
                last = b
        if cuts:
            bounds = [s, *cuts, e]
            return list(zip(bounds[:-1], bounds[1:]))
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
