# video-automation

긴 영상을 자동으로 하이라이트 컷하는 미니멀 CLI. 영상 종류에 따라 3가지 모드 지원.

진입점은 셋:

- **`web/app.py`** — 브라우저 UI. 업로드 → 옵션 → 진행률 → 4종 미리보기·다운로드 (FastAPI)
- **`pipeline.py`** — 하나의 소스에서 **롱폼·숏츠·썸네일·인트로 4종**을 한 번에 추출 (분석 1회 재사용)
- **`auto_cut.py`** — 하이라이트 컷 1개만 (롱폼) 뽑는 저수준 CLI. pipeline이 내부적으로 사용

## 웹 UI

```bash
# 웹 전용 의존성 설치 (CLI만 쓰면 불필요)
pip install -r requirements-web.txt

# 서버 실행 후 http://127.0.0.1:8000 접속
python web/app.py
# 또는: uvicorn web.app:app --reload
```

영상을 (여러 개도 가능 — 순서대로 이어붙임) 업로드하고 모드(scene 무료 / speech 자막 / vision)·숏츠 개수·썸네일 장수를 고르면, 백그라운드 잡이 돌며 진행률이 표시되고 완료 시 4종을 브라우저에서 미리보고 다운로드한다. 잡 작업물은 `web/jobs/<id>/`에 남는다(gitignore). 단일 사용자 로컬 도구 가정 — 잡 상태는 인메모리.

## 4종 산출 (pipeline.py)

```bash
# 한 줄로 4종 전체 생성 → outputs/
python pipeline.py input.mp4

# 여러 소스 → 순서대로 이어붙여 하나의 타임라인으로 처리
python pipeline.py clip1.mp4 clip2.mp4 clip3.mp4

# scene 모드(무료), 숏츠 3개, 세로 변환 시 흐린 배경
python pipeline.py input.mp4 --mode scene --shorts-count 3 --shorts-blur

# 일부만 — 숏츠와 썸네일만
python pipeline.py input.mp4 --only shorts thumbnail
```

> **여러 소스**를 주면 각 소스를 첫 소스 해상도에 맞춰 정규화(scale+pad·fps 통일·오디오 보장)한 뒤
> 이어붙여 단일 타임라인으로 분석한다. 해상도·코덱·오디오 유무가 달라도 안전하다.
> 합친 영상은 `outputs/_merged_source.mp4`로 남는다.

| 산출물 | 형태 | 비고 |
|--------|------|------|
| `longform.mp4` | 하이라이트 컷 16:9 | 클립별 자막 burn-in (**speech 모드만** — 아래 참고) |
| `shorts_NN.mp4` | 세로 9:16 | **적정 길이(기본 25초)에 가까운** 구간 상위 N개, 길면 중앙 기준 절단(hook 당김), 자막 + fade |
| `thumbnail_NN.jpg` | 후보 N장 | 구간을 시간축으로 분산해 N장(기본 3, 1장이면 `thumbnail.jpg`), 컬러 그레이드 |
| `intro.mp4` | hook 클립 3~5초 | 베스트 구간 앞부분 + fade (풀스크린 타이틀 카드 안 씀) |

> **자막은 speech 모드에서만 들어간다.** scene/vision은 발화 텍스트가 없어 자막을 비운다
> (디버그용 `scene_score=…` 같은 문자열이 영상에 박히지 않도록). 자막이 필요하면 speech 모드를 쓴다.

**부분 실패 격리**: 4종 중 하나가 실패해도 나머지는 생성되고, 끝에 실패한 종만 `--cache --only <종>`으로 재시도하라는 안내가 나온다. `--cache`는 `outputs/selection.json`을 재사용해 **LLM/Whisper 재호출 비용을 아낀다**.

주요 옵션: `--only`, `--shorts-count`(기본 2), `--shorts-ideal-seconds`(기본 25), `--shorts-max-seconds`(기본 45), `--shorts-blur`, `--thumbnail-count`(기본 3), `--intro-seconds`(기본 4), `--cache`, `--no-subtitle`, `--no-grade`. 분석 옵션(`--mode`/`-t`/`--llm-model` 등)은 auto_cut과 동일.

## 모드

| 모드 | 적합한 영상 | 신호원 | 비용 |
|------|------------|--------|------|
| `speech` (기본) | 음성 위주 (강연, 인터뷰, 브이로그) | Whisper 트랜스크립트 + LLM | $0.01~$0.5 / 1시간 |
| `scene` | 컷이 명확한 편집 영상 (트레일러, 광고) | ffmpeg scene 감지 | 무료 |
| `vision` | 정적/무음 영상 (풍경, 일상, b-roll) | 모자이크 그리드 + 비전 LLM | $0.01~$0.05 / 1시간 |

## 파이프라인

```
[speech]  영상 → Whisper(트랜스크립트) → LLM(구간 선정) → ffmpeg(컷+concat)
[scene]   영상 → ffmpeg scene 감지(점수)  → 상위 N개 선택  → ffmpeg(컷+concat)
[vision]  영상 → ffmpeg tile(모자이크)    → 비전 LLM(JSON) → ffmpeg(컷+concat)
```

## 설치

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ffmpeg 필요
brew install ffmpeg
```

## 사용

API 키는 `.env` 파일에 두거나(`cp .env.example .env`) 환경 변수로 export.

```bash
# speech 모드 (기본) — 음성 트랜스크립트 기반
python auto_cut.py input.mp4 -t 10

# 영상에 오디오가 없고 별도 파일로 있는 경우 (자동 mux)
python auto_cut.py video_only.mp4 --audio audio_only.m4a -t 5

# scene 모드 — 컷이 명확한 영상 (API 키 불필요)
python auto_cut.py input.mp4 --mode scene -t 5 --scene-threshold 0.3

# vision 모드 — 정적 영상도 OK, 모자이크 1장만 LLM에 전송
python auto_cut.py input.mp4 --mode vision -t 5 --llm-model gpt-4o-mini

# 모델 명시 — 이름 prefix로 provider 자동 분기
python auto_cut.py input.mp4 --llm-model claude-opus-4-7
python auto_cut.py input.mp4 --llm-model gpt-4o

# dry-run으로 선정 결과만 확인 (모자이크/트랜스크립트는 저장됨)
python auto_cut.py input.mp4 --mode vision --dry-run
python auto_cut.py input.mp4 --cache --dry-run
```

## 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--mode` | `speech` | `speech` / `scene` / `vision` |
| `--audio` | none | 별도 오디오 파일. 영상과 자동 mux 후 `<input>_av.mp4`를 입력으로 사용 |
| `-t, --target-minutes` | `10.0` | 목표 길이(분) |
| `-m, --whisper-model` | `medium` | speech 모드: `tiny`/`base`/`small`/`medium`/`large-v3` |
| `--language` | `ko` | speech 모드: 언어 코드 (`auto` 가능) |
| `--llm-model` | 자동 | `claude-*` → Anthropic, `gpt-*`/`o3-*` → OpenAI |
| `--scene-threshold` | `0.3` | scene 모드: ffmpeg scene 점수 임계값 (0~1) |
| `--clip-seconds` | `6.0` | scene/vision 모드: 각 클립 길이(초) |
| `--cache` | off | speech 모드: 트랜스크립트 재사용 |
| `--dry-run` | off | 컷 생략, 선정 결과만 저장 |

기본 LLM 자동 선택: `ANTHROPIC_API_KEY` → `claude-sonnet-4-6` / `OPENAI_API_KEY` → `gpt-4o-mini`.

## 모드별 동작 요약

**speech**: Whisper로 음성 → 한국어 인식 품질 검증(임계: 0.3자/초) → LLM에 트랜스크립트 + 목표 길이 전달 → LLM이 반환한 구간 중 트랜스크립트 segment와 겹치는 것만 채택 (환각 차단).

**scene**: ffmpeg `select='gt(scene,T)',metadata=print` 로 컷 포인트와 점수 추출 → 점수 상위부터 `clip-seconds` 길이 클립으로 만들어 목표 길이까지 채움 → 겹치는 클립은 스킵.

**vision**: ffmpeg `tile=NxM` 으로 영상을 균등 샘플링한 모자이크 1장 생성(예: 17.5분 → 7×8=56컷, 18.8초 간격) → 비전 LLM에 모자이크 + 시점 매핑 프롬프트 전송 → JSON으로 받은 시점들을 클립으로 변환.

## 비용 / 속도 감각

- Whisper `medium` (기본): 1시간 영상 M-시리즈 Mac 기준 15~25분 (로컬, 무료). 빠르게 보려면 `-m small` (5~10분)
- speech 모드 LLM (1시간, ~10k 입력 토큰):
  - `gpt-4o-mini` ≈ $0.005 / `gpt-4o` ≈ $0.08 / `claude-sonnet-4-6` ≈ $0.10 / `claude-opus-4-7` ≈ $0.50
- vision 모드 LLM: 모자이크 1장(~2240×1440, 약 1500~3000 입력 토큰) + JSON 응답
  - `gpt-4o-mini` ≈ $0.001 / `claude-sonnet-4-6` ≈ $0.01
- scene 모드: ffmpeg만 사용, 비용 0. 영상 길이의 1/3 정도 소요

## 알려진 한계

- speech: 트랜스크립트 정확도가 결과 품질 좌우. 잡음 영상은 `medium` 이상 권장. 컷 경계가 단어 중간에 걸릴 수 있음.
- scene: 카메라 고정/부드러운 영상은 scene 점수가 0.03 이하라 컷 포인트가 안 잡힘 → vision 모드 사용.
- vision: 모자이크 셀 해상도(320×180)에서 식별 가능한 수준의 차이만 잡힘. 미세한 움직임은 약함.

## 다음 개선 후보

- 화자 분리(pyannote)로 발화자 단위 컷
- 모드 자동 폴백 (speech 실패 시 vision)
- 숏츠 선정 고도화 (현재는 적정 길이 휴리스틱 + 중앙 절단 → LLM에 "숏폼 virality/hook 시점" 별도 질의)
- 썸네일 후보 랭킹 (현재는 시간 분산 → 얼굴/대비/장면전환 신호로 CTR 높은 프레임 우선)
- BGM 자동 삽입(effects.add_bgm) pipeline 연동
