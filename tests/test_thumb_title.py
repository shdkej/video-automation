"""썸네일 타이틀 배치·줄바꿈 단위 테스트 — 이미지 렌더 없이 좌표 로직만."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from effects import (  # noqa: E402
    HOOK_POSITIONS,
    THUMB_FONTS,
    hook_anchor_x,
    hook_anchor_y,
    thumb_font_path,
    wrap_hook_lines,
)


def test_thumb_fonts_all_bundled():
    # 레지스트리의 모든 폰트 파일이 실제로 동봉돼 있어야 한다
    for key in THUMB_FONTS:
        assert thumb_font_path(key) is not None, key


def test_thumb_font_unknown_key():
    assert thumb_font_path("comic-sans") is None


def test_positions_set():
    assert "bottom-center" in HOOK_POSITIONS and "top-left" in HOOK_POSITIONS
    assert len(HOOK_POSITIONS) == 9


def test_anchor_y():
    assert hook_anchor_y("top", 1000, 100) == 80
    assert hook_anchor_y("middle", 1000, 100) == 450
    assert hook_anchor_y("bottom", 1000, 100) == 780  # 1000 - 120 - 100


def test_anchor_x():
    assert hook_anchor_x("left", 1000, 300) == 60
    assert hook_anchor_x("center", 1000, 300) == 350
    assert hook_anchor_x("right", 1000, 300) == 640


def test_wrap_manual_newline_first():
    measure = lambda s: len(s) * 10  # noqa: E731
    assert wrap_hook_lines("첫 줄\n둘째 줄", measure, max_w=10_000) == ["첫 줄", "둘째 줄"]


def test_wrap_width_and_line_cap():
    measure = lambda s: len(s) * 10  # noqa: E731
    lines = wrap_hook_lines("가나다 라마바 사아자 차카타 파하", measure, max_w=70, max_lines=3)
    assert len(lines) == 3  # 상한에서 잘림
    assert all(measure(ln) <= 70 for ln in lines)
