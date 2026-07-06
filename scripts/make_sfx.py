#!/usr/bin/env python3
"""기본 효과음 세트 합성 — web/sfx/*.mp3 + meta.json 생성.

외부 음원 없이 numpy로 직접 합성한다(라이선스 자유). 곡선은 단순하지만
숏폼 단골 용도(전환·강조·등장)에 충분하고, 폴더에 mp3를 추가하면 확장된다.

실행(프로젝트 루트): python scripts/make_sfx.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

SR = 44100
OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "sfx"


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def _decay(dur: float, k: float) -> np.ndarray:
    return np.exp(-k * _t(dur))


def sine(freq: float, dur: float) -> np.ndarray:
    return np.sin(2 * np.pi * freq * _t(dur))


def sweep(f0: float, f1: float, dur: float) -> np.ndarray:
    t = _t(dur)
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t**2 / (2 * dur))
    return np.sin(phase)


def noise(dur: float) -> np.ndarray:
    return np.random.default_rng(7).uniform(-1, 1, int(SR * dur))


def mix_at(base: np.ndarray, part: np.ndarray, at: float) -> np.ndarray:
    i = int(SR * at)
    need = i + len(part)
    if need > len(base):
        base = np.pad(base, (0, need - len(base)))
    base[i:need] += part
    return base


def bell(freq: float, dur: float, k: float = 6.0) -> np.ndarray:
    # 기음 + 2배음 살짝 — 금속성 띠링
    return (sine(freq, dur) + 0.4 * sine(freq * 2, dur)) * _decay(dur, k)


def make_ding() -> np.ndarray:  # 띠링
    s = bell(1318.5, 0.9, 5)
    return mix_at(s * 0.7, bell(1760.0, 0.9, 5) * 0.7, 0.09)


def make_dingdong() -> np.ndarray:  # 딩동
    s = bell(1318.5, 0.7, 6)
    return mix_at(s * 0.7, bell(1046.5, 0.9, 5) * 0.7, 0.25)


def make_pop() -> np.ndarray:  # 팝
    body = sine(420, 0.06) * _decay(0.06, 60)
    click = noise(0.012) * _decay(0.012, 300)
    return mix_at(click * 0.5, body, 0.0)


def make_click() -> np.ndarray:  # 클릭
    return noise(0.02) * _decay(0.02, 250) * 0.8


def make_whoosh() -> np.ndarray:  # 휙
    n = noise(0.45)
    t = _t(0.45)
    env = np.sin(np.pi * t / 0.45) ** 2
    carrier = 0.5 + 0.5 * sweep(300, 2600, 0.45)  # 대역 이동 느낌의 진폭 변조
    return n * env * carrier * 0.9


def make_riser() -> np.ndarray:  # 라이저 (긴장 고조)
    dur = 1.1
    t = _t(dur)
    body = sweep(180, 1100, dur) * 0.5 + noise(dur) * 0.3
    return body * (t / dur) ** 2


def make_boom() -> np.ndarray:  # 붐
    dur = 0.9
    body = sweep(85, 38, dur) * _decay(dur, 4.5)
    thump = noise(0.03) * _decay(0.03, 200) * 0.4
    return mix_at(body, thump, 0.0)


def make_dundun() -> np.ndarray:  # 두둥
    first = sweep(90, 45, 0.5) * _decay(0.5, 6)
    second = sweep(75, 35, 1.0) * _decay(1.0, 3.5)
    return mix_at(mix_at(np.zeros(1), first * 0.9, 0.0), second, 0.32)


def make_tada() -> np.ndarray:  # 타다 (완성·등장)
    s = np.zeros(1)
    for i, f in enumerate((523.25, 659.25, 783.99, 1046.5)):  # C5 E5 G5 C6
        s = mix_at(s, bell(f, 1.0, 3.5) * 0.5, i * 0.07)
    return s


SFX = {
    "ding.mp3": ("띠링", make_ding),
    "dingdong.mp3": ("딩동", make_dingdong),
    "pop.mp3": ("팝", make_pop),
    "click.mp3": ("클릭", make_click),
    "whoosh.mp3": ("휙", make_whoosh),
    "riser.mp3": ("라이저", make_riser),
    "boom.mp3": ("붐", make_boom),
    "dundun.mp3": ("두둥", make_dundun),
    "tada.mp3": ("타다", make_tada),
}


def write_mp3(samples: np.ndarray, dest: Path) -> None:
    peak = np.max(np.abs(samples)) or 1.0
    pcm = (samples / peak * 0.85 * 32767).astype(np.int16)
    with tempfile.NamedTemporaryFile(suffix=".raw") as f:
        f.write(pcm.tobytes())
        f.flush()
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", f.name,
             "-codec:a", "libmp3lame", "-b:a", "128k", str(dest)],
            check=True,
        )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, (label, fn) in SFX.items():
        write_mp3(fn(), OUT_DIR / name)
        meta[name] = label
        print(f"[sfx] {name} ({label}) {(OUT_DIR / name).stat().st_size / 1e3:.0f}KB")
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
