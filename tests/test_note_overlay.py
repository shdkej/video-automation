"""note_overlay 순수 로직 테스트 — 균등 배분 타이밍 (_auto_pages).

렌더(Remotion/ffmpeg) 없이 페이지 타이밍 계산만 회귀 검증한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from note_overlay import AUTO_GAP_SEC, _auto_pages  # noqa: E402


def test_auto_pages_two_images_spread_evenly():
    pages = _auto_pages([Path("a.png"), Path("b.png")], duration=12.0)
    assert len(pages) == 2
    assert pages[0]["start"] == 0.8  # 앞 여백
    assert pages[1]["end"] == 11.5  # 뒤 여백 0.5
    # 페이지 사이 간격 — 배경이 온전히 보이는 숨 고르기
    assert pages[1]["start"] - pages[0]["end"] == AUTO_GAP_SEC
    # 두 슬롯 길이 동일
    d0 = pages[0]["end"] - pages[0]["start"]
    d1 = pages[1]["end"] - pages[1]["start"]
    assert abs(d0 - d1) < 0.01


def test_auto_pages_single_image_covers_middle():
    pages = _auto_pages([Path("a.png")], duration=10.0)
    assert len(pages) == 1
    assert pages[0]["start"] == 0.8
    assert pages[0]["end"] == 9.5


def test_auto_pages_stay_within_duration():
    for n in (1, 2, 3, 5):
        for dur in (5.0, 12.0, 60.0):
            pages = _auto_pages([Path(f"{i}.png") for i in range(n)], duration=dur)
            assert all(0 <= p["start"] < p["end"] <= dur for p in pages)
            # 순서 보존 + 겹침 없음
            for prev, cur in zip(pages, pages[1:]):
                assert prev["end"] <= cur["start"]
