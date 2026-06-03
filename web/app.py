#!/usr/bin/env python3
"""web/app.py — 4종 산출 파이프라인 웹 UI (FastAPI).

업로드 → 옵션 선택 → 백그라운드 잡 실행(진행률 폴링) → 결과 미리보기/다운로드.
파이프라인 함수(pipeline.py)를 그대로 import해 단계별로 호출하며 진행 상태를 갱신한다.

실행: uvicorn web.app:app --reload   (프로젝트 루트에서)
      또는  python web/app.py
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 프로젝트 루트를 import 경로에 추가 (pipeline.py가 루트에 있음)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pipeline as pl  # noqa: E402

BASE = Path(__file__).resolve().parent
JOBS_DIR = BASE / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE / "static"

# 단일 사용자 로컬 도구 가정 — 인메모리 잡 스토어로 충분
JOBS: dict[str, dict] = {}

app = FastAPI(title="video-automation")


# ============================================================================
# 잡 실행 (백그라운드 스레드)
# ============================================================================

def _args_from_opts(input_path: Path, outdir: Path, opts: dict) -> SimpleNamespace:
    """웹 옵션 → pipeline이 기대하는 args(Namespace). CLI 기본값을 그대로 채운다."""
    return SimpleNamespace(
        input=input_path, audio=None, outdir=outdir,
        mode=opts["mode"], target_minutes=float(opts["target_minutes"]),
        scene_threshold=0.3, clip_seconds=6.0,
        whisper_model=opts.get("whisper_model", "small"), language="ko",
        llm_model=None, cache=False,
        shorts_count=int(opts["shorts_count"]), shorts_max_seconds=45.0,
        shorts_ideal_seconds=25.0, shorts_blur=bool(opts.get("shorts_blur")),
        thumbnail_count=int(opts["thumbnail_count"]),
        intro_seconds=4.0,
        no_subtitle=bool(opts.get("no_subtitle")), no_grade=False,
        sub_font_size=56, sub_margin_v=80, only=None,
    )


def _run_job(job_id: str, input_paths: list[Path], opts: dict) -> None:
    job = JOBS[job_id]
    outdir = JOBS_DIR / job_id / "outputs"
    outdir.mkdir(parents=True, exist_ok=True)

    def stage(name: str, pct: int) -> None:
        job["stage"], job["progress"] = name, pct

    try:
        # 여러 소스면 먼저 공통 규격으로 정규화 후 이어붙임
        if len(input_paths) > 1:
            stage(f"{len(input_paths)}개 소스 이어붙이는 중", 5)
            input_path = outdir / "_merged_source.mp4"
            pl.concat_sources(input_paths, input_path)
        else:
            input_path = input_paths[0]
        args = _args_from_opts(input_path, outdir, opts)

        stage("분석 (Whisper/LLM)", 10)
        segments, captions = pl.analyze(args, outdir)
        (outdir / "selection.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2))
        (outdir / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2))
        job["segment_count"] = len(segments)

        outputs: dict = {}
        stage("롱폼 생성", 30)
        outputs["longform"] = pl.build_longform(args, segments, captions, outdir).name

        stage("숏츠 생성", 55)
        specs = pl.rank_for_shorts(
            segments, captions, args.shorts_count,
            args.shorts_max_seconds, args.shorts_ideal_seconds,
        )
        outputs["shorts"] = [
            pl.build_one_short(args, s, f"shorts_{n:02d}", outdir).name
            for n, s in enumerate(specs, 1)
        ]

        stage("썸네일 추출", 80)
        outputs["thumbnail"] = [p.name for p in pl.build_thumbnail(args, segments, outdir)]

        stage("인트로 생성", 92)
        outputs["intro"] = pl.build_intro(args, segments, outdir).name

        job["outputs"] = outputs
        job["status"], job["progress"], job["stage"] = "done", 100, "완료"
    except SystemExit as e:  # pipeline은 치명 오류에 sys.exit를 쓴다(BaseException)
        job["status"], job["error"] = "error", f"분석/생성 실패: {e}"
    except Exception as e:  # noqa: BLE001 — 잡 단위 격리
        job["status"], job["error"] = "error", str(e)


# ============================================================================
# API
# ============================================================================

@app.post("/api/jobs")
async def create_job(
    files: list[UploadFile],
    mode: str = Form("scene"),
    target_minutes: float = Form(3.0),
    shorts_count: int = Form(2),
    thumbnail_count: int = Form(3),
    shorts_blur: bool = Form(False),
    no_subtitle: bool = Form(False),
):
    if mode not in ("speech", "scene", "vision"):
        raise HTTPException(400, "mode는 speech/scene/vision 중 하나")
    if not files:
        raise HTTPException(400, "영상 파일이 필요합니다")
    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # 업로드 저장 — 확장자만 취해 안전한 파일명으로 (여러 개면 순서 보존)
    input_paths = []
    for idx, file in enumerate(files):
        suffix = Path(file.filename or f"input{idx}.mp4").suffix or ".mp4"
        p = job_dir / f"input_{idx:02d}{suffix}"
        with p.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        input_paths.append(p)

    JOBS[job_id] = {
        "status": "running", "stage": "대기", "progress": 0,
        "outputs": None, "error": None, "mode": mode,
        "source_count": len(input_paths),
    }
    opts = {
        "mode": mode, "target_minutes": target_minutes,
        "shorts_count": shorts_count, "thumbnail_count": thumbnail_count,
        "shorts_blur": shorts_blur, "no_subtitle": no_subtitle,
    }
    threading.Thread(target=_run_job, args=(job_id, input_paths, opts), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "잡 없음")
    return job


@app.get("/api/jobs/{job_id}/file/{name}")
async def get_file(job_id: str, name: str):
    if job_id not in JOBS:
        raise HTTPException(404, "잡 없음")
    # path traversal 방지 — outdir 안의 파일만
    outdir = (JOBS_DIR / job_id / "outputs").resolve()
    target = (outdir / name).resolve()
    if not str(target).startswith(str(outdir)) or not target.is_file():
        raise HTTPException(404, "파일 없음")
    download = name.endswith((".mp4", ".jpg"))
    return FileResponse(
        target,
        filename=name if download else None,
        media_type="video/mp4" if name.endswith(".mp4") else None,
    )


# 정적 파일 (index.html, app.js, style.css) — 마지막에 마운트(루트 "/")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
