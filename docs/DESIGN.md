---
# Reel Room DESIGN.md — 모든 UI 작업의 단일 출처
# 방법론: getdesign.md / shadcn DESIGN.md (토큰은 여기 frontmatter, 근거는 본문)
# 테마: 다크 온리 (다크룸 편집실). 라이트 테마 없음 — 만들지 말 것.
theme: dark-only
tokens:
  color:
    background: "oklch(0.15 0.008 65)"        # 웜 블랙 (#0b0a09 계승)
    foreground: "oklch(0.94 0.012 85)"        # 웜 페이퍼 (#f2ece1 계승)
    card: "oklch(0.19 0.010 65)"              # 패널 표면 (#181513 계승)
    card-foreground: "oklch(0.94 0.012 85)"
    popover: "oklch(0.22 0.011 65)"           # 시트·팝오버 (한 단 밝게)
    popover-foreground: "oklch(0.94 0.012 85)"
    primary: "oklch(0.72 0.19 45)"            # 앰버 (#ff7a18 계승) — CTA·활성·플레이헤드
    primary-foreground: "oklch(0.15 0.008 65)"
    secondary: "oklch(0.24 0.012 65)"         # 보조 표면 (#211c18 계승)
    secondary-foreground: "oklch(0.85 0.012 85)"
    muted: "oklch(0.24 0.012 65)"
    muted-foreground: "oklch(0.62 0.015 80)"  # (#8a8076 계승)
    accent: "oklch(0.80 0.14 60)"             # 앰버 소프트 (#ffb15a 계승) — hover·강조 텍스트
    accent-foreground: "oklch(0.15 0.008 65)"
    destructive: "oklch(0.62 0.21 27)"        # REC 레드 (#ff3b30 계승) — 삭제·에러·녹화점
    border: "oklch(1 0 0 / 9%)"               # shadcn 다크 관례 — 흰색 알파 보더
    input: "oklch(1 0 0 / 14%)"
    ring: "oklch(0.72 0.19 45 / 55%)"         # 앰버 링
  radius:
    base: "0.625rem"    # 10px — 컨트롤(md)
    sm: "0.375rem"      # 칩·배지 내부 요소
    lg: "0.875rem"      # 카드·시트
    xl: "1rem"          # 플레이어 게이트·최상위 셸
  typography:
    display: "Fraunces"          # 마스트헤드 전용 — UI 텍스트 금지
    ui: "Pretendard"             # 모든 UI 텍스트
    mono: "IBM Plex Mono"        # 타임코드·메타 라벨·수치 전용
    scale-px: [12, 13, 14, 16, 20, 28]   # 이 6단 밖의 폰트 크기 금지
  spacing:
    unit: 4            # 4px 그리드 — 간격은 4의 배수만 (8/12/16/20/24)
    touch-target: 44   # 인터랙티브 최소 (px)
  motion:
    fast: "150ms"      # hover·토글
    sheet: "240ms cubic-bezier(0.32, 0.72, 0, 1)"
    respect-reduced-motion: true
---

# Reel Room 디자인 시스템

이 파일은 참고 문서가 아니라 **규칙**이다. UI를 만들거나 고치는 에이전트는 값이 필요하면
여기 frontmatter에서 가져오고, 컴포넌트가 필요하면 아래 어휘로만 조립한다.
여기 없는 스타일이 필요해지면 먼저 이 문서에 추가하고 나서 쓴다.

## 왜 이 팔레트인가

Reel Room은 "영상이 컴퓨터를 떠나지 않는 다크룸 편집실"이다. 배경은 순수 검정이 아니라
**웜 블랙**(필름 베이스), 액센트는 앰버 단일(암실 세이프라이트), 위험·녹화만 레드.
shadcn 다크 관례를 따라 **보더는 색이 아니라 흰색 알파** — 표면 위계는 밝기 단차
(background 0.15 → card 0.19 → popover 0.22)로만 만든다.

## 컴포넌트 어휘 — 기본값 · 이탈 조건 · 금지

### Button (`.btn`)
- 기본: `variant=secondary size=md`(h-36px, radius-base, 13px/500). 화면당 primary는 **1개**(적용→, 네 가지 만들기).
- `primary`: 앰버 배경+primary-foreground. hover는 accent로(밝게), 알파 감산 금지(웜 블랙 위에서 탁해짐).
- `ghost`: 배경 없음+muted-foreground, hover 시 secondary 배경. 인라인 보조 액션(교체·제거·전체 사용).
- `destructive`: 파일 제거·구간 삭제만.
- 크기 이탈: 트랜스포트 재생 버튼만 원형 44px.
- 금지: outline 스타일 남발(보더 버튼은 ghost로 통일), 한 줄에 primary 2개.

### Segmented (`.seg`) — 단일 선택 칩 그룹의 유일한 형태
- muted 트랙(radius-base) 안에 아이템(radius-sm), 활성 = `bg-input`(흰 알파) + foreground. shadcn Tabs 다크 관례.
- 쓰는 곳: 자막 스타일·크기, 썸네일 굵기·효과, 분석 방식(카드 유지 예외), 크롭 초점.
- 5개 초과 아이템이면 가로 스크롤 + 끝 페이드(mask). 항목별 색 힌트(썸네일 템플릿)는 유지 가능 — 트랙·활성 규칙은 동일.
- 금지: 같은 의미의 선택 UI를 chips/style-picks/pos-off 등 다른 모양으로 만드는 것.

### Switch (`.switch`) — boolean 전용
- 38×22 트랙, 켜짐 = primary 트랙. 라벨 13px foreground, 부연은 muted-foreground.
- 금지: boolean에 Segmented 사용, 체크박스 노출.

### Field (`.field`)
- 라벨(12px mono, muted-foreground, letter-spacing 0.04em) 위 + 컨트롤 아래, 간격 8px.
- 입력 배경 = background(패널보다 어둡게 파인 느낌), 보더 = input, focus = ring.
- 숫자 입력은 66~90px 고정폭 + 단위 라벨 밖에.

### Card (`.card`)
- radius-lg, bg-card, border. 그림자 금지 — 위계는 보더+밝기.
- 내부 패딩 16px, 섹션 간 12px.

### Sheet (`.sheet`) — 모바일 도구 패널의 유일한 컨테이너
- 도구바 위로 슬라이드 업, bg-popover, border-t, radius-lg 상단만, 그립 핸들 36×4px.
- max-height 48vh, 내부 스크롤. 배경 dim 없음(플레이어를 보면서 조작하는 게 목적).
- 열림/닫힘 `motion.sheet`. 데스크톱(≥900px)에선 시트 대신 우측 고정 패널 360px.

### Badge (`.badge`)
- radius-full, 11px mono. `count`(dirty 수·클립 수)=secondary, `live`(REC·처리 중)=destructive 점+텍스트, `hint`(B컷·자막 있음)=accent 배경.

### Slider (`.slider`)
- 트랙 h-6px muted, 채움 primary, 썸 16px 흰색+보더, focus ring 4px(ring/50).
- 트림 바는 슬라이더가 아니라 전용 컴포넌트(아래) — 혼용 금지.

### TrimBar (`.trim-bar`) — 도메인 전용
- 트랙 = background+border, 사용 창 = primary 22% 채움+primary 보더+양끝 핸들(터치 44px 히트영역, 시각 4px).
- 같은 클립의 다른 구간 = foreground 14% 고스트. 라벨 "1.0s / 클립 2.0s" mono 12px.

### Toast (`.toast`)
- bg-popover, border, radius-base, 13px. 성공은 텍스트만, 실패는 destructive 보더 + 재시도 액션.
- `edit-status` 같은 인라인 상태 한 줄은 진행형("업로드 중…")에만 — 완료·실패는 토스트로.

### Timeline (도메인 전용 — 기존 유지 + 토큰화)
- 필름스트립 셀 radius-sm, 선택 = primary 보더+inset ring, 제외 = opacity 0.35.
- 플레이헤드 = primary 2px + glow. 스프로킷 스트립 장식은 border 토큰 색.

## 페이지 조합 패턴

- **업로드**: 마스트헤드(Fraunces) → 드롭존 카드 → 세부 설정(접힘 카드) → primary CTA. 세로 단일 흐름, 카드 간 24px.
- **편집실(결과)**: 콤팩트 앱바 → 플레이어 게이트(radius-xl) → 트랜스포트 → 타임라인 → [도구 시트] → 도구바. 마스트헤드 없음. 산출물은 "내보내기" 탭 안.
- **진행**: 편집실과 같은 셸에서 게이트 자리에 프로그레스 — 별도 화면 아님.

## 안티패턴 (절대 금지)

1. **그라디언트 장식 금지** — 배경 그라디언트는 기존 stage 셸 1곳만 유지, 신규 금지.
2. **그림자로 위계 만들기 금지** — 보더+표면 밝기로. 예외: sticky 도구바 분리용 상단 그림자 1개.
3. **스케일 밖 값 금지** — 폰트 6단·간격 4배수·radius 4단 밖의 매직 넘버를 CSS에 쓰지 않는다.
4. **Fraunces를 UI에 쓰지 않는다** — 마스트헤드("Reel Room")와 완성 헤드라인 외 전부 Pretendard.
5. **한 화면에 같은 선택 UI 두 모양 금지** — 단일 선택은 Segmented, boolean은 Switch, 그 외엔 이유를 이 문서에 먼저 적는다.
