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
import re
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
from auto_cut import BGM_MOODS, get_llm_usage, reset_llm_usage  # noqa: E402
from beats import detect_beats  # noqa: E402
from probe import probe_resolution  # noqa: E402
from effects import (  # noqa: E402
    DEFAULT_THUMB_POS,
    DEFAULT_THUMB_SCALE,
    HOOK_POSITIONS,
    THUMB_EFFECTS,
    THUMB_FONTS,
    THUMB_TEMPLATES,
    THUMB_WEIGHTS,
    add_bgm,
    add_sfx,
    overlay_hook_text,
)
from note_overlay import render_note_overlay  # noqa: E402
from web.media_library import resolve_library_file  # noqa: E402

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

# 동시 실행 슬롯 — 잡 스레드가 시작 시 blocking acquire(순차 큐), finally에서 release.
# 대기열 자체의 상한은 MAX_QUEUED_JOBS (업로드 디스크·대기 폭주 방지).
_RUNNING = threading.Semaphore(MAX_CONCURRENT_JOBS)
MAX_QUEUED_JOBS = int(os.environ.get("VIDAUTO_MAX_QUEUED_JOBS", "5"))

# Remotion 자막 스타일 — SubtitleOverlay.tsx의 SubStyle과 단일 계약
SUB_STYLES = ("fade", "kinetic", "impact", "bounce", "typewriter", "wave")


def _wait_for_slot(job: dict) -> None:
    """순차 큐 — 슬롯이 빌 때까지 대기. 대기 중엔 queued 상태로 폴링에 노출된다."""
    if not _RUNNING.acquire(blocking=False):
        job["status"], job["stage"] = "queued", "대기 중 — 앞선 작업이 끝나면 자동 시작"
        _RUNNING.acquire()
    job["status"], job["stage"] = "running", "시작"


def _reject_if_queue_full() -> None:
    queued = sum(1 for j in JOBS.values() if j.get("status") == "queued")
    if queued >= MAX_QUEUED_JOBS:
        raise HTTPException(
            429, f"대기열이 가득 찼습니다({MAX_QUEUED_JOBS}개) — 잠시 후 다시 시도하세요."
        )


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
        return bool(job and job.get("status") in ("running", "queued"))

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
        montage_seconds=float(opts.get("montage_seconds", 2.0)),
        thumbnail_count=int(opts["thumbnail_count"]),
        no_thumb_text=opts.get("thumb_pos", DEFAULT_THUMB_POS) == "off",
        thumb_text=str(opts.get("thumb_text", "")),
        thumb_pos=opts.get("thumb_pos", DEFAULT_THUMB_POS),
        thumb_font=opts.get("thumb_font", "pretendard"),
        thumb_effect=opts.get("thumb_effect", "none"),
        thumb_template=opts.get("thumb_template", "custom"),
        intro_seconds=4.0,
        no_subtitle=bool(opts.get("no_subtitle")), no_grade=False,
        sub_scale=float(opts.get("sub_scale", 1.0)),
        thumb_scale=float(opts.get("thumb_scale", DEFAULT_THUMB_SCALE)),
        thumb_weight=opts.get("thumb_weight", "bold"),
        no_scene_captions=not opts.get("scene_captions", True),
        subtitle_only=bool(opts.get("subtitle_only")),
        no_beat_sync=not opts.get("beat_sync", True),
        sub_font_size=int(36 * float(opts.get("sub_scale", 1.0))), sub_margin_v=80, only=None,
        sub_engine=opts.get("sub_engine", "remotion"),
        sub_style=opts.get("sub_style", "fade"),
    )


def _run_job(job_id: str, input_paths: list[Path], opts: dict) -> None:
    job = JOBS[job_id]
    _wait_for_slot(job)  # 순차 큐 — 앞선 잡이 끝나야 시작
    outdir = JOBS_DIR / job_id / "outputs"
    outdir.mkdir(parents=True, exist_ok=True)
    reset_llm_usage()  # 동시 잡 1개 전제 — 잡 단위 LLM 비용 추정 누적

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
        if opts["mode"] == "auto":
            job["mode_detected"] = args.mode  # analyze가 판별 결과로 치환

        wanted = [w for w in pl.WANTED if w in opts.get("outputs", pl.WANTED)]

        # 세로 입력엔 16:9 롱폼이 성립하지 않는다 — 건너뛰고 사유를 남긴다
        vw, vh = probe_resolution(input_path)
        if vh > vw and "longform" in wanted and not opts.get("subtitle_only"):
            wanted = [x for x in wanted if x != "longform"]
            job.setdefault("notes", []).append("세로 입력 — 16:9 롱폼은 건너뜀")

        # 몽타주(전체 유지)면 영상 산출물은 숏폼 1개로 통합
        montage = pl.is_montage(segments)
        if montage and not opts.get("subtitle_only"):
            wanted = [w for w in wanted if w not in ("longform", "intro")]
            job.setdefault("notes", []).append("짧은 클립 모음 — 숏폼 1개로 통합 (롱폼·인트로 생략)")

        outputs: dict = {}
        if opts.get("subtitle_only"):
            stage("자막 입히는 중", 60)
            outputs["subtitled"] = pl.build_subtitle_only(
                args, segments, captions, outdir, transcript=transcript,
            ).name
            if (outdir / "subtitled.srt").is_file():
                outputs["srt"] = "subtitled.srt"
            # 자막만 모드는 원본 타임라인 그대로 — 구간 시작 시각에 효과음
            sfx_evs = [(float(s["start"]), resolve_library_file(SFX_DIR, s["sfx"]))
                       for s in segments if s.get("sfx")]
            if any(p for _, p in sfx_evs):
                stage("효과음 입히는 중", 85)
                _mix_sfx_into(outdir, "subtitled.mp4", sfx_evs)
            bgm = _pick_bgm(job_id, outdir, opts, job)
            if bgm:
                stage("BGM 입히는 중", 90)
                mixed = outdir / ".subtitled.bgm.mp4"
                add_bgm(outdir / "subtitled.mp4", bgm, mixed,
                        bgm_volume=float(opts.get("bgm_volume", 0.3)))
                mixed.replace(outdir / "subtitled.mp4")
            job["outputs"] = outputs
            job["status"], job["progress"], job["stage"] = "done", 100, "완료"
            return

        if "longform" in wanted:
            stage("롱폼 생성", 30)
            outputs["longform"] = pl.build_longform(args, segments, captions, outdir, transcript=transcript).name

        specs: list = []  # 숏츠 spec — 효과음 매핑에 재사용
        if "shorts" in wanted:
            want_clean = bool(opts.get("shorts_clean"))
            if montage:
                stage("숏츠 생성 (몽타주 통합 1개)", 55)
                outputs["shorts"] = [pl.build_montage_short(
                    args, segments, captions, outdir, transcript=transcript,
                    clean_stem="shorts_01_clean" if want_clean else None,
                ).name]
                if (outdir / "shorts_01_clean.mp4").is_file():
                    outputs["shorts_clean"] = ["shorts_01_clean.mp4"]
            else:
                stage("숏츠 생성", 55)
                specs = pl.rank_for_shorts(
                    segments, captions, args.shorts_count,
                    args.shorts_max_seconds, args.shorts_ideal_seconds,
                )
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

        # 구간별 효과음 — BGM보다 먼저 돌려야 BGM 페이드가 전체에 걸린다.
        # 클린 버전·인트로는 제외(클린 = 효과 없는 동일 컷).
        if any(s.get("sfx") for s in segments):
            stage("효과음 입히는 중", 94)
            if outputs.get("longform"):
                _mix_sfx_into(outdir, outputs["longform"], [
                    (t, resolve_library_file(SFX_DIR, n))
                    for t, n in pl.sfx_events_longform(segments)
                ])
            if montage and outputs.get("shorts"):
                evs = [(t, resolve_library_file(SFX_DIR, n))
                       for t, n in pl.montage_sfx_events(segments)]
                if any(p for _, p in evs):
                    _mix_sfx_into(outdir, outputs["shorts"][0], evs)
            for spec, name in zip(specs, outputs.get("shorts", [])):
                n = pl.sfx_for_short(spec, segments)
                if n:
                    _mix_sfx_into(outdir, name, [(0.0, resolve_library_file(SFX_DIR, n))])

        bgm = _pick_bgm(job_id, outdir, opts, job)
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
        job["llm_usage"] = get_llm_usage()  # 추정치 — 정확 청구는 프로바이더 대시보드
        _RUNNING.release()  # 동시 잡 슬롯 반환


NOTE_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _run_note_job(job_id: str, input_paths: list[Path]) -> None:
    """노트 오버레이 잡 — 영상 위에 노트 이미지가 페이지처럼 떠오르는 연출.

    4종 파이프라인과 분리된 경량 경로: 분석(Whisper/LLM) 없이 Remotion 렌더 한 번.
    이미지 등장 타이밍은 영상 길이에 균등 배분(note_overlay.py), 순서는 업로드 순서.
    """
    job = JOBS[job_id]
    _wait_for_slot(job)  # 순차 큐 — 앞선 잡이 끝나야 시작
    outdir = JOBS_DIR / job_id / "outputs"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        notes = [p for p in input_paths if p.suffix.lower() in NOTE_IMAGE_SUFFIXES]
        videos, _ = pl.split_media([p for p in input_paths if p not in notes])
        if not videos or not notes:
            raise ValueError("영상 1개 이상 + 노트 이미지 1장 이상이 필요합니다.")

        if len(videos) > 1:
            job["stage"], job["progress"] = f"{len(videos)}개 영상 이어붙이는 중", 10
            merged = outdir / "_merged_source.mp4"
            pl.concat_sources(videos, merged)
            video = merged
        else:
            video = videos[0]

        job["stage"], job["progress"] = f"노트 오버레이 렌더 (이미지 {len(notes)}장)", 30
        render_note_overlay(video, notes, outdir / "note_overlay.mp4")

        job["outputs"] = {"note": "note_overlay.mp4"}
        job["status"], job["progress"], job["stage"] = "done", 100, "완료"
    except Exception as e:  # noqa: BLE001 — 잡 단위 격리
        job["status"], job["error"] = "error", str(e)
    finally:
        _RUNNING.release()


# ============================================================================
# API
# ============================================================================

def _find_bgm(job_dir: Path) -> Path | None:
    """잡 폴더의 BGM 파일 — 재생성 때도 재사용된다."""
    return next(iter(job_dir.glob("bgm.*")), None)


# BGM 라이브러리 — hostPath 마운트(파드 교체에도 유지), 무드 폴더별 mp3 + 메타(.json)
MUSIC_DIR = BASE / "music"

# SFX 라이브러리 — 합성 기본 세트(레포 동봉, scripts/make_sfx.py), mp3 추가로 확장
SFX_DIR = BASE / "sfx"


def _track_meta(p: Path) -> dict:
    meta = p.with_name(p.name + ".json")
    if meta.is_file():
        try:
            return json.loads(meta.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _auto_pick_bgm(outdir: Path) -> Path | None:
    """LLM이 고른 무드(mood.json) + 라이브러리에서 자동 선곡.

    speech 모드는 무드 LLM 콜이 없어 mood.json이 없다 — 그 경우 영상 템포로
    upbeat/calm을 추정해 폴백한다(명시 선택은 편집기 bgm_choice로 가능).
    영상 자체 템포(beats.json)가 있으면 BPM이 가장 가까운 곡, 없으면 첫 곡.
    """
    mood = None
    mpath = outdir / "mood.json"
    if mpath.is_file():
        try:
            mood = json.loads(mpath.read_text()).get("mood")
        except (json.JSONDecodeError, OSError):
            return None
    if not mood:
        mood = "calm"  # 템포도 모르면 무난한 기본값
        bjson = outdir / "beats.json"
        if bjson.is_file():
            try:
                vb = json.loads(bjson.read_text())
                if len(vb) > 1 and 60.0 / (vb[1] - vb[0]) >= 100:
                    mood = "upbeat"
            except (json.JSONDecodeError, OSError):
                pass
    d = MUSIC_DIR / str(mood)
    tracks = sorted(d.glob("*.mp3")) if d.is_dir() else []
    if not tracks:
        return None
    target_bpm = None
    bjson = outdir / "beats.json"
    if bjson.is_file():
        try:
            vb = json.loads(bjson.read_text())
            if len(vb) > 1:
                target_bpm = 60.0 / (vb[1] - vb[0])
        except (json.JSONDecodeError, OSError):
            pass

    def distance(p: Path) -> float:
        bpm = _track_meta(p).get("bpm")
        return abs(bpm - target_bpm) if (bpm and target_bpm) else 9999.0

    return min(tracks, key=distance)


def _mix_sfx_into(outdir: Path, name: str, events: list) -> None:
    """산출물 파일에 효과음 이벤트를 제자리 믹싱. 해석 실패(None) 이벤트는 버린다."""
    events = [(t, p) for t, p in events if p is not None]
    if not events:
        return
    src = outdir / name
    mixed = outdir / f".{name}.sfx.mp4"
    add_sfx(src, events, mixed)
    mixed.replace(src)


def _validate_thumb(pos: str, font: str, weight: str, effect: str, scale: float,
                    template: str = "custom") -> float:
    """썸네일 타이틀 파라미터 공통 검증 — 잘못된 값은 400, scale은 클램프해 반환."""
    if pos != "off" and pos not in HOOK_POSITIONS:
        raise HTTPException(400, "thumb_pos는 off 또는 top/middle/bottom-left/center/right")
    if font not in THUMB_FONTS:
        raise HTTPException(400, f"thumb_font는 {'/'.join(THUMB_FONTS)} 중 하나")
    if weight not in THUMB_WEIGHTS:
        raise HTTPException(400, f"thumb_weight는 {'/'.join(THUMB_WEIGHTS)} 중 하나")
    if effect not in THUMB_EFFECTS:
        raise HTTPException(400, f"thumb_effect는 {'/'.join(THUMB_EFFECTS)} 중 하나")
    if template != "custom" and template not in THUMB_TEMPLATES:
        raise HTTPException(400, f"thumb_template은 custom 또는 {'/'.join(THUMB_TEMPLATES)} 중 하나")
    return min(2.0, max(0.5, scale))


def _pick_bgm(job_id: str, outdir: Path, opts: dict, job: dict) -> Path | None:
    """BGM 결정 — 명시 선택(bgm_choice) > 업로드 곡 > 무드 자동 선곡 > 없음.

    bgm_choice: "auto"(기존 동작) | "off" | "무드/파일.mp3"(라이브러리 명시 선택).
    명시 선택이 유효하지 않으면 노트를 남기고 자동 경로로 폴백한다.
    """
    choice = str(opts.get("bgm_choice", "auto"))
    if choice == "off":
        return None
    if choice != "auto":
        track = resolve_library_file(MUSIC_DIR, choice)
        if track is not None:
            job["bgm_track"] = track.name
            job["bgm_credit"] = _track_meta(track).get("credit")
            return track
        job.setdefault("notes", []).append(f"선택한 BGM({choice}) 없음 — 자동 선곡으로 대체")
    bgm = _find_bgm(JOBS_DIR / job_id)
    if bgm is None and opts.get("bgm_auto", True):
        bgm = _auto_pick_bgm(outdir)
        if bgm:
            job["bgm_track"] = bgm.name
            job["bgm_credit"] = _track_meta(bgm).get("credit")
    return bgm


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
    subtitle_only: bool = Form(False),
    beat_sync: bool = Form(True),
    bgm_auto: bool = Form(True),
    montage_seconds: float = Form(2.0),
    thumb_text: str = Form(""),
    thumb_pos: str = Form(DEFAULT_THUMB_POS),
    thumb_font: str = Form("pretendard"),
    thumb_scale: float = Form(DEFAULT_THUMB_SCALE),
    thumb_weight: str = Form("bold"),
    thumb_effect: str = Form("none"),
    thumb_template: str = Form("custom"),
):
    thumb_scale = _validate_thumb(thumb_pos, thumb_font, thumb_weight, thumb_effect,
                                  thumb_scale, thumb_template)
    montage_seconds = min(10.0, max(0.0, montage_seconds))
    if mode not in ("auto", "speech", "scene", "vision"):
        raise HTTPException(400, "mode는 auto/speech/scene/vision 중 하나")
    if not outputs or set(outputs) - set(pl.WANTED):
        raise HTTPException(400, f"outputs는 {'/'.join(pl.WANTED)} 중에서 1개 이상")
    if sub_engine not in ("pil", "remotion"):
        raise HTTPException(400, "sub_engine은 pil/remotion 중 하나")
    if sub_style not in SUB_STYLES:
        raise HTTPException(400, f"sub_style은 {'/'.join(SUB_STYLES)} 중 하나")
    if shorts_focus not in ("left", "center", "right"):
        raise HTTPException(400, "shorts_focus는 left/center/right 중 하나")
    if not files:
        raise HTTPException(400, "영상 파일이 필요합니다")
    _reject_if_queue_full()  # 실행은 순차 큐(_wait_for_slot) — 대기열 상한만 거부
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
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    JOBS[job_id] = {
        "status": "queued", "stage": "대기", "progress": 0,
        "outputs": None, "error": None, "mode": mode,
        "source_count": len(input_paths),
        "subtitle_only": subtitle_only, "beat_sync": beat_sync,
        "bgm_auto": bgm_auto,
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
        "subtitle_only": subtitle_only, "beat_sync": beat_sync,
        "bgm_auto": bgm_auto, "montage_seconds": montage_seconds,
        "thumb_text": thumb_text, "thumb_pos": thumb_pos, "thumb_font": thumb_font,
        "thumb_scale": thumb_scale, "thumb_weight": thumb_weight,
        "thumb_effect": thumb_effect, "thumb_template": thumb_template,
    }
    threading.Thread(target=_run_job, args=(job_id, input_paths, opts), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/note-jobs")
async def create_note_job(files: list[UploadFile]):
    """노트 오버레이 잡 생성 — 영상 + 노트 이미지(png/jpg/webp)를 함께 업로드.

    옵션 없음(기본값 신뢰): 이미지 순서는 업로드 순서, 타이밍은 균등 배분.
    진행/결과 조회는 기존 GET /api/jobs/{id}를 그대로 쓴다.
    """
    names = [f.filename or "" for f in files]
    if not any(n.lower().endswith(NOTE_IMAGE_SUFFIXES) for n in names):
        raise HTTPException(400, "노트 이미지(png/jpg/webp)가 1장 이상 필요합니다")
    if all(n.lower().endswith(NOTE_IMAGE_SUFFIXES) for n in names):
        raise HTTPException(400, "배경 영상 파일이 필요합니다")
    _reject_if_queue_full()  # 실행은 순차 큐(_wait_for_slot) — 대기열 상한만 거부
    cleanup_jobs()
    job_id = uuid.uuid4().hex[:12]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        input_paths = _save_uploads(files, job_dir)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    JOBS[job_id] = {
        "status": "queued", "stage": "대기", "progress": 0,
        "outputs": None, "error": None, "mode": "note", "kind": "note",
        "source_count": len(input_paths),
    }
    threading.Thread(target=_run_note_job, args=(job_id, input_paths), daemon=True).start()
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
    subtitle_only: bool = Form(False),
    beat_sync: bool = Form(True),
    bgm_auto: bool = Form(True),
    bgm_choice: str = Form("auto"),
    thumb_text: str = Form(""),
    thumb_pos: str = Form(DEFAULT_THUMB_POS),
    thumb_font: str = Form("pretendard"),
    thumb_scale: float = Form(DEFAULT_THUMB_SCALE),
    thumb_weight: str = Form("bold"),
    thumb_effect: str = Form("none"),
    thumb_template: str = Form("custom"),
    sub_scale: float = Form(1.0),
):
    """기존 잡의 분석(selection.json)을 재사용해 산출 옵션만 바꿔 다시 생성.

    Whisper/LLM 같은 비싼 분석은 cache=True로 재활용되고 build(ffmpeg)만 다시 돈다.
    mode 등 분석 자체를 바꾸려면 새로 업로드해야 한다.
    """
    if not outputs or set(outputs) - set(pl.WANTED):
        raise HTTPException(400, f"outputs는 {'/'.join(pl.WANTED)} 중에서 1개 이상")
    thumb_scale = _validate_thumb(thumb_pos, thumb_font, thumb_weight, thumb_effect,
                                  thumb_scale, thumb_template)
    sub_scale = min(1.6, max(0.6, sub_scale))
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
    _reject_if_queue_full()  # 실행은 순차 큐(_wait_for_slot) — 대기열 상한만 거부
    prev = JOBS.get(job_id, {})
    if prev.get("status") in ("running", "queued"):
        raise HTTPException(409, "이 작업은 아직 진행·대기 중입니다 — 끝난 뒤 다시 만들어주세요.")
    mode = prev.get("mode", "scene")
    # 자막만 잡의 재생성은 자막만으로 유지 (프론트가 명시하지 않아도)
    subtitle_only = subtitle_only or bool(prev.get("subtitle_only"))
    JOBS[job_id] = {
        "status": "queued", "stage": "재생성 대기", "progress": 0,
        "outputs": None, "error": None, "mode": mode,
        "source_count": prev.get("source_count", len(input_paths)),
        "subtitle_only": subtitle_only, "beat_sync": beat_sync,
        "bgm_auto": bgm_auto,
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
        "subtitle_only": subtitle_only, "beat_sync": beat_sync,
        "bgm_auto": bgm_auto, "bgm_choice": bgm_choice,
        "thumb_text": thumb_text, "thumb_pos": thumb_pos, "thumb_font": thumb_font,
        "thumb_scale": thumb_scale, "thumb_weight": thumb_weight,
        "thumb_effect": thumb_effect, "thumb_template": thumb_template,
        "sub_scale": sub_scale,
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
    if (outdir / "subtitled.mp4").is_file():
        o["subtitled"] = "subtitled.mp4"
    if (outdir / "note_overlay.mp4").is_file():
        o["note"] = "note_overlay.mp4"
    if (outdir / "longform.srt").is_file():
        o["srt"] = "longform.srt"
    elif (outdir / "subtitled.srt").is_file():
        o["srt"] = "subtitled.srt"
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


@app.get("/api/music")
async def list_music():
    """BGM 라이브러리 목록 — 무드별 곡과 BPM/크레딧 메타."""
    lib = {}
    for mood in BGM_MOODS:
        d = MUSIC_DIR / mood
        lib[mood] = ([{"name": p.name, **_track_meta(p)} for p in sorted(d.glob("*.mp3"))]
                     if d.is_dir() else [])
    return {"moods": lib}


@app.get("/api/sfx")
async def list_sfx():
    """효과음 라이브러리 목록 — meta.json의 한글 라벨 포함."""
    meta = {}
    mpath = SFX_DIR / "meta.json"
    if mpath.is_file():
        try:
            meta = json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    items = ([{"name": p.name, "label": meta.get(p.name, p.stem)}
              for p in sorted(SFX_DIR.glob("*.mp3"))] if SFX_DIR.is_dir() else [])
    return {"sfx": items}


@app.get("/api/sfx/{name}")
async def get_sfx_file(name: str):
    """효과음 서빙 — 구간 상세의 미리듣기용."""
    p = resolve_library_file(SFX_DIR, name)
    if p is None:
        raise HTTPException(404, "효과음 없음")
    return FileResponse(p)


@app.get("/api/music/{mood}/{name}")
async def get_music_file(mood: str, name: str):
    """라이브러리 곡 서빙 — 결과 화면 미리듣기용."""
    if mood not in BGM_MOODS:
        raise HTTPException(404, "무드 없음")
    p = resolve_library_file(MUSIC_DIR, f"{mood}/{name}")
    if p is None:
        raise HTTPException(404, "곡 없음")
    return FileResponse(p)


@app.post("/api/music/{mood}")
async def upload_music(mood: str, file: UploadFile, credit: str = Form("")):
    """무드 폴더에 곡 추가 — 업로드 즉시 BPM을 측정해 메타로 남긴다."""
    if mood not in BGM_MOODS:
        raise HTTPException(400, f"mood는 {'/'.join(BGM_MOODS)} 중 하나")
    if not file.filename or not file.filename.lower().endswith((".mp3", ".m4a", ".wav")):
        raise HTTPException(400, "mp3/m4a/wav 오디오 파일이 필요합니다")
    d = MUSIC_DIR / mood
    d.mkdir(parents=True, exist_ok=True)
    safe = Path(file.filename).name.replace("/", "_")
    dest = d / safe
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    b = detect_beats(dest)
    bpm = round(60.0 / (b[1] - b[0])) if len(b) > 1 else None
    meta = {"bpm": bpm, "credit": credit.strip() or None}
    dest.with_name(dest.name + ".json").write_text(json.dumps(meta, ensure_ascii=False))
    return {"mood": mood, "name": safe, **meta}


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
            sfx = s.get("sfx")
            if sfx and resolve_library_file(SFX_DIR, str(sfx)) is None:
                raise HTTPException(400, f"효과음 없음: {sfx}")
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


@app.post("/api/thumb-preview")
async def thumb_preview(
    text: str = Form(""),
    pos: str = Form(DEFAULT_THUMB_POS),
    font: str = Form("pretendard"),
    scale: float = Form(DEFAULT_THUMB_SCALE),
    weight: str = Form("bold"),
    effect: str = Form("none"),
    template: str = Form("custom"),
    job_id: str = Form(""),
    t: float = Form(0.0),
    frame: UploadFile | None = File(None),
):
    """썸네일 타이틀 미리보기 — 실제 렌더러(overlay_hook_text)로 그대로 그려서 반환.

    바탕은 ①업로드 frame(영상 만들기 전, 브라우저가 뽑은 프레임) ②job_id+t(결과
    화면) ③그라디언트 플레이스홀더 순. CSS 근사가 아니라 산출물과 동일 픽셀.
    """
    import io
    import tempfile

    from fastapi.responses import Response
    from PIL import Image as PILImage

    scale = _validate_thumb(pos, font, weight, effect, scale, template)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp = Path(f.name)
    try:
        if frame and frame.filename:
            img = PILImage.open(io.BytesIO(await frame.read())).convert("RGB")
        elif job_id:
            jdir = JOBS_DIR / job_id
            merged = jdir / "outputs" / "_merged_source.mp4"
            src = merged if merged.is_file() else next(
                (p for p in sorted(jdir.glob("input_*"))
                 if p.is_file() and not p.name.endswith(".transcript.json")), None)
            if not src:
                raise HTTPException(404, "원본 없음")
            # 컨트롤을 만질 때마다 호출되므로 프레임은 캐시 — ffmpeg 추출은 1회
            fdir = jdir / ".frames"
            fdir.mkdir(exist_ok=True)
            cached = fdir / f"p_{max(0.0, t):.1f}.jpg".replace(".", "_", 1)
            if not cached.is_file():
                r = subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}",
                     "-i", str(src), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4",
                     str(cached)],
                    capture_output=True,
                )
                if r.returncode != 0 or not cached.is_file():
                    raise HTTPException(404, "프레임 추출 실패")
            img = PILImage.open(cached).convert("RGB")
        else:
            img = PILImage.new("RGB", (540, 304), (24, 21, 19))
        if img.width > 540:  # 미리보기는 실크기 불필요 — 렌더 속도 우선
            img = img.resize((540, int(img.height * 540 / img.width)))
        img.save(tmp, quality=88)
        if pos != "off" and text.strip():
            overlay_hook_text(tmp, text, pos=pos, font=font, scale=scale,
                              weight=weight, effect=effect, template=template)
        return Response(content=tmp.read_bytes(), media_type="image/jpeg")
    finally:
        tmp.unlink(missing_ok=True)


BROLL_MAX_MB = 15
BROLL_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_BROLL_RE = re.compile(r"^broll_[0-9a-f]{8}\.(png|jpe?g|webp)$")


@app.get("/api/jobs/{job_id}/broll/{name}")
async def get_broll(job_id: str, name: str):
    """B컷 이미지 서빙 — 서버 발급 이름만 (경로 조작 차단)."""
    if not _BROLL_RE.match(name):
        raise HTTPException(404, "파일 없음")
    target = JOBS_DIR / job_id / name
    if not target.is_file():
        raise HTTPException(404, "파일 없음")
    return FileResponse(target)


@app.post("/api/jobs/{job_id}/broll")
async def upload_broll(job_id: str, file: UploadFile = File(...)):
    """구간 B컷 이미지 업로드 — 서버 발급 이름(broll_<hex>.<ext>)을 돌려주고,
    편집기가 그 이름을 segment.broll에 실어 재생성 때 컷어웨이로 합성한다."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(404, "잡 없음")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in BROLL_EXTS:
        raise HTTPException(400, f"B컷은 이미지({'/'.join(sorted(BROLL_EXTS))})만 가능합니다")
    name = f"broll_{uuid.uuid4().hex[:8]}{ext}"
    size = 0
    with open(job_dir / name, "wb") as f:
        while chunk := await file.read(_UPLOAD_CHUNK):
            size += len(chunk)
            if size > BROLL_MAX_MB * 1024 * 1024:
                f.close()
                (job_dir / name).unlink(missing_ok=True)
                raise HTTPException(413, f"B컷 이미지는 {BROLL_MAX_MB}MB 이하")
            f.write(chunk)
    return {"name": name}


@app.get("/api/thumb-templates")
async def thumb_templates():
    """썸네일 타이틀 템플릿 목록 — 키·라벨·칩 힌트(폰트·대표색). 정의는 effects.py 단일 출처."""
    def chip_colors(t: dict) -> dict:
        fill = t["fill"]
        color = fill["gradient"][0] if isinstance(fill, dict) else fill
        bg = t.get("bg", {}).get("color") if t.get("bg") else None
        return {"color": "#%02x%02x%02x" % tuple(color[:3]),
                "bg": "#%02x%02x%02x" % tuple(bg[:3]) if bg else None}

    return [{"key": k, "label": t["label"], "font": t["font"],
             "weight": t.get("weight", "bold"), "effect": t.get("effect", "none"),
             **chip_colors(t)}
            for k, t in THUMB_TEMPLATES.items()]


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


# 동봉 폰트 — 편집기의 썸네일 타이틀 폰트 미리보기용 (@font-face)
app.mount("/fonts", StaticFiles(directory=str(ROOT / "assets" / "fonts")), name="fonts")

# 정적 파일 (index.html, app.js, style.css) — 마지막에 마운트(루트 "/")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
