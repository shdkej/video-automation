# 기술부채 정리 기록

4-에이전트 조사(Planner/Developer/Marketer/Operator)에서 도출한 ROI 톱 항목을
구현한 기록. 변경 동기와 검증 방법을 남겨 다음 세션이 이어받을 수 있게 한다.

## 완료 (2026-06-04)

### M1. `sys.exit` → `PipelineError` 예외 전환
- **문제**: 치명 오류에 `sys.exit`(BaseException)를 써서, 웹(`web/app.py`)이
  `except SystemExit`를 따로 잡아야 했고 분석 로직을 함수로 격리하기 어려웠다.
- **변경**:
  - `auto_cut.py`에 `PipelineError(Exception)` 정의.
  - `auto_cut.py`/`pipeline.py`의 치명 오류 `sys.exit` → `raise PipelineError`
    (입력 검증·LLM 환각·트랜스크립트 품질·씬 미감지 등).
  - 두 파일 `if __name__ == "__main__"`에서 `try: main() except PipelineError as e: sys.exit(str(e))`.
  - `web/app.py _run_job`의 `except SystemExit` 제거 — 이제 `except Exception`이 잡는다.
  - 유지: `pipeline.py:399` `sys.exit(1)`은 "부분 실패 종료코드"라 의미가 달라 보존.
- **효과**: 웹 잡이 한 종 실패에도 죽지 않고 격리, CLI는 동일하게 메시지+종료코드 1.

### M2. ffprobe 래퍼 → `probe.py` 통합
- **문제**: `get_video_duration`(auto_cut)와 `probe_duration`(effects)가 중복,
  `add_bgm`에 인라인 ffprobe까지 3중복.
- **변경**: `probe.py` 신설 — `probe_duration`/`has_audio_stream`/`has_video_stream`/`probe_resolution`.
  `auto_cut`/`effects`/`pipeline`이 모두 여기서 import. `effects`는 하위호환 re-export 유지.
- **효과**: ffprobe 옵션 변경·버그 수정 지점이 한 곳.

### M3. 순수 함수 pytest
- `tests/test_pure.py` — `validate_segments`/`filter_grounded_segments`/`overlaps`/
  `total_duration`/`rank_for_shorts`/`caption_for_segment`/`strip_leading_fillers`/
  `split_media` + `PipelineError` 계약. 14 케이스, ffmpeg/LLM 없이 0.05초.
- 실행: `pip install -r requirements-dev.txt && pytest tests/`

### 운영 가드 (web/app.py)
- **동시 잡 상한**: `threading.Semaphore(VIDAUTO_MAX_CONCURRENT_JOBS=2)`. create/rebuild에서
  비블로킹 acquire, 초과 시 429. `_run_job` finally에서 release.
- **업로드 크기 제한**: `_save_uploads`가 청크 스트리밍하며 잡 총합 `VIDAUTO_MAX_UPLOAD_MB=2048`
  초과 시 413 + 부분 저장 파일 정리.
- **LLM 비용 가드**: `select_highlights`가 프롬프트 길이 `VIDAUTO_MAX_TRANSCRIPT_CHARS=120000`
  초과 시 `PipelineError`로 중단(자르지 않고 사용자에게 분할/한도조정 선택권).

## 후순위 (미착수)
- reframe+fade ffmpeg 패스 병합(4→3) — 성능, 변경 위험 있어 마지막.
