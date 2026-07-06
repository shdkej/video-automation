"""미디어 라이브러리(BGM·SFX) 파일 해석 — FastAPI 무의존 순수 로직.

라이브러리 상대 경로("upbeat/Carefree.mp3")를 실제 파일로 해석하되,
루트 밖 탈출(../)과 비허용 확장자를 차단한다. app.py와 tests가 공유.
"""

from __future__ import annotations

from pathlib import Path

AUDIO_EXTS = (".mp3", ".m4a", ".wav")


def resolve_library_file(
    root: Path, rel: str, exts: tuple[str, ...] = AUDIO_EXTS,
) -> Path | None:
    """root 안의 rel 경로가 실존하는 허용 오디오 파일이면 절대 경로, 아니면 None."""
    if not rel or not rel.strip():
        return None
    if Path(rel).suffix.lower() not in exts:
        return None
    root = root.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        return None
    return target if target.is_file() else None
