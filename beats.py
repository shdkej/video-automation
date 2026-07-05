"""오디오 비트 감지 — 컷 경계·펀치인·몽타주를 리듬에 스냅하기 위한 단일 출처.

librosa 같은 무거운 의존 없이 ffmpeg PCM 디코드 + numpy 에너지 온셋 →
자기상관 템포 추정 → 비트 그리드 피팅으로 간다. 프레임 정밀 비트 트래킹이
아니라 컷 스냅용이므로 ±수십 ms 오차는 허용 범위다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SR = 22050
HOP_SEC = 0.02  # 온셋 해상도(50fps)
MIN_BPM, MAX_BPM = 60.0, 180.0


def onset_envelope(path: Path) -> np.ndarray:
    """오디오 → 정규화된 온셋 강도(에너지 상승분) 배열. 오디오가 없으면 빈 배열."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    x = np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0
    hop = int(SR * HOP_SEC)
    n = len(x) // hop
    if n < 100:  # 2초 미만은 템포 추정 불가
        return np.zeros(0, dtype=np.float32)
    energy = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    onset = np.diff(energy, prepend=energy[:1])
    onset[onset < 0] = 0.0
    peak = float(onset.max())
    return onset / peak if peak > 0 else np.zeros(0, dtype=np.float32)


def beats_from_envelope(env: np.ndarray, hop_sec: float = HOP_SEC) -> list:
    """온셋 배열 → 비트 시각 목록. 자기상관으로 주기, 그리드 오프셋으로 위상.

    무음/무리듬(온셋 대부분 0)이면 빈 목록 — 스냅은 조용히 no-op이 된다.
    """
    if env.size < 100 or float(env.sum()) <= 0:
        return []
    # 리듬성 게이트: 온셋이 희박하거나 균일하면(말소리·백색소음) 비트로 치지 않는다
    active_ratio = float((env > 0.1).mean())
    if active_ratio < 0.02:
        return []

    lag_lo = max(2, int((60.0 / MAX_BPM) / hop_sec))
    lag_hi = min(int((60.0 / MIN_BPM) / hop_sec), env.size // 2)
    if lag_hi <= lag_lo:
        return []
    acs = {lag: float((env[:-lag] * env[lag:]).mean())
           for lag in range(lag_lo, lag_hi + 1)}
    best_ac = max(acs.values())
    if best_ac <= 0:
        return []
    # 자기상관은 배음(2배 주기)이 원 템포만큼 세게 잡히는 경향이 있다 —
    # 충분히 강한 것 중 가장 짧은 주기를 원 템포로 본다.
    best_lag = min(lag for lag, ac in acs.items() if ac >= 0.85 * best_ac)
    # 자기상관 피크가 배경 대비 유의해야 리듬으로 인정 (평탄하면 랜덤 잡음)
    mean_ac = float(env.mean()) ** 2
    if mean_ac > 0 and best_ac / mean_ac < 1.5:
        return []

    period = best_lag * hop_sec
    phases = [float(env[p::best_lag].sum()) for p in range(best_lag)]
    phase_idx = int(np.argmax(phases))

    # 그리드 정합도 게이트: 비트 위치(±1hop)에 온셋 에너지가 몰려 있어야 음악 리듬.
    # 말소리는 온셋이 연속적으로 퍼져 있어 정합도가 낮다 — 오인식 방지.
    idx = np.arange(phase_idx, env.size, best_lag)
    near = np.zeros_like(env, dtype=bool)
    for off in (-1, 0, 1):
        j = idx + off
        near[j[(j >= 0) & (j < env.size)]] = True
    coverage = float(env[near].sum()) / float(env.sum())
    if coverage < 0.3:
        return []

    duration = env.size * hop_sec
    beats, t = [], phase_idx * hop_sec
    while t < duration:
        beats.append(round(t, 3))
        t += period
    return beats


def detect_beats(path: Path) -> list:
    """영상/오디오 파일에서 비트 시각 목록 추출. 실패·무리듬이면 빈 목록."""
    try:
        return beats_from_envelope(onset_envelope(path))
    except Exception:  # noqa: BLE001 — 비트는 보조 신호, 실패해도 편집은 계속
        return []


def snap_to_beat(t: float, beats: list, max_shift: float = 0.35) -> float:
    """t를 가장 가까운 비트로 스냅. max_shift보다 멀면 그대로."""
    if not beats:
        return t
    b = min(beats, key=lambda x: abs(x - t))
    return b if abs(b - t) <= max_shift else t


def snap_segments_to_beats(segments: list, beats: list, max_shift: float = 0.35) -> list:
    """구간 경계를 비트에 스냅. 스냅으로 1초 미만이 되면 원본 유지."""
    if not beats:
        return segments
    out = []
    for s in segments:
        ns = snap_to_beat(s["start"], beats, max_shift)
        ne = snap_to_beat(s["end"], beats, max_shift)
        if ne - ns >= 1.0:
            out.append({**s, "start": round(ns, 3), "end": round(ne, 3)})
        else:
            out.append(s)
    return out
