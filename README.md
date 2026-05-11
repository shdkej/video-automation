# video-automation

긴 영상을 자동으로 하이라이트 컷하는 미니멀 CLI. 영상 종류에 따라 3가지 모드 지원.

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
| `-t, --target-minutes` | `10.0` | 목표 길이(분) |
| `-m, --whisper-model` | `small` | speech 모드: `tiny`/`base`/`small`/`medium`/`large-v3` |
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

- Whisper `small`: 1시간 영상 M-시리즈 Mac 기준 5~10분 (로컬, 무료)
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
- 자막(SRT) 자동 생성
- 9:16 변환(쇼츠)
