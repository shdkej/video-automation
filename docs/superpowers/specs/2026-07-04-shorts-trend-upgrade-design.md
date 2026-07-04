# 숏츠 트렌드 업그레이드 — 설계 스펙 (2026-07-04)

## 배경 / 왜

숏츠·릴스 트렌드(2026) 기준으로 현재 숏츠 산출물의 갭을 진단한 결과:

- **첫 프레임이 검정**: `apply_fade(fade_in=0.3)`이 숏츠에도 적용됨. 첫 1~3초에 이탈의
  50~60%가 몰리고 첫 프레임이 곧 커버 역할인데 페이드인은 안티패턴. hook 배너도
  14프레임 슬라이드인이라 frame 0에 텍스트가 없음.
- **컷이 기계적**: 긴 구간을 중앙 기준 절단 → 단어 중간 절단 가능. 구간 내부의
  침묵·추임새가 그대로 남아 템포가 죽음. 트렌드는 dead-air 제거 점프컷(3~5초당 컷)
  \+ punch-in으로 점프컷을 의도된 리듬으로 만드는 것.
- **자막 타이밍이 발화 단위**: 단어 stagger는 균일 2프레임 간격의 시각 효과일 뿐,
  실제 발화 타이밍과 무관. 트렌드 카라오케 자막은 말하는 순간 단어가 뜸.
- **화면 변화 부족**: "2.5초당 1회 화면 변화(Visual Velocity)" 기준 대비 자막 외
  변화 없음.
- **썸네일에 텍스트 없음**: 프레임 추출+그레이드뿐.

## 결정 사항 (사용자 확정)

| 항목 | 결정 |
|------|------|
| 침묵 제거 점프컷 | 넣는다. 컷 경계는 단어 경계 스냅 |
| 카라오케 자막 | 단어별 실제 발화 타이밍. 구캐시는 균일 stagger 폴백 |
| punch-in 줌 | 컷마다 1.0x↔1.08x 교차 |
| 썸네일 | hook 텍스트 burn-in + 숏츠별 세로 커버 추가 |

## 아키텍처

**ffmpeg footage + Remotion 오버레이 역할 분담 유지** (Remotion 올인원 기각 —
렌더 시간 수 배 증가, 4K 저코어 가드 무력화).

Python이 타임라인 계산의 단일 출처가 되고, ffmpeg가 footage(컷·punch-in·concat),
Remotion이 투명 자막 오버레이를 담당한다.

```
rank_for_shorts (단어 경계 스냅)
  → shorts_timeline.plan(): keep-intervals + remap 함수   ← 단일 출처
      ├─ effects.build_short_footage(): 서브클립 컷 + punch-in 교차 + concat
      └─ shorts_events(): 자막·단어 타이밍을 remap 통과시켜 이벤트 생성
  → render_subtitled_remotion (카라오케 words 전달)
  → 오디오만 0.2s 페이드아웃 (영상 페이드 없음)
```

## 구성 요소

### 1) `shorts_timeline.py` (신규, 순수 함수 — 외부 프로세스 의존 없음)

- `snap_to_words(start, end, words) -> (start, end)`: 윈도우 경계를 가장 가까운
  단어 경계로 스냅. 단어 중간 절단 제거.
- `cut_silences(start, end, words, min_silence=0.45, pad=0.12, min_clip=0.6)
  -> [(s, e), ...]`: 윈도우 내부 0.45초+ 무음을 제거한 keep-interval 목록.
  발화 앞뒤 0.12초 패딩, 서브클립 최소 0.6초 가드.
- `Timeline` (keep-intervals 보유): `remap(t_src) -> t_new`, `duration`.
  자막 이벤트·단어 타이밍은 전부 이 remap을 통과한다.
- transcript/words 없으면 통짜 1개 interval 반환 → 기존 동작과 동일 폴백.
  scene/vision·구캐시에서 크래시 없음.

### 2) footage 빌드 (`effects.py`)

- `build_short_footage(input, intervals, output, punch_scale=1.08)`:
  interval별 서브클립 컷 → 홀수번째 서브클립에 punch-in(center crop 1.08x) →
  concat(동일 코덱, filter concat). 무음 지점에서 자르므로 오디오 클릭 위험 낮음.
- 무전사(interval 1개) 폴백: ~4초 주기로 가상 컷을 만들어 punch 교차 적용.
- 숏츠 페이드 변경: 영상 페이드인/아웃 제거(frame 0 풀노출), 오디오만 끝 0.2초
  페이드(팝 방지).

### 3) 카라오케 자막

- `auto_cut.transcribe_video`: `word_timestamps=True`. transcript segment에
  `words: [{word, start, end}]` 추가. **구캐시(words 없음)는 균일 stagger 폴백.**
- 자막 이벤트 스키마 확장: `words: [{text, start, end}]` (이벤트 상대시간).
- `SubtitleOverlay.tsx` shorts 모드: `words`가 있으면 단어 등장 프레임을 실제
  발화 시점으로, 없으면 현행 균일 stagger 유지.
- `HookBanner`: 슬라이드인 제거 → frame 0부터 완전 노출.

### 4) 썸네일

- 기존 가로 후보에 최상위 hook 문구 PIL burn-in (Pretendard ExtraBold + 검정
  외곽선 — 숏츠 자막과 동일 룩).
- 숏츠별 세로 커버 `shorts_NN_cover.jpg` 추가(해당 숏츠 hook 포함, 릴스 커버용).
- `--no-thumb-text`로 끌 수 있음.

### 5) A/B 플래그 · 측정

- `--no-shorts-jumpcut`, `--no-shorts-punchin`, `--shorts-silence-min`(기본 0.45)
  → 전후 비교(A/B) 가능.
- 완료 요약에 숏츠별 측정치: 제거 무음 총량(초), 컷 수, 초당 화면변화 횟수.

### 6) 에러 처리

- 종 단위 부분 실패 격리는 현행 유지 (`step()`).
- 타임라인 계산 결과가 비정상(총길이 < 3초 등)이면 점프컷을 포기하고 통짜
  interval로 폴백 — 산출물이 안 나오는 것보다 현행 수준 산출이 낫다.

### 7) 테스트

- `shorts_timeline.py`는 순수 함수 → 단위 테스트: 단어 스냅, 무음 컷(패딩·최소
  길이 가드), remap 왕복, words 없음 폴백.
- 이벤트 remap: 점프컷 후 자막 타이밍이 keep-interval 안에만 존재하는지.
- 가장 간단한 성공 케이스(PRD 대응): 합성 words로 plan → footage 인자 검증.

## 범위 밖

- SFX/BGM 에셋 동봉(라이선스), Remotion 올인원 전환, 롱폼 편집 변경.

## 근거 (트렌드 소스)

- 첫 3초 이탈 50~60%, 첫 프레임=커버: miraflow.ai, conthunt.app (2026 가이드)
- 점프컷 3~5초 간격 엔게이지 +32%, dead-air 제거: insideeditors.com, later.com
- punch-in/마이크로줌 표준 문법, 카라오케(kinetic) 자막: invideo.io, snshelper.com
- 무음 시청 85% → 자막 필수: joinbrands.com
