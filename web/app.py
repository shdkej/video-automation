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
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 프로젝트 루트를 import 경로에 추가 (pipeline.py가 루트에 있음)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pipeline as pl  # noqa: E402
from effects import add_bgm  # noqa: E402

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
        scene_threshold=float(opts.get("scene_threshold", 0.3)),
        clip_seconds=float(opts.get("clip_seconds", 6.0)),
        whisper_model=opts.get("whisper_model", "medium"), language="ko",
        llm_model=None, cache=True,  # 같은 잡 폴더의 트랜스크립트/selection 재사용(재생성 대비)
        shorts_count=int(opts["shorts_count"]),
        shorts_max_seconds=float(opts.get("shorts_max_seconds", 45.0)),
        shorts_ideal_seconds=float(opts.get("shorts_ideal_seconds", 25.0)),
        shorts_blur=bool(opts.get("shorts_blur")),
        shorts_focus=opts.get("shorts_focus", "center"),
        shorts_silence_min=0.45,
        no_shorts_jumpcut=not opts.get("shorts_jumpcut", True),
        no_shorts_punchin=not opts.get("shorts_punchin", True),
        thumbnail_count=int(opts["thumbnail_count"]), no_thumb_text=False,
        intro_seconds=4.0,
        no_subtitle=bool(opts.get("no_subtitle")), no_grade=False,
        no_scene_captions=not opts.get("scene_captions", True),
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

        wanted = [w for w in pl.WANTED if w in opts.get("outputs", pl.WANTED)]

        outputs: dict = {}
        if "longform" in wanted:
            stage("롱폼 생성", 30)
            outputs["longform"] = pl.build_longform(args, segments, captions, outdir, transcript=transcript).name

        if "shorts" in wanted:
            stage("숏츠 생성", 55)
            specs = pl.rank_for_shorts(
                segments, captions, args.shorts_count,
                args.shorts_max_seconds, args.shorts_ideal_seconds,
            )
            want_clean = bool(opts.get("shorts_clean"))
            outputs["shorts"] = [
                pl.build_one_short(
                    args, s, f"shorts_{n:02d}", outdir, transcript=transcript,
                    clean_stem=f"shorts_{n:02d}_clean" if want_clean else None,
                ).name
                for n, s in enumerate(specs, 1)
            ]
            # 클린은 자막 직전의 동일 컷을 남긴 것 — 자막이 안 들어간 숏츠는 생성되지 않음
            clean_names = [f"shorts_{n:02d}_clean.mp4" for n in range(1, len(specs) + 1)
                           if (outdir / f"shorts_{n:02d}_clean.mp4").is_file()]
            if clean_names:
                outputs["shorts_clean"] = clean_names

        if "thumbnail" in wanted:
            stage("썸네일 추출", 80)
            outputs["thumbnail"] = [p.name for p in pl.build_thumbnail(args, segments, captions, outdir)]

        if "intro" in wanted:
            stage("인트로 생성", 92)
            outputs["intro"] = pl.build_intro(args, segments, outdir, transcript=transcript).name

        if (outdir / "longform.srt").is_file():
            outputs["srt"] = "longform.srt"

        bgm = _find_bgm(JOBS_DIR / job_id)
        if bgm:
            stage("BGM 입히는 중", 96)
            videos_out = ([outputs.get("longform")] if outputs.get("longform") else []) \
                + outputs.get("shorts", []) + outputs.get("shorts_clean", []) \
                + ([outputs.get("intro")] if outputs.get("intro") else [])
            vol = float(opts.get("bgm_volume", 0.3))
            for name in videos_out:
                src = outdir / name
                mixed = outdir / f".{name}.bgm.mp4"
                add_bgm(src, bgm, mixed, bgm_volume=vol)
                mixed.replace(src)

        job["outputs"] = outputs
        job["status"], job["progress"], job["stage"] = "done", 100, "완료"
    except Exception as e:  # noqa: BLE001 — 잡 단위 격리 (PipelineError 포함)
        job["status"], job["error"] = "error", str(e)
    finally:
        _RUNNING.release()  # 동시 잡 슬롯 반환


# ============================================================================
# API
# ============================================================================

def _find_bgm(job_dir: Path) -> Path | None:
    """잡 폴더의 BGM 파일 — 재생성 때도 재사용된다."""
    return next(iter(job_dir.glob("bgm.*")), None)


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
    bgm: UploadFile | None = File(None),
    mode: str = Form("scene"),
    target_minutes: float = Form(3.0),
    shorts_count: int = Form(2),
    thumbnail_count: int = Form(3),
    shorts_blur: bool = Form(False),
    shorts_jumpcut: bool = Form(True),
    shorts_punchin: bool = Form(True),
    shorts_clean: bool = Form(True),
    scene_captions: bool = Form(True),
    no_subtitle: bool = Form(False),
    sub_engine: str = Form("remotion"),
    sub_style: str = Form("fade"),
    outputs: list[str] = Form(list(pl.WANTED)),
    scene_threshold: float = Form(0.3),
    clip_seconds: float = Form(6.0),
    shorts_max_seconds: float = Form(45.0),
    shorts_ideal_seconds: float = Form(25.0),
    shorts_focus: str = Form("center"),
    bgm_volume: float = Form(0.3),
):
    if mode not in ("speech", "scene", "vision"):
        raise HTTPException(400, "mode는 speech/scene/vision 중 하나")
    if not outputs or set(outputs) - set(pl.WANTED):
        raise HTTPException(400, f"outputs는 {'/'.join(pl.WANTED)} 중에서 1개 이상")
    if sub_engine not in ("pil", "remotion"):
        raise HTTPException(400, "sub_engine은 pil/remotion 중 하나")
    if sub_style not in ("fade", "kinetic"):
        raise HTTPException(400, "sub_style은 fade/kinetic 중 하나")
    if shorts_focus not in ("left", "center", "right"):
        raise HTTPException(400, "shorts_focus는 left/center/right 중 하나")
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
        if bgm and bgm.filename:
            ext = Path(bgm.filename).suffix.lower() or ".mp3"
            with open(job_dir / f"bgm{ext}", "wb") as f:
                shutil.copyfileobj(bgm.file, f)
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
        "shorts_jumpcut": shorts_jumpcut, "shorts_punchin": shorts_punchin,
        "shorts_clean": shorts_clean, "scene_captions": scene_captions,
        "sub_engine": sub_engine, "sub_style": sub_style,
        "outputs": outputs,
        "scene_threshold": scene_threshold, "clip_seconds": clip_seconds,
        "shorts_max_seconds": shorts_max_seconds,
        "shorts_ideal_seconds": shorts_ideal_seconds,
        "shorts_focus": shorts_focus, "bgm_volume": bgm_volume,
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
    shorts_jumpcut: bool = Form(True),
    shorts_punchin: bool = Form(True),
    shorts_clean: bool = Form(True),
    scene_captions: bool = Form(True),
    no_subtitle: bool = Form(False),
    sub_engine: str = Form("remotion"),
    sub_style: str = Form("fade"),
    outputs: list[str] = Form(list(pl.WANTED)),
    scene_threshold: float = Form(0.3),
    clip_seconds: float = Form(6.0),
    shorts_max_seconds: float = Form(45.0),
    shorts_ideal_seconds: float = Form(25.0),
    shorts_focus: str = Form("center"),
    bgm_volume: float = Form(0.3),
):
    """기존 잡의 분석(selection.json)을 재사용해 산출 옵션만 바꿔 다시 생성.

    Whisper/LLM 같은 비싼 분석은 cache=True로 재활용되고 build(ffmpeg)만 다시 돈다.
    mode 등 분석 자체를 바꾸려면 새로 업로드해야 한다.
    """
    if not outputs or set(outputs) - set(pl.WANTED):
        raise HTTPException(400, f"outputs는 {'/'.join(pl.WANTED)} 중에서 1개 이상")
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
        "shorts_jumpcut": shorts_jumpcut, "shorts_punchin": shorts_punchin,
        "shorts_clean": shorts_clean, "scene_captions": scene_captions,
        "sub_engine": sub_engine, "sub_style": sub_style,
        "outputs": outputs,
        "scene_threshold": scene_threshold, "clip_seconds": clip_seconds,
        "shorts_max_seconds": shorts_max_seconds,
        "shorts_ideal_seconds": shorts_ideal_seconds,
        "shorts_focus": shorts_focus, "bgm_volume": bgm_volume,
    }
    threading.Thread(target=_run_job, args=(job_id, input_paths, opts), daemon=True).start()
    return {"job_id": job_id}


def _outputs_from_dir(outdir: Path) -> dict:
    """산출물 폴더에서 outputs 딕셔너리 재구성 — 서버 재시작 후 결과 열람용."""
    o: dict = {}
    if (outdir / "longform.mp4").is_file():
        o["longform"] = "longform.mp4"
    shorts = sorted(p.name for p in outdir.glob("shorts_*.mp4")
                    if not p.name.endswith("_clean.mp4"))
    if shorts:
        o["shorts"] = shorts
    clean = sorted(p.name for p in outdir.glob("shorts_*_clean.mp4"))
    if clean:
        o["shorts_clean"] = clean
    thumbs = sorted(p.name for p in outdir.glob("thumbnail*.jpg"))
    if thumbs:
        o["thumbnail"] = thumbs
    if (outdir / "intro.mp4").is_file():
        o["intro"] = "intro.mp4"
    if (outdir / "longform.srt").is_file():
        o["srt"] = "longform.srt"
    return o


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job:
        return job
    # 인메모리 상태가 없어도(서버 재시작) 산출물이 디스크에 남아 있으면 재구성해 준다.
    outdir = JOBS_DIR / job_id / "outputs"
    outputs = _outputs_from_dir(outdir) if outdir.is_dir() else {}
    if not outputs:
        raise HTTPException(404, "잡 없음")
    segment_count = None
    sel = outdir / "selection.json"
    if sel.is_file():
        try:
            segment_count = len(json.loads(sel.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "status": "done", "stage": "완료", "progress": 100,
        "outputs": outputs, "error": None, "mode": None,
        "source_count": None, "segment_count": segment_count,
    }


def _transcript_path(job_id: str) -> Path | None:
    job_dir = JOBS_DIR / job_id
    return next(iter(job_dir.glob("input_*.transcript.json")), None)


@app.get("/api/jobs/{job_id}/analysis")
async def get_analysis(job_id: str):
    """편집자 검토용 분석 데이터 — 선정 구간·자막·훅 (+ speech는 발화 텍스트)."""
    outdir = JOBS_DIR / job_id / "outputs"
    sel = outdir / "selection.json"
    if not sel.is_file():
        raise HTTPException(404, "분석 없음")
    segments = json.loads(sel.read_text())
    cap = outdir / "captions.json"
    captions = json.loads(cap.read_text()) if cap.is_file() else ["" for _ in segments]

    transcript_texts = None
    tpath = _transcript_path(job_id)
    if tpath:
        tsegs = json.loads(tpath.read_text()).get("segments", [])
        transcript_texts = [
            {"i": i, "start": t["start"], "end": t["end"], "text": t["text"]}
            for i, t in enumerate(tsegs)
        ]
    return {"segments": segments, "captions": captions, "transcript": transcript_texts}


@app.post("/api/jobs/{job_id}/analysis")
async def update_analysis(job_id: str, payload: dict = Body(...)):
    """편집자 교정 저장 — 구간 조정/제외, 자막·훅 수정, 발화 텍스트 교정.

    파일(selection/captions/transcript)만 고쳐 두면 재생성(rebuild)이 캐시로
    읽어가므로 별도 재분석 없이 반영된다.
    """
    st = JOBS.get(job_id, {})
    if st.get("status") == "running":
        raise HTTPException(409, "작업이 진행 중입니다. 끝난 뒤 교정하세요.")
    outdir = JOBS_DIR / job_id / "outputs"
    sel = outdir / "selection.json"
    if not sel.is_file():
        raise HTTPException(404, "분석 없음")

    if "segments" in payload:
        segments = payload["segments"]
        captions = payload.get("captions", [])
        if not isinstance(segments, list) or not segments:
            raise HTTPException(400, "segments는 1개 이상이어야 합니다")
        if len(captions) != len(segments):
            raise HTTPException(400, "captions 길이가 segments와 같아야 합니다")
        for s in segments:
            try:
                if not float(s["start"]) < float(s["end"]):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "각 구간은 start < end 숫자여야 합니다")
        sel.write_text(json.dumps(segments, ensure_ascii=False, indent=2))
        (outdir / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2))

    if payload.get("transcript"):
        tpath = _transcript_path(job_id)
        if tpath:
            data = json.loads(tpath.read_text())
            tsegs = data.get("segments", [])
            for item in payload["transcript"]:
                i = item.get("i")
                text = str(item.get("text", "")).strip()
                if isinstance(i, int) and 0 <= i < len(tsegs) and text and tsegs[i]["text"].strip() != text:
                    tsegs[i]["text"] = text
                    tsegs[i].pop("words", None)  # 교정된 발화는 단어 타이밍 무효 — 카라오케는 균일 폴백
            tpath.write_text(json.dumps(data, ensure_ascii=False))
    return {"ok": True}


@app.get("/api/jobs/{job_id}/frame")
async def get_frame(job_id: str, t: float):
    """소스 타임라인 t초의 미리보기 프레임 — 구간 검토용 (캐시)."""
    job_dir = JOBS_DIR / job_id
    merged = job_dir / "outputs" / "_merged_source.mp4"
    src = merged if merged.is_file() else next(
        (p for p in sorted(job_dir.glob("input_*"))
         if p.is_file() and not p.name.endswith(".transcript.json")), None)
    if not src:
        raise HTTPException(404, "원본 없음")
    fdir = job_dir / ".frames"
    fdir.mkdir(exist_ok=True)
    fp = fdir / f"f_{max(0.0, t):.1f}.jpg".replace(".", "_", 1)
    if not fp.is_file():
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}",
             "-i", str(src), "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "5", str(fp)],
            capture_output=True,
        )
        if r.returncode != 0 or not fp.is_file():
            raise HTTPException(404, "프레임 추출 실패")
    return FileResponse(fp, media_type="image/jpeg")


@app.get("/api/jobs/{job_id}/archive")
async def get_archive(job_id: str):
    """산출물 전체 zip — mp4 재압축은 무의미하므로 무압축 저장."""
    outdir = JOBS_DIR / job_id / "outputs"
    if not outdir.is_dir():
        raise HTTPException(404, "잡 없음")
    files = [p for p in outdir.iterdir()
             if p.is_file() and not p.name.startswith((".", "_"))]
    if not files:
        raise HTTPException(404, "산출물 없음")
    bundle = JOBS_DIR / job_id / "bundle.zip"
    newest = max(p.stat().st_mtime for p in files)
    if not bundle.is_file() or bundle.stat().st_mtime < newest:
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
            for p in files:
                z.write(p, p.name)
    return FileResponse(bundle, filename=f"reelroom_{job_id}.zip", media_type="application/zip")


@app.get("/api/jobs/{job_id}/file/{name}")
async def get_file(job_id: str, name: str):
    # path traversal 방지 — outdir 안의 파일만 (인메모리 상태와 무관하게 디스크 기준)
    outdir = (JOBS_DIR / job_id / "outputs").resolve()
    target = (outdir / name).resolve()
    if not str(target).startswith(str(outdir)) or not target.is_file():
        raise HTTPException(404, "파일 없음")
    download = name.endswith((".mp4", ".jpg", ".srt"))
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
