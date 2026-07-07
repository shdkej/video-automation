"""썸네일 타이틀 배치·줄바꿈 단위 테스트 — 이미지 렌더 없이 좌표 로직만."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from effects import (  # noqa: E402
    DEFAULT_THUMB_STYLE,
    HOOK_POSITIONS,
    THUMB_EFFECTS,
    THUMB_FONTS,
    THUMB_TEMPLATES,
    draw_thumb_effect,
    hook_anchor_x,
    hook_anchor_y,
    overlay_hook_text,
    resolve_thumb_style,
    thumb_font_path,
    wrap_hook_lines,
)


def test_effects_registry():
    assert THUMB_EFFECTS == ("none", "fireworks", "fire", "sparkle")


def test_draw_effect_none_is_identity():
    img = Image.new("RGB", (200, 100), (10, 10, 10))
    assert draw_thumb_effect(img, "none", (50, 40, 150, 60)) is img


def test_draw_effect_changes_pixels_and_is_deterministic():
    base = Image.new("RGB", (200, 100), (10, 10, 10))
    a = draw_thumb_effect(base.copy(), "sparkle", (50, 40, 150, 60)).convert("RGB")
    b = draw_thumb_effect(base.copy(), "sparkle", (50, 40, 150, 60)).convert("RGB")
    assert list(a.getdata()) != list(base.getdata())  # 뭔가 그려짐
    assert list(a.getdata()) == list(b.getdata())      # 시드 고정 — 재현 가능


def test_thumb_fonts_all_bundled():
    # 레지스트리의 모든 폰트 파일이 실제로 동봉돼 있어야 한다
    for key in THUMB_FONTS:
        assert thumb_font_path(key) is not None, key


def test_thumb_font_unknown_key():
    assert thumb_font_path("comic-sans") is None


def test_pretendard_weight_swaps_file():
    assert thumb_font_path("pretendard", "normal").name == "Pretendard-Bold.otf"
    assert thumb_font_path("pretendard", "bold").name == "Pretendard-ExtraBold.otf"
    # 단일 웨이트 폰트는 굵기와 무관하게 같은 파일
    assert thumb_font_path("jua", "normal") == thumb_font_path("jua", "heavy")


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


# ---------- 템플릿 ----------

def test_templates_registry():
    # 20종 고정 — 키·라벨·폰트가 전부 유효해야 프론트 칩과 파이프라인이 안전
    assert len(THUMB_TEMPLATES) == 20
    for key, t in THUMB_TEMPLATES.items():
        assert t["label"], key
        assert t["font"] in THUMB_FONTS, key
        assert t.get("effect", "none") in THUMB_EFFECTS, key


def test_resolve_custom_uses_individual_values():
    s = resolve_thumb_style("custom", font="jua", weight="heavy", effect="fire")
    assert (s["font"], s["weight"], s["effect"]) == ("jua", "heavy", "fire")
    assert s["fill"] == DEFAULT_THUMB_STYLE["fill"]  # 현행 기본 룩 유지


def test_resolve_template_overrides_individual_values():
    s = resolve_thumb_style("impact", font="nanumpen", weight="normal", effect="fire")
    assert s["font"] == "blackhan"      # 번들 값이 개별 값을 대체
    assert s["fill"] == (230, 28, 28)


def test_resolve_unknown_falls_back_to_custom():
    s = resolve_thumb_style("no-such-template", font="dohyeon")
    assert s["font"] == "dohyeon"


def test_overlay_renders_every_template(tmp_path):
    # PRD 최소 성공 케이스 — 10종 전부 에러 없이 그려지고 픽셀이 실제로 바뀐다
    base = Image.new("RGB", (540, 304), (24, 21, 19))
    for key in ["custom", *THUMB_TEMPLATES]:
        p = tmp_path / f"{key}.jpg"
        base.save(p, quality=92)
        overlay_hook_text(p, "오늘의 하이라이트\n지금 공개합니다", pos="top-center",
                          scale=1.5, template=key)
        out = Image.open(p).convert("RGB")
        assert list(out.getdata()) != list(base.getdata()), key


def test_overlay_template_is_deterministic(tmp_path):
    base = Image.new("RGB", (540, 304), (24, 21, 19))
    imgs = []
    for n in ("a", "b"):
        p = tmp_path / f"{n}.jpg"
        base.save(p, quality=92)
        overlay_hook_text(p, "재현성", pos="middle-center", template="sticker")
        imgs.append(list(Image.open(p).convert("RGB").getdata()))
    assert imgs[0] == imgs[1]
