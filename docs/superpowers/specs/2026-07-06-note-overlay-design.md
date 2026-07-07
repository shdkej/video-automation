# NoteOverlay — 영상 위에 노트 페이지가 떠 있는 연출 (프로토타입)

날짜: 2026-07-06 · 상태: 프로토타입

## 목표

인스타 릴( https://www.instagram.com/reel/DXt2sKeh6fY/ ) 스타일 재현:
배경 영상이 풀스크린으로 재생되고, 그 장소를 그린 스케치(노트 페이지)가
화면 중앙에 그림자를 달고 떠 있다. 페이지는 책장 넘기듯 flip으로 등장/퇴장.

## 접근 (합의됨)

- ffmpeg overlay 대신 **Remotion** — 3D flip·spring·float가 CSS/spring으로 자연스럽게 됨
- 도입부 실촬영 없이 **순수 합성 프로토타입** 먼저
- 테스트 소재는 **placeholder 자동 생성** (사용자 선택)

## 구성

| 파일 | 역할 |
|---|---|
| `remotion-map/src/NoteOverlay.tsx` | 컴포지션. props: `videoSrc`, `pages[{src,start,end}]`, 해상도/fps |
| `scripts/make_note_demo.py` | placeholder 생성 — PIL 종이질감 스케치 2장 + ffmpeg 배경 12s (3장면, 슬로우 줌) |
| `remotion-map/src/Root.tsx` | `NoteOverlay` 컴포지션 등록 |
| `note_overlay.py` | 실소재 CLI — 임의 경로 영상+이미지를 props JSON으로 렌더. 해상도/fps/길이는 ffprobe로 자동, 타이밍 생략 시 균등 배분 |

## 실소재 동적 입력 (2026-07-07 추가)

`python note_overlay.py video.mp4 pages.json` 한 줄로 렌더.
pages.json은 `[{"src","start","end"}]`(타이밍 지정) 또는 `["a.png","b.png"]`(균등 배분).

스테이징 제약 두 가지를 실측으로 확인:
- 사전 번들(build/)이 public을 복사해 가므로, 렌더 시점 소재는 **build/public**에 넣어야 보인다
- Remotion 정적 서버(serve-handler)는 **symlink를 기본 404** 처리 → hardlink 우선, 복사 폴백

## 연출 상세

- 배경: `OffthreadVideo` cover 풀스크린
- 노트 페이지: 화면 폭 ~78%, 중앙, `drop-shadow`
  - 등장: rotateY -100°→0° spring flip (transform-origin left — 책장 넘기는 방향)
  - 유지: 느린 sine float (translateY ±6px, rotate ±0.6°)
  - 퇴장: rotateY 0→70° + fade
- 스케치가 그려진 대상과 같은 장면 위에 뜨도록 타이밍 정렬 (릴 원본 문법)

## 검증

렌더 후 페이지 등장/유지/퇴장 시점 프레임 추출해 육안 확인.

## 웹 UI 통합 (2026-07-07 추가)

별도 컨트롤 없이 **파일 조합이 곧 의도**: 드롭존에 영상 + 이미지(png/jpg/webp)를
함께 올리면 노트 오버레이 모드로 전환 (CTA·힌트 문구 변경, 4종 UI 숨김).
- `POST /api/note-jobs` — 4종 파이프라인과 분리된 경량 잡. 옵션 없음(순서 = 업로드
  순서, 타이밍 = 균등 배분). 진행/결과는 기존 `GET /api/jobs/{id}` 공용.
- `_auto_pages`는 짧은 영상에서 간격부터 줄여 start < end ≤ duration 보장
  (tests/test_note_overlay.py가 회귀 검증).

## 이후 확장 (이번 범위 아님)

타이밍 수동 조정 UI, 도입부 실촬영 하이브리드, 노트 잡 재생성(rebuild).
