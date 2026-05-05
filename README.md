# video-automation

긴 영상을 LLM이 고른 하이라이트 구간으로 자동 컷하는 미니멀 CLI.

## 파이프라인

```
입력 영상
  │
  ├─ [1] faster-whisper  → 트랜스크립트(JSON, 타임스탬프 포함)
  │                         캐시: <input>.transcript.json
  ├─ [2] Claude / GPT     → 하이라이트 구간 선정(JSON)
  │                         결과:  <input>.selection.json
  └─ [3] ffmpeg           → 구간별 컷 + concat
                            출력:  <input>_cut.mp4
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

API 키는 `.env` 파일에 두거나(`cp .env.example .env` 후 편집) 환경 변수로 export하면 됩니다.

```bash
# 방법 1: .env 파일 사용
cp .env.example .env
# 편집기로 열어 키 입력

# 방법 2: 직접 export
export ANTHROPIC_API_KEY=sk-ant-...
# 또는
export OPENAI_API_KEY=sk-...

# 1시간 영상을 10분으로 (LLM은 환경 변수 보고 자동 선택)
python auto_cut.py input.mp4

# 모델 명시 — 이름 prefix로 provider 자동 분기
python auto_cut.py input.mp4 --llm-model claude-opus-4-7
python auto_cut.py input.mp4 --llm-model gpt-4o
python auto_cut.py input.mp4 --llm-model o3-mini

# 길이/Whisper 모델 지정
python auto_cut.py input.mp4 -t 15 -m medium -o highlight.mp4

# 트랜스크립트 캐시 + dry-run으로 프롬프트 반복 튜닝
python auto_cut.py input.mp4 --dry-run
python auto_cut.py input.mp4 --cache --dry-run
```

## 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `-t, --target-minutes` | `10.0` | 목표 길이(분) |
| `-m, --whisper-model` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `--language` | `ko` | 언어 코드 (`auto` 가능) |
| `--llm-model` | 환경에 따라 자동 | `claude-*` → Anthropic, `gpt-*`/`o3-*` → OpenAI |
| `--cache` | off | `<input>.transcript.json` 재사용 |
| `--dry-run` | off | 컷 생략, 선정 결과만 저장 |

기본 모델 자동 선택:
- `ANTHROPIC_API_KEY` 있으면 `claude-sonnet-4-6`
- 없고 `OPENAI_API_KEY` 있으면 `gpt-4o-mini`
- 둘 다 없으면 에러

## 비용 / 속도 감각

- Whisper `small` 모델은 1시간 영상 기준 M-시리즈 Mac에서 5~10분 소요
- Claude Sonnet 또는 GPT-4o-mini로 1시간 트랜스크립트(~10k 토큰) 처리 시 $0.10 미만
- 캐시 + dry-run으로 프롬프트 튜닝하면 재실행 비용은 LLM 호출분만 발생

## 알려진 한계

- 음성 위주 영상에 최적. 시각적 임팩트만 있는 구간은 못 잡음
- 트랜스크립트 정확도가 결과 품질을 좌우. 잡음 많은 영상은 `medium` 이상 권장
- 컷 경계가 단어 중간에 걸리면 어색할 수 있음 → 향후 word-level timestamp로 보강 가능

## 다음 개선 후보

- 화자 분리(pyannote)로 발화자 단위 컷
- 시각 정보(얼굴/씬 변화) 보조
- 자막(SRT) 자동 생성
- 9:16 변환(쇼츠)
