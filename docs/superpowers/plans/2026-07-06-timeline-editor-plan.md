# 타임라인 편집기 구현 계획

스펙: `../specs/2026-07-06-timeline-editor-design.md` · 단계별 커밋, 최종 1회 배포

## 1단계 — BGM 선택 + 데모 + PIL 줄바꿈 (backend 중심)

- `web/media_library.py` (신규, FastAPI 무의존): `resolve_library_file(root, rel, exts)` — 경로 탈출 차단 순수 함수. app.py와 tests가 공유
- `GET /api/music/{mood}/{name}` — 미리듣기 서빙 (BGM_MOODS + resolve 가드)
- rebuild에 `bgm_choice: Form("auto")` — `auto`(기존)/`off`/`"mood/file.mp3"`. `_run_job`의 BGM 결정 2곳(자막만·일반)을 `_pick_bgm(job_id, outdir, opts, job)` 헬퍼로 통합
- `subtitle.py wrap_text` — 수동 `\n` 우선 분할 후 각 줄 자동 줄바꿈. `render_caption_png`의 max_width 없음 분기도 `text.split("\n")`
- 스타일 데모: `scripts/make_style_demos.py` — ffmpeg 그라디언트 2.5s 클립에 4스타일(fade/kinetic/impact/pil) 렌더 → `web/static/demos/{style}.mp4`. 생성은 파드에서 실행(remotion 사전 번들) 후 kubectl cp로 회수, 레포 커밋
- 테스트: resolve_library_file 가드, wrap_text `\n`

## 2단계 — 타임라인 UI (frontend 전면)

- `#edit-panel`+`#rebuild` → 단일 "편집" 섹션으로 통합:
  - 전체 설정 바: 자막 스타일(선택 시 데모 클립 재생) · 효과 토글(점프컷/펀치인/흐린배경/클린) · 숏츠/썸네일 수 · 음악 라디오 리스트(자동/끄기/곡별 ▶ 미리듣기, 현재 적용 곡 표시) · BGM 볼륨
  - 타임라인: 가로 스크롤, 트랙 3줄(영상 썸네일/글자/효과음), 구간 폭 ∝ 길이(min 64px)
  - 구간 탭 → 상세 카드: 사용 체크·시작/끝·자막 textarea(`\n`)·훅·효과음 선택(3단계)·**썸네일 위 자막 오버레이 근사**(Pretendard, `word-break: keep-all`, 세로/가로 비율별 파라미터)
  - 발화 교정 rows 유지
  - "다시 만들기" = 교정 저장(POST analysis) → rebuild(bgm_choice 포함)
- 미리듣기 공유 Audio 1개 — 재생 시 이전 정지. 모바일 터치 타겟 기준 유지

## 3단계 — 효과음

- 음원: `scripts/make_sfx.py` — numpy 합성(띠링·팝·휙·붐·두둥·클릭·타다·라이저 등 8~9개, 자체 생성이라 라이선스 자유) → `web/sfx/*.mp3` + `meta.json`(라벨)
- `GET /api/sfx` 목록 · `GET /api/sfx/{name}` 서빙 (resolve 가드)
- `POST analysis`: segment `sfx` 필드 통과(라이브러리 검증), selection.json에 저장
- 믹싱 `effects.add_sfx(input, events=[(sec, path)], output)` — adelay+amix(normalize=0)
- 이벤트 계산 순수 함수 (`pipeline.py`):
  - 롱폼: `compute_xfade_windows` w_start (1구간도 동작 확인됨)
  - 숏츠: spec↔segment overlap 매칭 → 해당 숏츠 t=0
  - 자막만: seg.start 원본 타임라인
  - 인트로: 제외
- `_run_job`: 산출물 생성 후 BGM 이전에 SFX 믹싱
- 테스트: 이벤트 계산(롱폼 윈도우·숏츠 매칭)

## 배포

단계 커밋 → push(1회) → CI 빌드 → rollout restart → 파드에서 /api/music·/api/sfx·정적 데모 스모크 + 모바일 뷰포트 스크린샷
