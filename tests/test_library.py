"""라이브러리 경로 해석·자막 줄바꿈 단위 테스트 — ffmpeg/LLM 없이 도메인 로직만."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageFont  # noqa: E402

from subtitle import wrap_text  # noqa: E402
from web.media_library import resolve_library_file  # noqa: E402

FONT = ImageFont.truetype(
    str(Path(__file__).resolve().parent.parent / "assets/fonts/Pretendard-Bold.otf"), 40
)


def _lib(tmp_path: Path) -> Path:
    d = tmp_path / "music" / "upbeat"
    d.mkdir(parents=True)
    (d / "Carefree.mp3").write_bytes(b"x")
    (tmp_path / "secret.mp3").write_bytes(b"x")
    return tmp_path / "music"


def test_resolve_ok(tmp_path):
    root = _lib(tmp_path)
    p = resolve_library_file(root, "upbeat/Carefree.mp3")
    assert p is not None and p.name == "Carefree.mp3"


def test_resolve_blocks_traversal(tmp_path):
    root = _lib(tmp_path)
    assert resolve_library_file(root, "../secret.mp3") is None
    assert resolve_library_file(root, "upbeat/../../secret.mp3") is None


def test_resolve_rejects_missing_and_bad_ext(tmp_path):
    root = _lib(tmp_path)
    assert resolve_library_file(root, "upbeat/nope.mp3") is None
    assert resolve_library_file(root, "upbeat/Carefree.exe") is None
    assert resolve_library_file(root, "") is None


def test_wrap_text_manual_newline_wins():
    lines = wrap_text("첫 줄\n둘째 줄", FONT, max_width=10_000)
    assert lines == ["첫 줄", "둘째 줄"]


def test_wrap_text_auto_wrap_within_manual_lines():
    # 수동 줄 안에서도 폭 초과 시 어절 단위로 다시 쪼개진다
    lines = wrap_text("아주 긴 문장이 폭을 넘어가면\n짧은 줄", FONT, max_width=200)
    assert "짧은 줄" in lines and len(lines) >= 3


def test_wrap_text_no_newline_unchanged():
    assert wrap_text("한 줄", FONT, max_width=10_000) == ["한 줄"]
