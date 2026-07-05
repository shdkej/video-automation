#!/usr/bin/env python3
"""web/app.py — 4종 산출 파이프라인 웹 UI (FastAPI).

업로드 → 옵션 선택 → 백그라운드 잡 실행(진행률 폴링) → 결과 미리보기/다운로드.
파이프라인 함수(pipeline.py)를 그대로 import해 단계별로 호출하며 진행 상태를 갱신한다.

실행: uvicorn web.app:app --reload   (프로젝트 루트에서)
      또는  python web/app.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
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

# 잡 폴더 자동 정리 기준 (환경변수로 조정 가능)
JOB_MAX_AGE_HOURS = float(os.environ.get("VIDAUTO_JOB_MAX_AGE_H", "24"))
JOB_MAX_COUNT = int(os.environ.get("VIDAUTO_JOB_MAX_COUNT", "20"))

# 운영 가드 (환경변수로 조정 가능)
# - 동시 잡 상한: Whisper/ffmpeg는 CPU·메모리를 많이 써 무제한 동시 실행 시 서버가 죽는다
# - 업로드 크기 상한: 거대 파일로 디스크가 차는 것을 막는다 (잡당 총합 기준)
MAX_CONCURRENT_JOBS = int(os.environ.get("VIDAUTO_MAX_CONCURRENT_JOBS", "2"))
MAX_UPLOAD_MB = float(os.environ.get("VIDAUTO_MAX_UPLOAD_MB", "2048"))
_UPLOAD_CHUNK = 1024 * 1024  # 1MB

# 동시 실행 슬롯. create/rebuild에서 비블로킹 acquire, _run_job finally에서 release.
_RUNNING = threading.Semaphore(MAX_CONCURRENT_JOBS)

app = FastAPI(title="video-automation")


def cleanup_jobs() -> None:
    """오래됐거나 개수를 초과한 잡 폴더를 삭제. 진행 중(running) 잡은 보호.

    새 잡 생성 시점에 호출 — 별도 스케줄러 없이 디스크 누적을 막는다.
    서버 재시작으로 JOBS(인메모리)가 비어도 디스크 폴더는 mtime으로 정리된다.
    """
    try:
        dirs = [d for d in JOBS_DIR.iterdir() if d.is_dir()]
    except FileNotFoundError:
        return

    def is_running(name: str) -> bool:
        job = JOBS.get(name)
        return bool(job and job.get("status") == "running")

    def drop(d: Path) -> None:
        shutil.rmtree(d, ignore_errors=True)
        JOBS.pop(d.name, None)

    now = time.time()
    # 1) 나이 초과
    for d in dirs:
        if is_running(d.name):
            continue
        try:
            age_h = (now - d.stat().st_mtime) / 3600
        except OSError:
            continue
        if age_h > JOB_MAX_AGE_HOURS:
            drop(d)

    # 2) 개수 초과 (최신 우선 보존, 오래된 것부터 삭제)
    survivors = [d for d in JOBS_DIR.iterdir() if d.is_dir() and not is_running(d.name)]
    survivors.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for d in survivors[JOB_MAX_COUNT:]:
        drop(d)


# ============================================================================
# 잡 실행 (백그라운드 스레드)
# ============================================================================

def _args_from_opts(input_path: Path, outdir: Path, opts: dict) -> SimpleNamespace:
    """웹 옵션 → pipeline이 기대하는 args(Namespace). CLI 기본값을 그대로 채운다."""
    return SimpleNamespace(
        input=input_path, audio=None, outdir=outdir,
        mode=opts["mode"], target_minutes=float(opts["target_minutes"]),
        scene_threshold=0.3, clip_seconds=6.0,
        whisper_model=opts.get("whisper_model", "medium"), language="ko",
        llm_model=None, cache=True,  # 같은 잡 폴더의 트랜스크립트/selection 재사용(재생성 대비)
        shorts_count=int(opts["shorts_count"]), shorts_max_seconds=45.0,
        shorts_ideal_seconds=25.0, shorts_blur=bool(opts.get("shorts_blur")),
        shorts_silence_min=0.45, no_shorts_jumpcut=False, no_shorts_punchin=False,
        thumbnail_count=int(opts["thumbnail_count"]), no_thumb_text=False,
        intro_seconds=4.0,
        no_subtitle=bool(opts.get("no_subtitle")), no_grade=False,
        sub_font_size=36, sub_margin_v=80, only=None,
        sub_engine=opts.get("sub_engine", "remotion"),
        sub_style=opts.get("sub_style", "fade"),
    )


def _run_job(job_id: str, input_paths: list[Path], opts: dict) -> None:
    job = JOBS[job_id]
    outdir = JOBS_DIR / job_id / "outputs"
    outdir.mkdir(parents=True, exist_ok=True)

    def stage(name: str, pct: int) -> None:
        job["stage"], job["progress"] = name, pct

    try:
        # 업로드 중 오디오 파일은 영상에 입힐 사운드트랙으로 분리 인식
        videos, audios = pl.split_media(input_paths)
        if not videos:
            raise ValueError("영상 파일이 없습니다 (오디오만으로는 처리할 수 없습니다).")

        if len(videos) > 1:
            stage(f"{len(videos)}개 영상 이어붙이는 중", 5)
            input_path = outdir / "_merged_source.mp4"
            pl.concat_sources(videos, input_path)
        else:
            input_path = videos[0]

        if audios:
            stage("오디오 입히는 중", 8)
            muxed = outdir / "_muxed_av.mp4"
            pl.mux_audio_into_video(input_path, audios[0], muxed)
            input_path = muxed

        args = _args_from_opts(input_path, outdir, opts)

        stage("분석 (Whisper/LLM)", 10)
        segments, captions, transcript = pl.analyze(args, outdir)
        (outdir / "selection.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2))
        (outdir / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2))
        job["segment_count"] = len(segments)

        outputs: dict = {}
        stage("롱폼 생성", 30)
        outputs["longform"] = pl.build_longform(args, segments, captions, outdir, transcript=transcript).name

        stage("숏츠 생성", 55)
        specs = pl.rank_for_shorts(
            segments, captions, args.shorts_count,
            args.shorts_max_seconds, args.shorts_ideal_seconds,
        )
        outputs["shorts"] = [
            pl.build_one_short(args, s, f"shorts_{n:02d}", outdir, transcript=transcript).name
            for n, s in enumerate(specs, 1)
        ]

        stage("썸네일 추출", 80)
        outputs["thumbnail"] = [p.name for p in pl.build_thumbnail(args, segments, captions, outdir)]

        stage("인트로 생성", 92)
        outputs["intro"] = pl.build_intro(args, segments, outdir).name

        job["outputs"] = outputs
        job["status"], job["progress"], job["stage"] = "done", 100, "완료"
    except Exception as e:  # noqa: BLE001 — 잡 단위 격리 (PipelineError 포함)
        job["status"], job["error"] = "error", str(e)
    finally:
        _RUNNING.release()  # 동시 잡 슬롯 반환


# ============================================================================
# API
# ============================================================================

def _save_uploads(files: list[UploadFile], job_dir: Path) -> list[Path]:
    """업로드를 청크 단위로 저장하며 잡 총합 크기를 제한한다.

    메모리에 통째로 올리지 않고 스트리밍하며 누적 바이트를 센다. 한도를 넘으면
    이미 쓴 파일까지 정리하고 413으로 거부 — 거대 파일로 디스크가 차는 것을 막는다.
    """
    limit = int(MAX_UPLOAD_MB * 1024 * 1024)
    input_paths: list[Path] = []
    total = 0
    for idx, file in enumerate(files):
        suffix = Path(file.filename or f"input{idx}.mp4").suffix or ".mp4"
        p = job_dir / f"input_{idx:02d}{suffix}"
        with p.open("wb") as f:
            while chunk := file.file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > limit:
                    f.close()
                    for done in input_paths + [p]:
                        done.unlink(missing_ok=True)
                    raise HTTPException(
                        413, f"업로드 총합이 한도({MAX_UPLOAD_MB:.0f}MB)를 초과했습니다."
                    )
                f.write(chunk)
        input_paths.append(p)
    return input_paths


@app.post("/api/jobs")
async def create_job(
    files: list[UploadFile],
    mode: str = Form("scene"),
    target_minutes: float = Form(3.0),
    shorts_count: int = Form(2),
    thumbnail_count: int = Form(3),
    shorts_blur: bool = Form(False),
    no_subtitle: bool = Form(False),
    sub_engine: str = Form("remotion"),
    sub_style: str = Form("fade"),
):
    if mode not in ("speech", "scene", "vision"):
        raise HTTPException(400, "mode는 speech/scene/vision 중 하나")
    if sub_engine not in ("pil", "remotion"):
        raise HTTPException(400, "sub_engine은 pil/remotion 중 하나")
    if sub_style not in ("fade", "kinetic"):
        raise HTTPException(400, "sub_style은 fade/kinetic 중 하나")
    if not files:
        raise HTTPException(400, "영상 파일이 필요합니다")
    # 동시 잡 상한 — 슬롯이 없으면 즉시 거부(429). 슬롯은 _run_job finally에서 반환.
    if not _RUNNING.acquire(blocking=False):
        raise HTTPException(
            429, f"동시 처리 한도({MAX_CONCURRENT_JOBS}개) 초과. 진행 중인 작업이 끝나면 다시 시도하세요."
        )
    cleanup_jobs()  # 새 잡 전에 오래된/초과 잡 폴더 정리
    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # 업로드 저장 — 확장자만 취해 안전한 파일명으로 (여러 개면 순서 보존)
    try:
        input_paths = _save_uploads(files, job_dir)
    except HTTPException:
        _RUNNING.release()  # 저장 실패 시 슬롯 반환(스레드 시작 전이므로 여기서)
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    JOBS[job_id] = {
        "status": "running", "stage": "대기", "progress": 0,
        "outputs": None, "error": None, "mode": mode,
        "source_count": len(input_paths),
    }
    opts = {
        "mode": mode, "target_minutes": target_minutes,
        "shorts_count": shorts_count, "thumbnail_count": thumbnail_count,
        "shorts_blur": shorts_blur, "no_subtitle": no_subtitle,
        "sub_engine": sub_engine, "sub_style": sub_style,
    }
    threading.Thread(target=_run_job, args=(job_id, input_paths, opts), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/rebuild")
async def rebuild_job(
    job_id: str,
    target_minutes: float = Form(3.0),
    shorts_count: int = Form(2),
    thumbnail_count: int = Form(3),
    shorts_blur: bool = Form(False),
    no_subtitle: bool = Form(False),
    sub_engine: str = Form("remotion"),
    sub_style: str = Form("fade"),
):
    """기존 잡의 분석(selection.json)을 재사용해 산출 옵션만 바꿔 다시 생성.

    Whisper/LLM 같은 비싼 분석은 cache=True로 재활용되고 build(ffmpeg)만 다시 돈다.
    mode 등 분석 자체를 바꾸려면 새로 업로드해야 한다.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "잡 없음")
    # input_* 글롭은 사이드카 캐시(input_NN.transcript.json)까지 잡으므로 미디어만 추린다.
    input_paths = sorted(
        p for p in job_dir.glob("input_*")
        if p.is_file() and not p.name.endswith(".transcript.json")
    )
    if not input_paths:
        raise HTTPException(404, "원본 입력이 남아있지 않습니다(정리됨). 새로 업로드해주세요.")
    # 재생성도 _run_job 스레드를 띄우므로 동시 잡 슬롯을 확보한다(finally에서 반환).
    if not _RUNNING.acquire(blocking=False):
        raise HTTPException(
            429, f"동시 처리 한도({MAX_CONCURRENT_JOBS}개) 초과. 진행 중인 작업이 끝나면 다시 시도하세요."
        )

    prev = JOBS.get(job_id, {})
    mode = prev.get("mode", "scene")
    JOBS[job_id] = {
        "status": "running", "stage": "재생성 대기", "progress": 0,
        "outputs": None, "error": None, "mode": mode,
        "source_count": prev.get("source_count", len(input_paths)),
    }
    opts = {
        "mode": mode, "target_minutes": target_minutes,
        "shorts_count": shorts_count, "thumbnail_count": thumbnail_count,
        "shorts_blur": shorts_blur, "no_subtitle": no_subtitle,
        "sub_engine": sub_engine, "sub_style": sub_style,
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
