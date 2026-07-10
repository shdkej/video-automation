// REEL ROOM — 업로드 → 폴링 → 결과
const $ = (id) => document.getElementById(id);
const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

const MODE_NOTES = {
  auto: "영상 특성(발화·씬·오디오)을 실측해서 분석 방식을 자동으로 고릅니다. 뭘 고를지 모르겠으면 이걸로.",
  scene: "ffmpeg 씬 감지로 컷 포인트를 찾습니다. API 키 불필요·무료. 자막은 speech 모드에서만 들어갑니다.",
  speech: "Whisper가 음성을 받아 적고 LLM이 핵심 구간을 고른 뒤 한국어 자막을 입힙니다. .env에 OPENAI_API_KEY 또는 ANTHROPIC_API_KEY 필요.",
  vision: "정적·무음 영상용. 시점별 모자이크 한 장을 비전 LLM이 분석합니다. API 키 필요.",
};

let pickedFiles = []; // 업로드 순서 = 타임라인 순서
let pollTimer = null;

// ---------- 완료 알림 (탭이 백그라운드일 때만) ----------
const BASE_TITLE = document.title;
function requestNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}
function notifyDone(ok, msg) {
  if (!document.hidden) return;
  document.title = (ok ? "✅ 완성" : "⚠️ 오류") + " — Reel Room";
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(ok ? "Reel Room — 완성" : "Reel Room — 처리 중단", { body: msg || "" }); } catch { /* 무시 */ }
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) document.title = BASE_TITLE;
});

// 자막 select(off/pil/fade/kinetic) → 백엔드 옵션 3종으로 분해
const KO_COUNT = ["", "한", "두", "세", "네"];
function pickedOutputs() {
  return [...document.querySelectorAll(".out-pick:checked")].map((el) => el.value);
}
function refreshCtaLabel() {
  if ($("subtitle_only").checked) { $("cta-label").textContent = "자막만 입히기"; return; }
  const n = pickedOutputs().length;
  $("cta-label").textContent = n === 0 ? "산출물을 선택하세요" : `${KO_COUNT[n]} 가지 만들기`;
}
document.querySelectorAll(".out-pick").forEach((el) => el.addEventListener("change", refreshCtaLabel));
$("subtitle_only").addEventListener("change", () => {
  // 자막만 모드에선 산출물 선택이 의미 없으므로 흐리게
  document.querySelectorAll(".out-pick").forEach((el) => { el.closest(".toggle").style.opacity = $("subtitle_only").checked ? 0.35 : 1; });
  refreshCtaLabel();
});

const REMOTION_STYLES = ["fade", "kinetic", "impact", "bounce", "typewriter", "wave"];
function appendSubOpts(fd, subMode) {
  const animated = REMOTION_STYLES.includes(subMode);
  fd.append("no_subtitle", subMode === "off");
  fd.append("sub_engine", animated ? "remotion" : "pil");
  fd.append("sub_style", animated ? subMode : "fade");
}

// ---------- 모드 카드 ----------
const cards = document.querySelectorAll(".mode-card");
function selectMode(mode) {
  cards.forEach((c) => c.classList.toggle("selected", c.dataset.mode === mode));
  $("mode").value = mode;
  $("mode-note").textContent = MODE_NOTES[mode];
  $("footer-mode").textContent = mode;
}
cards.forEach((c) => c.addEventListener("click", () => selectMode(c.dataset.mode)));
selectMode("auto");

// ---------- 드롭존 ----------
const dz = $("dropzone");
const fileInput = $("file");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });

["dragenter", "dragover"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragging"); })
);
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragging"); })
);
const AUDIO_RE = /\.(m4a|mp3|wav|aac|flac|ogg|opus|aiff?)$/i;
const MEDIA_RE = /\.(mp4|mov|mkv|webm|avi|m4v|m4a|mp3|wav|aac|flac|ogg|opus|aiff?)$/i;
const NOTE_IMG_RE = /\.(png|jpe?g|webp)$/i; // 노트 오버레이용 이미지
dz.addEventListener("drop", (e) => {
  const ok = [...e.dataTransfer.files].filter(
    (f) => f.type.startsWith("video/") || f.type.startsWith("audio/")
      || MEDIA_RE.test(f.name) || NOTE_IMG_RE.test(f.name)
  );
  addFiles(ok);
});

function addFiles(list) {
  for (const f of list) pickedFiles.push(f);
  renderFileList();
}

function fmtSize(bytes) {
  if (bytes > 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes > 1e6) return (bytes / 1e6).toFixed(0) + " MB";
  return (bytes / 1e3).toFixed(0) + " KB";
}

const isNoteImg = (f) => NOTE_IMG_RE.test(f.name) || f.type.startsWith("image/");
// 영상 + 이미지가 함께 있으면 노트 오버레이 모드 — 별도 컨트롤 없이 파일 조합이 곧 의도
function noteMode() {
  return pickedFiles.some(isNoteImg) &&
    pickedFiles.some((f) => !isNoteImg(f) && !AUDIO_RE.test(f.name) && !f.type.startsWith("audio/"));
}

function renderFileList() {
  const ul = $("file-list");
  ul.innerHTML = "";
  let vn = 0; // 영상만 순번, 오디오는 ♪, 노트 이미지는 🗒
  pickedFiles.forEach((f, i) => {
    const isAudio = AUDIO_RE.test(f.name) || f.type.startsWith("audio/");
    const isImg = isNoteImg(f);
    const li = document.createElement("li");
    li.className = "file-row" + (isAudio ? " audio" : "");
    const idx = document.createElement("span");
    idx.className = "fr-idx" + (isAudio ? " audio" : "");
    idx.textContent = isAudio ? "♪" : isImg ? "🗒" : String(++vn);
    const name = document.createElement("span"); name.className = "fr-name"; name.textContent = f.name;
    const size = document.createElement("span"); size.className = "fr-size"; size.textContent = fmtSize(f.size);
    const btns = document.createElement("span"); btns.className = "fr-btns";
    const up = document.createElement("button"); up.type = "button"; up.textContent = "↑"; up.title = "위로"; up.disabled = i === 0;
    const down = document.createElement("button"); down.type = "button"; down.textContent = "↓"; down.title = "아래로"; down.disabled = i === pickedFiles.length - 1;
    const rm = document.createElement("button"); rm.type = "button"; rm.textContent = "✕"; rm.title = "제거"; rm.className = "rm";
    up.onclick = () => { [pickedFiles[i - 1], pickedFiles[i]] = [pickedFiles[i], pickedFiles[i - 1]]; renderFileList(); };
    down.onclick = () => { [pickedFiles[i + 1], pickedFiles[i]] = [pickedFiles[i], pickedFiles[i + 1]]; renderFileList(); };
    rm.onclick = () => { pickedFiles.splice(i, 1); renderFileList(); };
    btns.append(up, down, rm);
    li.append(idx, name, size, btns);
    ul.append(li);
  });
  // 영상이 있어야 썸네일 미리보기 섹션이 나타난다 — 없으면 통째로 숨김
  const hasVideo = pickedFiles.some((f) => !isNoteImg(f) && !AUDIO_RE.test(f.name) && !f.type.startsWith("audio/"));
  const note = noteMode();
  // 노트 모드에선 4종 파이프라인 UI(썸네일 미리보기)가 무의미 — 숨기고 CTA만 바꾼다
  $("thumb-form-sec").classList.toggle("hidden", !hasVideo || note);
  $("note-hint").classList.toggle("hidden", !note);
  $("cta-label").textContent = note ? "노트 오버레이 만들기" : "네 가지 만들기";
  formTC.refresh(); // 첫 영상이 바뀌면 썸네일 타이틀 미리보기 바탕도 갱신
}

// ---------- 제출 ----------
// fetch는 업로드 진행률을 못 주므로 XHR — 모바일 업링크에선 업로드가 수 분 걸린다
function uploadWithProgress(url, fd, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded, e.total); };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* 비JSON 응답 */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body.detail || xhr.statusText || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("네트워크 오류 — 업로드에 실패했습니다"));
    xhr.send(fd);
  });
}

$("job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (pickedFiles.length === 0) { dz.classList.add("dragging"); setTimeout(() => dz.classList.remove("dragging"), 600); return; }
  if (noteMode()) return submitNoteJob();

  const fd = new FormData();
  pickedFiles.forEach((f) => fd.append("files", f)); // 순서 보존
  fd.append("mode", $("mode").value);
  fd.append("target_minutes", $("job-form").target_minutes.value);
  fd.append("shorts_count", $("job-form").shorts_count.value);
  fd.append("thumbnail_count", $("job-form").thumbnail_count.value);
  fd.append("shorts_blur", $("shorts_blur").checked);
  fd.append("shorts_jumpcut", $("shorts_jumpcut").checked);
  fd.append("shorts_punchin", $("shorts_punchin").checked);
  fd.append("shorts_clean", $("shorts_clean").checked);
  fd.append("scene_captions", $("scene_captions").checked);
  fd.append("shorts_focus", $("shorts_focus").value);
  fd.append("shorts_max_seconds", $("shorts_max").value);
  fd.append("shorts_ideal_seconds", $("shorts_ideal").value);
  fd.append("scene_threshold", $("scene_th").value);
  fd.append("clip_seconds", $("clip_sec").value);
  fd.append("montage_seconds", $("montage_sec").value);
  fd.append("bgm_volume", $("bgm_vol").value);
  if ($("bgm_file").files[0]) fd.append("bgm", $("bgm_file").files[0]);
  fd.append("subtitle_only", $("subtitle_only").checked);
  fd.append("beat_sync", $("beat_sync").checked);
  fd.append("bgm_auto", $("bgm_auto").checked);
  formTC.appendTo(fd);
  appendSubOpts(fd, $("sub_mode").value);
  if (!$("subtitle_only").checked) {
    const picked = pickedOutputs();
    if (picked.length === 0) { showError("산출물을 하나 이상 선택해주세요."); return; }
    picked.forEach((o) => fd.append("outputs", o));
  }

  $("submit-btn").disabled = true;
  requestNotifyPermission();
  hide($("form-section"));
  show($("progress-section"));
  $("stepper").classList.remove("hidden");
  updateStepper(0);
  $("bar-fill").style.width = "0%";
  $("stage-text").textContent = "업로드 준비…";
  try {
    const { job_id } = await uploadWithProgress("/api/jobs", fd, (loaded, total) => {
      const pct = Math.round((loaded / total) * 100);
      $("bar-fill").style.width = pct + "%";
      $("stage-text").textContent = pct >= 100
        ? "업로드 완료 — 처리 대기 중…"
        : `업로드 중 · ${pct}% (${fmtSize(loaded)} / ${fmtSize(total)})`;
    });
    const srcName = pickedFiles.length > 1
      ? `${pickedFiles[0].name} 외 ${pickedFiles.length - 1}개`
      : pickedFiles[0].name;
    saveRecentJob(job_id, { mode: $("mode").value, name: srcName });
    startPolling(job_id);
  } catch (err) {
    showError(err.message);
  } finally {
    $("submit-btn").disabled = false;
  }
});

// 노트 오버레이 제출 — 파일만 보내면 끝 (옵션 없음, 타이밍은 서버가 균등 배분)
async function submitNoteJob() {
  const fd = new FormData();
  pickedFiles.forEach((f) => fd.append("files", f)); // 순서 보존 = 페이지 순서
  $("submit-btn").disabled = true;
  requestNotifyPermission();
  hide($("form-section"));
  show($("progress-section"));
  $("stepper").classList.add("hidden"); // 4종 스텝퍼는 노트 잡과 무관
  $("bar-fill").style.width = "0%";
  $("stage-text").textContent = "업로드 준비…";
  try {
    const { job_id } = await uploadWithProgress("/api/note-jobs", fd, (loaded, total) => {
      const pct = Math.round((loaded / total) * 100);
      $("bar-fill").style.width = pct + "%";
      $("stage-text").textContent = pct >= 100
        ? "업로드 완료 — 처리 대기 중…"
        : `업로드 중 · ${pct}% (${fmtSize(loaded)} / ${fmtSize(total)})`;
    });
    const vid = pickedFiles.find((f) => !isNoteImg(f));
    saveRecentJob(job_id, { mode: "note", name: vid ? vid.name : pickedFiles[0].name });
    startPolling(job_id);
  } catch (err) {
    showError(err.message);
  } finally {
    $("submit-btn").disabled = false;
  }
}

// ---------- 폴링 + 스텝퍼 ----------
const STEP_THRESHOLDS = [
  { key: "분석", min: 0 }, { key: "롱폼", min: 30 }, { key: "숏츠", min: 55 },
  { key: "썸네일", min: 80 }, { key: "인트로", min: 92 },
];
function updateStepper(progress) {
  const steps = [...document.querySelectorAll("#stepper li")];
  let activeIdx = 0;
  STEP_THRESHOLDS.forEach((s, i) => { if (progress >= s.min) activeIdx = i; });
  steps.forEach((li, i) => {
    li.classList.toggle("done", i < activeIdx || progress >= 100);
    li.classList.toggle("active", i === activeIdx && progress < 100);
  });
}

let currentJobId = null;
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}
function startPolling(jobId) {
  if (bgJobId === jobId) clearBgWatch();
  currentJobId = jobId;
  stopPolling();
  let lastHiddenPoll = 0;
  let failStreak = 0; // 서버·프록시 일시 장애 연속 횟수 — 잠깐 죽었다 살아나면 이어서 폴링
  pollTimer = setInterval(async () => {
    // 탭이 백그라운드면 5초 간격으로만 — 모바일 배터리·데이터 절약
    if (document.hidden) {
      const now = Date.now();
      if (now - lastHiddenPoll < 5000) return;
      lastHiddenPoll = now;
    }
    // 5xx·비JSON(프록시 HTML 에러 페이지)·네트워크 단절은 전부 일시 장애로 취급.
    // res.json()이 HTML을 만나면 Safari는 알 수 없는 generic SyntaxError를 던지는데,
    // 잡은 서버에서 계속 돌고 있으므로 세션을 에러로 끝내면 안 된다.
    const transient = () => {
      failStreak++;
      $("stage-text").textContent = `서버 연결 대기 중… (${failStreak}초) — 잡은 서버에서 계속 돕니다`;
      if (failStreak >= 90) {
        stopPolling();
        showError("서버가 응답하지 않습니다 — 복구되면 '최근 작업'에서 이어서 확인할 수 있습니다");
      }
    };
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (res.status === 404) {
        stopPolling();
        showError("작업을 찾을 수 없습니다 (서버 재시작으로 정리됐을 수 있음)");
        return;
      }
      let job = null;
      if (res.ok) { try { job = await res.json(); } catch { /* HTML 에러 페이지 */ } }
      if (!job) { transient(); return; }
      failStreak = 0;
      const p = job.progress || 0;
      $("bar-fill").style.width = p + "%";
      $("stage-text").textContent = `${job.stage || ""} · ${p}%`;
      $("stepper").classList.toggle("hidden", job.kind === "note");
      updateStepper(p);
      if (job.status === "done") {
        stopPolling();
        notifyDone(true, "산출물이 준비됐습니다");
        renderResults(jobId, job);
      } else if (job.status === "error") {
        stopPolling();
        notifyDone(false, job.error || "");
        showError(job.error || "알 수 없는 오류");
      }
    } catch { transient(); }
  }, 1000);
}

// ---------- 백그라운드 잡 배너 — 진행 중인 잡을 두고 다른 잡을 볼 때 복귀 도선 ----------
let bgJobId = null;
let bgTimer = null;
function clearBgWatch() {
  bgJobId = null;
  if (bgTimer) { clearInterval(bgTimer); bgTimer = null; }
  hide($("job-banner"));
}
function watchBgJob(id) {
  clearBgWatch();
  bgJobId = id;
  const banner = $("job-banner");
  const tick = async () => {
    try {
      const res = await fetch(`/api/jobs/${id}`);
      if (!res.ok) { clearBgWatch(); return; }
      const job = await res.json();
      if (job.status === "running" || job.status === "queued") {
        banner.textContent = job.status === "queued"
          ? "⏳ 다른 작업 대기 중 — 보러 가기"
          : `⏳ 다른 작업 처리 중 · ${job.progress || 0}% — 보러 가기`;
        show(banner);
      } else {
        banner.textContent = job.status === "done"
          ? "✓ 다른 작업 완성 — 보러 가기"
          : "⚠ 다른 작업 중단됨 — 확인하기";
        show(banner);
        if (bgTimer) { clearInterval(bgTimer); bgTimer = null; }
        notifyDone(job.status === "done", "백그라운드 작업이 끝났습니다");
      }
    } catch { /* 다음 틱에 재시도 */ }
  };
  tick();
  bgTimer = setInterval(tick, 3000);
}
$("job-banner").addEventListener("click", () => {
  if (!bgJobId) return;
  const id = bgJobId;
  clearBgWatch();
  openJob(id);
});

// ---------- 결과 ----------
const fileUrl = (jobId, name) => `/api/jobs/${jobId}/file/${encodeURIComponent(name)}`;

// 파일 공유 지원 여부 (iOS·Android — 사진첩 저장, SNS 앱으로 바로 전달)
const CAN_SHARE_FILES = (() => {
  try {
    return !!navigator.canShare && navigator.canShare({ files: [new File([""], "t.mp4", { type: "video/mp4" })] });
  } catch { return false; }
})();

function cut(jobId, name, label, fmt, { vertical = false, image = false } = {}) {
  const url = fileUrl(jobId, name);
  const media = image
    ? `<img src="${url}" alt="${label}">`
    : `<video src="${url}" controls preload="metadata"></video>`;
  const share = CAN_SHARE_FILES
    ? `<button type="button" class="share-btn" data-url="${url}" data-name="${escHtml(name)}">공유 ↗</button>`
    : "";
  return `<div class="cut ${vertical ? "vertical" : ""}">
    <div class="cut-label"><span>${label}</span><span class="fmt">${fmt}</span></div>
    ${media}
    <div class="cut-actions"><a class="dl" href="${url}" download>${name}</a>${share}</div>
  </div>`;
}

// 공유 버튼 — 파일을 받아 OS 공유 시트로 (SNS 업로드·사진첩 저장)
$("results").addEventListener("click", async (e) => {
  const btn = e.target.closest(".share-btn");
  if (!btn) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "준비 중…";
  try {
    const res = await fetch(btn.dataset.url);
    const blob = await res.blob();
    const file = new File([blob], btn.dataset.name, { type: blob.type || "video/mp4" });
    await navigator.share({ files: [file] });
  } catch (err) {
    if (err.name !== "AbortError") {
      btn.textContent = "공유 불가 — 다운로드를 이용해주세요";
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500);
      return;
    }
  }
  btn.textContent = orig;
  btn.disabled = false;
});

function renderResults(jobId, job) {
  updateStepper(100);
  hide($("progress-section"));
  show($("result-section"));
  const bits = [];
  if (job.source_count > 1) bits.push(`${job.source_count}개 소스 결합`);
  if (job.segment_count != null) bits.push(`선정 구간 ${job.segment_count}개`);
  if (job.mode_detected) bits.push(`자동 판별 → ${job.mode_detected}`);
  (job.notes || []).forEach((n) => bits.push(n));
  const u = job.llm_usage;
  if (u && u.calls) bits.push(`LLM ~$${u.usd} (${u.calls}콜, 추정)`);
  if (job.bgm_track) bits.push(`BGM ${job.bgm_track}${job.bgm_credit ? ` (${job.bgm_credit})` : ""}`);
  $("seg-info").textContent = bits.join(" · ");

  const o = job.outputs || {};
  updateRecentJob(jobId, { out: outputsSummary(o) });
  let html = "";
  if (o.note) html += cut(jobId, o.note, "노트 오버레이", "영상+노트");
  if (o.subtitled) html += cut(jobId, o.subtitled, "자막본", "원본 그대로");
  if (o.longform) html += cut(jobId, o.longform, "롱폼", "16:9");
  (o.shorts || []).forEach((n, i) => (html += cut(jobId, n, `숏츠 ${i + 1}`, "9:16", { vertical: true })));
  (o.shorts_clean || []).forEach((n, i) => (html += cut(jobId, n, `숏츠 ${i + 1} 클린`, "9:16", { vertical: true })));
  if (o.intro) html += cut(jobId, o.intro, "인트로", "hook");
  (o.thumbnail || []).forEach((n, i) => (html += cut(jobId, n, `썸네일 ${i + 1}`, "JPG", { image: true })));
  $("results").innerHTML = html || "<p>생성된 산출물이 없습니다.</p>";

  $("dl-extra").innerHTML =
    `<a class="dl" href="/api/jobs/${jobId}/archive">전부 받기 ↓zip</a>` +
    (o.srt ? `<a class="dl" href="${fileUrl(jobId, o.srt)}" download>자막 ↓srt</a>` : "");
  renderCompare(jobId, o);
  // 노트 잡은 분석(selection.json)이 없어 편집기가 성립하지 않는다
  if (o.note) hide($("editor"));
  else { show($("editor")); initEditor(jobId, job); }
}

// 분석 재사용 재생성 — 편집기(전체 설정 바)의 값으로 다시 만들기
async function doRebuild() {
  if (!currentJobId) return;
  const fd = new FormData();
  fd.append("shorts_count", $("ed_shorts").value);
  fd.append("thumbnail_count", $("ed_thumb").value);
  fd.append("shorts_blur", $("ed_blur").checked);
  fd.append("shorts_jumpcut", $("ed_jumpcut").checked);
  fd.append("shorts_punchin", $("ed_punchin").checked);
  fd.append("shorts_clean", $("ed_clean").checked);
  fd.append("shorts_focus", $("shorts_focus").value);
  fd.append("shorts_max_seconds", $("shorts_max").value);
  fd.append("shorts_ideal_seconds", $("shorts_ideal").value);
  fd.append("bgm_volume", $("ed_bgmvol").value);
  fd.append("bgm_choice", bgmChoice);
  editorTC.appendTo(fd);
  fd.append("sub_scale", subScale);
  appendSubOpts(fd, styleChoice);

  hide($("result-section"));
  show($("progress-section"));
  $("bar-fill").style.width = "0%";
  $("stage-text").textContent = "재생성 준비…";
  try {
    const res = await fetch(`/api/jobs/${currentJobId}/rebuild`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    startPolling(currentJobId);
  } catch (err) {
    showError(err.message);
  }
}

function showError(msg) {
  stopPolling();
  [$("form-section"), $("progress-section"), $("result-section")].forEach(hide);
  show($("error-section"));
  $("error-text").textContent = msg;
}

function reset() {
  [$("result-section"), $("error-section"), $("progress-section")].forEach(hide);
  show($("form-section"));
  $("results").innerHTML = "";
  $("bar-fill").style.width = "0%";
}
$("reset-btn").addEventListener("click", reset);
$("error-reset-btn").addEventListener("click", reset);

// ---------- 최근 작업 패널 (localStorage + 서버 상태) ----------
const RECENT_KEY = "reelroom_recent_jobs";

function recentJobs() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch { return []; }
}

function saveRecentJob(id, meta = {}) {
  const list = recentJobs().filter((j) => j.id !== id);
  list.unshift({ id, ts: Date.now(), ...meta });
  localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 15)));
  renderJobPanel();
}

function updateRecentJob(id, patch) {
  const list = recentJobs();
  const idx = list.findIndex((j) => j.id === id);
  if (idx < 0) return;
  list[idx] = { ...list[idx], ...patch };
  localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  renderJobPanel();
}

function outputsSummary(o = {}) {
  const parts = [];
  if (o.note) parts.push("노트 오버레이");
  if (o.subtitled) parts.push("자막본");
  if (o.longform) parts.push("롱폼");
  if (o.shorts && o.shorts.length) parts.push(`숏츠${o.shorts.length}`);
  if (o.shorts_clean && o.shorts_clean.length) parts.push(`클린${o.shorts_clean.length}`);
  if (o.thumbnail && o.thumbnail.length) parts.push(`썸네일${o.thumbnail.length}`);
  if (o.intro) parts.push("인트로");
  return parts.join(" · ");
}

const escHtml = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtTs(ts) {
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function renderJobPanel() {
  const list = recentJobs();
  $("jp-empty").style.display = list.length ? "none" : "";
  $("job-list").innerHTML = "";
  list.forEach((j) => {
    const li = document.createElement("li");
    li.dataset.id = j.id;
    li.innerHTML = `<span class="jp-title">${escHtml(j.name || j.mode || "?")}</span>
      <span class="jp-meta"><span>${fmtTs(j.ts)}</span><span>${escHtml(j.mode || "")}</span><span class="jp-badge" data-badge></span></span>
      ${j.out ? `<span class="jp-out">${escHtml(j.out)}</span>` : ""}`;
    li.addEventListener("click", () => openJob(j.id, li));
    $("job-list").appendChild(li);
  });
  highlightActiveJob();
}

function highlightActiveJob() {
  [...$("job-list").children].forEach((li) =>
    li.classList.toggle("active", li.dataset.id === currentJobId));
}

async function openJob(id, li) {
  if (li && li.classList.contains("expired")) return;
  // 진행 중인 잡을 두고 다른 잡으로 이동하면 배너로 계속 지켜본다
  if (pollTimer && currentJobId && currentJobId !== id) watchBgJob(currentJobId);
  if (bgJobId === id) clearBgWatch();
  stopPolling();
  try {
    const res = await fetch(`/api/jobs/${id}`);
    if (res.status === 404) {
      // 서버에서 정리됨(24시간/개수 한도 또는 재시작) — 목록에 만료로 표시
      if (li) {
        li.classList.add("expired");
        const b = li.querySelector("[data-badge]");
        b.textContent = "만료";
        b.classList.add("expired-txt");
      }
      return;
    }
    let job;
    try { job = await res.json(); }
    catch { showError("서버 응답이 올바르지 않습니다 — 서버가 복구 중일 수 있으니 잠시 후 다시 시도해주세요"); return; }
    [$("form-section"), $("error-section"), $("result-section"), $("progress-section")].forEach(hide);
    currentJobId = id;
    if (job.status === "running" || job.status === "queued") {
      show($("progress-section"));
      startPolling(id);
    } else if (job.status === "error") {
      showError(job.error || "알 수 없는 오류");
    } else {
      renderResults(id, job);
    }
    highlightActiveJob();
  } catch (err) {
    showError(err.message);
  }
}

// 초기 렌더 + 새로고침 복귀 — ?job=<id> 딥링크 우선, 아니면 진행 중인 최근 잡 복귀
renderJobPanel();
(async () => {
  const qJob = new URLSearchParams(location.search).get("job");
  if (qJob) { openJob(qJob); return; }
  const last = recentJobs()[0];
  if (!last) return;
  try {
    const res = await fetch(`/api/jobs/${last.id}`);
    if (!res.ok) return;
    const job = await res.json();
    if (job.status === "running" || job.status === "queued") openJob(last.id);
  } catch { /* 서버 정리됨 — 조용히 무시 */ }
})();

// ---------- 클린 vs 풀 비교 재생 ----------
function renderCompare(jobId, o) {
  const el = $("compare");
  el.innerHTML = "";
  const full = o.shorts || [];
  const clean = o.shorts_clean || [];
  const n = Math.min(full.length, clean.length);
  if (!n) return;
  let html = `<details class="adv cmp-adv"><summary>효과 비교 <small>왼쪽 풀 효과 · 오른쪽 클린 — 동시 재생</small></summary>`;
  for (let i = 0; i < n; i++) {
    html += `<div class="cmp-row" data-i="${i}">
      <button type="button" class="cta-sm cmp-play" data-i="${i}">숏츠 ${i + 1} 동시 재생 ▶</button>
      <div class="cmp-videos">
        <video src="${fileUrl(jobId, full[i])}" preload="metadata" muted></video>
        <video src="${fileUrl(jobId, clean[i])}" preload="metadata" muted></video>
      </div>
    </div>`;
  }
  html += `</details>`;
  el.innerHTML = html;
  el.querySelectorAll(".cmp-play").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".cmp-row");
      row.querySelectorAll("video").forEach((v) => { v.currentTime = 0; v.play(); });
    });
  });
}

// ---------- 통합 편집기 — 전체 설정(스타일·효과·음악) + 타임라인(구간·글자·효과음) ----------
let edData = null;     // GET /analysis 원본
let edJobId = null;
let edSegs = [];       // 편집 상태 [{use,start,end,caption,hook,sfx}]
let edSel = -1;        // 선택된 구간 인덱스
let musicLib = null;   // GET /api/music (1회 로드)
let sfxLib = null;     // GET /api/sfx (없으면 [] — 효과음 트랙 숨김)
let bgmChoice = "auto";
let styleChoice = "fade";

const secToMmss = (s) => {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};

// 공유 미리듣기 — 한 번에 하나만 재생
const previewAudio = new Audio();
let previewKey = null;
function stopPreview() {
  previewAudio.pause();
  previewKey = null;
  document.querySelectorAll(".play-btn.playing").forEach((b) => b.classList.remove("playing"));
}
function togglePreview(url, key, btn) {
  if (previewKey === key) { stopPreview(); return; }
  stopPreview();
  previewAudio.src = url;
  previewAudio.play().catch(() => {});
  previewKey = key;
  if (btn) btn.classList.add("playing");
}
previewAudio.addEventListener("ended", stopPreview);

// ----- 자막 스타일 선택 + 데모 재생 -----
const DEMO_STYLES = ["fade", "kinetic", "impact", "pil"];
function syncStyleButtons() {
  document.querySelectorAll("#style-picks button").forEach((b) =>
    b.classList.toggle("selected", b.dataset.style === styleChoice));
}
$("style-picks").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-style]");
  if (!btn) return;
  styleChoice = btn.dataset.style;
  syncStyleButtons();
  const demo = $("style-demo");
  if (DEMO_STYLES.includes(styleChoice)) {
    demo.src = `/demos/${styleChoice}.mp4`;
    demo.classList.remove("hidden");
    demo.play().catch(() => {});
  } else {
    demo.pause();
    demo.classList.add("hidden");
  }
});

// 영상 자막 크기 (작게/보통/크게)
let subScale = 1;
function syncSubScaleButtons() {
  document.querySelectorAll("#sub-scale-picks button").forEach((b) =>
    b.classList.toggle("selected", Number(b.dataset.scale) === subScale));
}
$("sub-scale-picks").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-scale]");
  if (!btn) return;
  subScale = Number(btn.dataset.scale);
  syncSubScaleButtons();
});

// ----- 썸네일 타이틀 컨트롤 — 업로드 폼·편집기 공용 -----
// 미리보기는 CSS 근사가 아니라 서버 렌더(overlay_hook_text) — 산출물과 동일 픽셀
const TC_POSITIONS = [
  "top-left", "top-center", "top-right",
  "middle-left", "middle-center", "middle-right",
  "bottom-left", "bottom-center", "bottom-right",
];
const DEFAULT_TC_TEXT = "오늘의 하이라이트\n지금 공개합니다";
// 폰트 키 → 프리뷰 칩용 CSS 패밀리 (동봉 @font-face와 동일 파일)
const TC_FONT_CSS = {
  pretendard: "'Pretendard'", blackhan: "'BlackHanSansW'", dohyeon: "'DoHyeonW'",
  jua: "'JuaW'", nanumpen: "'NanumPenW'",
};
// 템플릿 목록 — 1회 로드해 폼·편집기 공용
let tplListPromise = null;
function loadThumbTemplates() {
  if (!tplListPromise) {
    tplListPromise = fetch("/api/thumb-templates")
      .then((r) => (r.ok ? r.json() : []))
      .catch(() => []);
  }
  return tplListPromise;
}
const TC_TEMPLATE = `
  <div class="tc-wrap">
    <div class="tc-rows">
      <textarea class="tc-text thumb-input" rows="2" placeholder="비우면 자동 (훅 문구) — 엔터로 줄바꿈">${DEFAULT_TC_TEXT}</textarea>
      <div class="tc-row">
        <span class="tc-lab">템플릿</span>
        <div class="chips tc-scroll tc-templates">
          <button type="button" data-tpl="custom" class="selected">직접 조합</button>
        </div>
      </div>
      <div class="tc-row tc-manual">
        <span class="tc-lab">폰트</span>
        <div class="font-picks tc-scroll tc-fonts">
          <button type="button" data-font="pretendard" class="selected" style="font-family:'Pretendard';font-weight:800">프리텐다드</button>
          <button type="button" data-font="blackhan" style="font-family:'BlackHanSansW'">블랙한산스</button>
          <button type="button" data-font="dohyeon" style="font-family:'DoHyeonW'">도현</button>
          <button type="button" data-font="jua" style="font-family:'JuaW'">주아</button>
          <button type="button" data-font="nanumpen" style="font-family:'NanumPenW'">나눔손글씨</button>
        </div>
      </div>
      <div class="tc-row tc-manual">
        <span class="tc-lab">굵기</span>
        <div class="chips tc-scroll tc-weights">
          <button type="button" data-weight="normal">보통</button>
          <button type="button" data-weight="bold" class="selected">굵게</button>
          <button type="button" data-weight="heavy">아주 굵게</button>
        </div>
        <span class="tc-lab">효과</span>
        <div class="chips tc-scroll tc-effects">
          <button type="button" data-effect="none" class="selected">없음</button>
          <button type="button" data-effect="fireworks">폭죽</button>
          <button type="button" data-effect="fire">불꽃</button>
          <button type="button" data-effect="sparkle">반짝이</button>
        </div>
      </div>
      <div class="tc-row">
        <span class="tc-lab">크기</span>
        <input type="range" class="tc-scale" min="50" max="200" step="5" value="150">
        <span class="scale-val tc-scale-val">150%</span>
      </div>
      <div class="tc-row">
        <span class="tc-lab">위치</span>
        <div class="pos-grid tc-pos">
          ${TC_POSITIONS.map((p) =>
            `<button type="button" data-pos="${p}"${p === "top-center" ? ' class="selected"' : ""}></button>`).join("")}
        </div>
        <button type="button" class="pos-off tc-off">글자 없음</button>
      </div>
    </div>
    <div class="thumb-preview hidden tc-preview">
      <img class="tc-img" alt="썸네일 미리보기">
      <span class="tc-loading hidden">준비 중…</span>
      <p class="sd-note tc-note"></p>
    </div>
  </div>`;

function createThumbControls(rootId, getBase, getAutoText) {
  const root = $(rootId);
  root.innerHTML = TC_TEMPLATE;
  const q = (sel) => root.querySelector(sel);
  // 기본값: 2줄 문구·크기 150%·상단 중앙 (지우면 자동 훅 문구)
  const state = { text: DEFAULT_TC_TEXT, font: "pretendard", scale: 1.5, weight: "bold", effect: "none", pos: "top-center", template: "custom" };
  let timer = null;
  let lastUrl = null;

  // 템플릿 칩 — 서버 목록(폰트·대표색 힌트)으로 렌더. 메타는 선택 시 번들 표시용
  const tplMeta = {};
  loadThumbTemplates().then((tpls) => {
    const wrap = q(".tc-templates");
    tpls.forEach((t) => {
      tplMeta[t.key] = t;
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.tpl = t.key;
      b.textContent = t.label;
      b.style.fontFamily = TC_FONT_CSS[t.font] || TC_FONT_CSS.pretendard;
      if (t.bg) { b.style.background = t.bg; b.style.color = t.color; }
      else b.style.color = t.color;
      wrap.appendChild(b);
    });
  });

  async function renderPreview() {
    const wrap = q(".tc-preview");
    const note = q(".tc-note");
    const img = q(".tc-img");
    const loading = q(".tc-loading");
    // 로딩은 이미지 위 배지 + 살짝 디밍 — 아래에 줄이 생겨 높이가 출렁이지 않게
    loading.classList.remove("hidden");
    img.classList.add("loading");
    const done = () => { loading.classList.add("hidden"); img.classList.remove("loading"); };
    const base = await getBase();
    if (!base) { done(); wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    const fd = new FormData();
    fd.append("text", state.pos === "off" ? "" : (state.text.trim() || (getAutoText ? getAutoText() : "")));
    fd.append("pos", state.pos === "off" ? "bottom-center" : state.pos);
    fd.append("font", state.font);
    fd.append("scale", state.scale);
    fd.append("weight", state.weight);
    fd.append("effect", state.effect);
    fd.append("template", state.template);
    if (base.blob) fd.append("frame", base.blob, "frame.jpg");
    else if (base.jobId) { fd.append("job_id", base.jobId); fd.append("t", base.t); }
    try {
      const res = await fetch("/api/thumb-preview", { method: "POST", body: fd });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = url;
      img.onload = () => {
        // 실제 프레임 비율로 고정 — 이후 갱신에서 높이가 변하지 않는다
        wrap.style.setProperty("--tc-ar", `${img.naturalWidth} / ${img.naturalHeight}`);
        done();
      };
      img.src = url;
      note.textContent = base.note || "";
    } catch (err) {
      done();
      note.textContent = `미리보기 실패 (${err.message}) — 산출엔 영향 없음`;
    }
  }
  function refresh(now = false) {
    clearTimeout(timer);
    timer = setTimeout(renderPreview, now ? 0 : 400);
  }
  const syncSel = (sel, attr, val) =>
    root.querySelectorAll(`${sel} button`).forEach((b) => b.classList.toggle("selected", b.dataset[attr] === val));

  // 템플릿 ↔ 직접 조합 — 템플릿 중엔 폰트·굵기·효과가 번들에 덮이므로 흐리게
  const syncTemplate = () => {
    syncSel(".tc-templates", "tpl", state.template);
    root.querySelectorAll(".tc-manual").forEach((el) =>
      el.classList.toggle("dimmed", state.template !== "custom"));
  };
  const toCustom = () => {
    if (state.template === "custom") return;
    state.template = "custom";
    syncTemplate();
  };
  q(".tc-templates").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tpl]");
    if (!b) return;
    state.template = b.dataset.tpl;
    // 번들 폰트·굵기·효과를 상태와 칩에 반영 — 뭘로 바뀌는지 보이고,
    // "직접 조합"으로 돌아가도 이 조합에서 이어서 만질 수 있다
    const meta = tplMeta[state.template];
    if (meta) {
      Object.assign(state, { font: meta.font, weight: meta.weight, effect: meta.effect });
      syncSel(".tc-fonts", "font", state.font);
      syncSel(".tc-weights", "weight", state.weight);
      syncSel(".tc-effects", "effect", state.effect);
    }
    syncTemplate();
    refresh(true);
  });

  q(".tc-text").addEventListener("input", () => { state.text = q(".tc-text").value; refresh(); });
  q(".tc-fonts").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-font]");
    if (!b) return;
    toCustom();
    state.font = b.dataset.font;
    syncSel(".tc-fonts", "font", state.font);
    refresh(true);
  });
  q(".tc-scale").addEventListener("input", () => {
    state.scale = Number(q(".tc-scale").value) / 100;
    q(".tc-scale-val").textContent = q(".tc-scale").value + "%";
    refresh();
  });
  q(".tc-weights").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-weight]");
    if (!b) return;
    toCustom();
    state.weight = b.dataset.weight;
    syncSel(".tc-weights", "weight", state.weight);
    refresh(true);
  });
  q(".tc-effects").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-effect]");
    if (!b) return;
    toCustom();
    state.effect = b.dataset.effect;
    syncSel(".tc-effects", "effect", state.effect);
    refresh(true);
  });
  q(".tc-pos").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-pos]");
    if (!b) return;
    state.pos = b.dataset.pos;
    syncSel(".tc-pos", "pos", state.pos);
    q(".tc-off").classList.remove("selected");
    refresh(true);
  });
  q(".tc-off").addEventListener("click", () => {
    state.pos = state.pos === "off" ? "bottom-center" : "off";
    syncSel(".tc-pos", "pos", state.pos);
    q(".tc-off").classList.toggle("selected", state.pos === "off");
    refresh(true);
  });

  return {
    state,
    refresh,
    reset() {
      Object.assign(state, { text: DEFAULT_TC_TEXT, font: "pretendard", scale: 1.5, weight: "bold", effect: "none", pos: "top-center", template: "custom" });
      q(".tc-text").value = DEFAULT_TC_TEXT;
      q(".tc-scale").value = 150;
      q(".tc-scale-val").textContent = "150%";
      syncTemplate();
      syncSel(".tc-fonts", "font", "pretendard");
      syncSel(".tc-weights", "weight", "bold");
      syncSel(".tc-effects", "effect", "none");
      syncSel(".tc-pos", "pos", "top-center");
      q(".tc-off").classList.remove("selected");
    },
    appendTo(fd) {
      fd.append("thumb_text", state.text.trim());
      fd.append("thumb_pos", state.pos);
      fd.append("thumb_font", state.font);
      fd.append("thumb_scale", state.scale);
      fd.append("thumb_weight", state.weight);
      fd.append("thumb_effect", state.effect);
      fd.append("thumb_template", state.template);
    },
  };
}

function autoHookText() {
  // 백엔드 pick_thumbnail_hook 근사 — 최고점 hook > 첫 hook > 첫 캡션
  const segs = (edData && edData.segments) || [];
  const scored = segs.filter((s) => s.score != null && s.hook);
  if (scored.length) return scored.reduce((a, b) => (Number(a.score) >= Number(b.score) ? a : b)).hook;
  const hooked = segs.find((s) => s.hook);
  if (hooked) return hooked.hook;
  const cap = ((edData && edData.captions) || []).find((c) => c && c.trim());
  return (cap || "").trim();
}

// 업로드 폼 인스턴스 — 선택한 첫 영상의 프레임을 브라우저에서 뽑아 바탕으로 (영상 만들기 전 미리보기)
let formFrameBlob = null;
let formFrameFile = null;
function captureFrameFromFile(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.autoplay = true;
    let settled = false;
    let timer = null;
    const finish = (blob) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      v.pause();
      v.removeAttribute("src");
      URL.revokeObjectURL(url);
      resolve(blob);
    };
    const draw = () => {
      if (settled || !v.videoWidth) return;
      try {
        const c = document.createElement("canvas");
        c.width = 540;
        c.height = Math.max(2, Math.round(v.videoHeight * 540 / v.videoWidth));
        c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
        c.toBlob(finish, "image/jpeg", 0.85);
      } catch { finish(null); }
    };
    // preload만으로는 프레임이 준비되지 않는다(특히 iOS) — 음소거 재생으로 킥,
    // 첫 프레임 콜백(rVFC)에서 캡처. 미지원 브라우저는 재생 이벤트로 폴백.
    if ("requestVideoFrameCallback" in v) {
      v.requestVideoFrameCallback(() => draw());
    } else {
      v.addEventListener("playing", () => setTimeout(draw, 60), { once: true });
      v.addEventListener("canplay", () => setTimeout(draw, 60), { once: true });
    }
    v.onerror = () => finish(null);
    timer = setTimeout(() => finish(null), 8000);
    v.src = url;
    v.load();
    const p = v.play();
    if (p) p.catch(() => { /* 자동재생 거부 — 타임아웃이 처리 */ });
  });
}
const formTC = createThumbControls("tc-form", async () => {
  const first = pickedFiles.find((f) => !AUDIO_RE.test(f.name) && !f.type.startsWith("audio/"));
  if (!first) return null;
  if (formFrameFile !== first) {
    formFrameBlob = await captureFrameFromFile(first);
    formFrameFile = first;
  }
  // 프레임 캡처가 안 되는 브라우저·코덱이면 임시 배경으로라도 문구·효과를 보여준다
  return formFrameBlob
    ? { blob: formFrameBlob }
    : { note: "영상 프레임을 읽지 못해 임시 배경입니다 — 산출물엔 실제 장면이 들어갑니다" };
});

// 편집기 인스턴스 — 잡의 첫 구간 프레임 위에 (자동 훅 문구 폴백)
const editorTC = createThumbControls("tc-editor", async () => {
  if (!edJobId || !edSegs.length) return null;
  const mid = ((Number(edSegs[0].start) + Number(edSegs[0].end)) / 2).toFixed(1);
  return { jobId: edJobId, t: mid };
}, autoHookText);

// ----- 편집기 초기화 (결과 렌더 시) -----
async function initEditor(jobId, job) {
  edJobId = jobId;
  edData = null;
  edSel = -1;
  edSegs = [];
  $("edit-status").textContent = "";
  $("seg-detail").classList.add("hidden");
  $("tl-tracks").innerHTML = `<p class="jp-empty">불러오는 중…</p>`;
  const demo = $("style-demo");
  demo.pause();
  demo.classList.add("hidden");

  // 직전 옵션을 편집기 기본값으로
  $("ed_shorts").value = $("job-form").shorts_count.value;
  $("ed_thumb").value = $("job-form").thumbnail_count.value;
  $("ed_blur").checked = $("shorts_blur").checked;
  $("ed_jumpcut").checked = $("shorts_jumpcut").checked;
  $("ed_punchin").checked = $("shorts_punchin").checked;
  $("ed_clean").checked = $("shorts_clean").checked;
  $("ed_bgmvol").value = $("bgm_vol").value;
  styleChoice = $("sub_mode").value;
  syncStyleButtons();

  try {
    const reqs = [fetch(`/api/jobs/${jobId}/analysis`)];
    reqs.push(musicLib ? null : fetch("/api/music"));
    reqs.push(sfxLib ? null : fetch("/api/sfx"));
    const [aRes, mRes, sRes] = await Promise.all(reqs);
    if (!aRes.ok) throw new Error("분석 데이터가 없습니다 (정리됐을 수 있음)");
    edData = await aRes.json();
    if (mRes) musicLib = mRes.ok ? await mRes.json() : { moods: {} };
    if (sRes) sfxLib = sRes.ok ? (await sRes.json()).sfx || [] : [];
  } catch (err) {
    $("tl-tracks").innerHTML = `<p class="jp-empty">${escHtml(err.message)}</p>`;
    return;
  }

  edSegs = (edData.segments || []).map((s, i) => ({
    use: true,
    start: s.start,
    end: s.end,
    clipStart: s.clip_start,   // 원본 클립 경계 — 트림 바 (몽타주 트림 시 존재)
    clipEnd: s.clip_end,
    caption: (edData.captions || [])[i] || "",
    hook: s.hook || "",
    sfx: s.sfx || "",
    broll: s.broll || "",      // B컷 이미지 (서버 발급 이름) — 구간 동안 화면을 덮는 컷어웨이
    tpl: s,                    // 재생성 시 원본 segment 필드 계승용 (구간 추가·삭제에도 안전)
  }));
  renderBgmList(job);
  renderTimeline();
  renderTranscriptEdit();
  activateTool("clip");
  initPlayer(jobId, job);

  // 썸네일 타이틀 — 원본 프레임 위 서버 렌더 미리보기 (편집기 인스턴스)
  subScale = 1;
  syncSubScaleButtons();
  editorTC.reset();
  editorTC.refresh(true);
}

// ----- 도구바 — 하단 탭으로 패널 전환 -----
function activateTool(tool) {
  document.querySelectorAll("#toolbar button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tool === tool));
  document.querySelectorAll(".tool-panel").forEach((p) =>
    p.classList.toggle("hidden", p.dataset.panel !== tool));
  if (tool !== "subtitle") $("style-demo").pause();
}
document.querySelectorAll("#toolbar button").forEach((b) =>
  b.addEventListener("click", () => activateTool(b.dataset.tool)));

// ----- 플레이어 — 결과물 재생 + 타임라인 플레이헤드 동기화 -----
// 매핑 가능(몽타주 숏폼·롱폼)한 출력에서만 플레이헤드를 움직인다.
const edPlayer = $("ed-player");
let outVideos = [];   // [{name, label, mappable}]
let outMappable = false;
let phRaf = null;

function isMontageData() {
  const segs = (edData && edData.segments) || [];
  return !!segs.length && String(segs[0].reason || "").startsWith("montage");
}

function initPlayer(jobId, job) {
  const o = job.outputs || {};
  const montage = isMontageData();
  outVideos = [];
  if (o.subtitled) outVideos.push({ name: o.subtitled, label: "자막본", mappable: false });
  if (o.longform) outVideos.push({ name: o.longform, label: "롱폼", mappable: true });
  (o.shorts || []).forEach((n, i) =>
    outVideos.push({ name: n, label: `숏츠 ${i + 1}`, mappable: montage && i === 0 }));
  if (o.intro) outVideos.push({ name: o.intro, label: "인트로", mappable: false });

  const picker = $("out-picker");
  picker.innerHTML = outVideos.length > 1
    ? outVideos.map((v, i) => `<button type="button" data-i="${i}">${escHtml(v.label)}</button>`).join("")
    : "";
  const first = Math.max(0, outVideos.findIndex((v) => v.mappable));
  if (outVideos.length) selectOut(first);
  else { edPlayer.removeAttribute("src"); $("tp-time").textContent = "0:00.0 / 0:00.0"; }
}

function selectOut(i) {
  const v = outVideos[i];
  if (!v) return;
  edPlayer.pause();
  edPlayer.src = fileUrl(edJobId, v.name);
  outMappable = v.mappable;
  $("tl-playhead").classList.toggle("hidden", !outMappable);
  document.querySelectorAll("#out-picker button").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.i) === i));
}
$("out-picker").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (b) selectOut(Number(b.dataset.i));
});

const fmtTc = (s) => {
  if (!isFinite(s)) s = 0;
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}.${Math.floor((s % 1) * 10)}`;
};

// 편집 상태의 출력 타임라인 — 사용 구간 누적 (몽타주=정확, 롱폼=xfade 근사)
function outTimelineMap() {
  let t = 0;
  return edSegs.map((s) => {
    const d = s.use ? Math.max(0, s.end - s.start) : 0;
    const m = { t0: t, d };
    t += d;
    return m;
  });
}

function updatePlayhead() {
  if (!outMappable || edSel === -2) return;
  const map = outTimelineMap();
  const cells = document.querySelectorAll(".tl-vcell");
  const t = edPlayer.currentTime;
  let x = null;
  for (let i = 0; i < map.length; i++) {
    if (!map[i].d || !cells[i]) continue;
    if (t < map[i].t0 + map[i].d) {
      const f = Math.min(1, Math.max(0, (t - map[i].t0) / map[i].d));
      x = cells[i].offsetLeft + f * cells[i].offsetWidth;
      break;
    }
    x = cells[i].offsetLeft + cells[i].offsetWidth;  // 마지막 사용 클립 끝
  }
  if (x != null) $("tl-playhead").style.left = `${Math.round(x)}px`;
}

function syncTransport() {
  $("tp-time").textContent = `${fmtTc(edPlayer.currentTime)} / ${fmtTc(edPlayer.duration)}`;
  const playing = !edPlayer.paused && !edPlayer.ended;
  $("tp-play").textContent = playing ? "⏸" : "⏵";
  $("player-rest").style.opacity = playing ? 0 : 1;
}
function phLoop() {
  updatePlayhead();
  syncTransport();
  if (!edPlayer.paused && !edPlayer.ended) phRaf = requestAnimationFrame(phLoop);
}
["play", "pause", "ended", "loadedmetadata", "timeupdate", "seeked"].forEach((ev) =>
  edPlayer.addEventListener(ev, () => {
    if (phRaf) cancelAnimationFrame(phRaf);
    phLoop();
  }));
const togglePlay = () => { if (edPlayer.src) { edPlayer.paused ? edPlayer.play() : edPlayer.pause(); } };
$("tp-play").addEventListener("click", togglePlay);
edPlayer.addEventListener("click", togglePlay);

// ----- 음악(BGM) 리스트 -----
function renderBgmList(job) {
  bgmChoice = "auto";
  const cur = job && job.bgm_track;
  const row = (val, main, sub, url) => `
    <div class="bgm-row ${val === bgmChoice ? "selected" : ""}" data-val="${escHtml(val)}">
      <span class="bgm-name">${escHtml(main)}</span>
      ${sub ? `<span class="bgm-sub">${escHtml(sub)}</span>` : ""}
      ${url ? `<button type="button" class="play-btn" data-url="${url}">▶</button>` : ""}
    </div>`;
  let html = row("auto", "자동 선곡", "영상 무드 기반" + (cur ? ` · 현재 ${cur}` : ""), null);
  html += row("off", "끄기", "BGM 없이", null);
  const moods = (musicLib && musicLib.moods) || {};
  for (const mood of Object.keys(moods)) {
    for (const t of moods[mood]) {
      const sub = [mood, t.bpm ? `${t.bpm}bpm` : "", t.name === cur ? "현재 적용" : ""]
        .filter(Boolean).join(" · ");
      html += row(
        `${mood}/${t.name}`,
        t.name.replace(/\.mp3$/i, "").replace(/_/g, " "),
        sub,
        `/api/music/${encodeURIComponent(mood)}/${encodeURIComponent(t.name)}`,
      );
    }
  }
  $("bgm-list").innerHTML = html;
}
$("bgm-list").addEventListener("click", (e) => {
  const play = e.target.closest(".play-btn");
  if (play) { togglePreview(play.dataset.url, play.dataset.url, play); return; }
  const rowEl = e.target.closest(".bgm-row");
  if (!rowEl) return;
  bgmChoice = rowEl.dataset.val;
  document.querySelectorAll(".bgm-row").forEach((r) => r.classList.toggle("selected", r === rowEl));
});

// ----- 타임라인 (영상·글자·효과음 트랙) -----
const PX_PER_SEC = 16;
function sfxLabel(name) {
  const item = (sfxLib || []).find((x) => x.name === name);
  return item ? item.label || item.name : name;
}
function renderTimeline() {
  const showSfx = !!(sfxLib && sfxLib.length);
  let vid = "", txt = "", sfx = "";
  edSegs.forEach((s, i) => {
    const w = Math.max(72, Math.round((Number(s.end) - Number(s.start)) * PX_PER_SEC));
    const mid = ((Number(s.start) + Number(s.end)) / 2).toFixed(1);
    const cls = `tl-cell${i === edSel ? " selected" : ""}${s.use ? "" : " excluded"}`;
    vid += `<div class="${cls} tl-vcell" data-i="${i}" style="width:${w}px;background-image:url('/api/jobs/${edJobId}/frame?t=${mid}')">${s.broll ? '<span class="tl-broll">B</span>' : ""}<span class="tl-dur">${(s.end - s.start).toFixed(1)}s</span></div>`;
    txt += `<div class="${cls} tl-tcell" data-i="${i}" style="width:${w}px">${escHtml((s.caption || "").split("\n")[0] || "–")}</div>`;
    if (showSfx) sfx += `<div class="${cls} tl-scell${s.sfx ? " has-sfx" : ""}" data-i="${i}" style="width:${w}px">${escHtml(s.sfx ? sfxLabel(s.sfx) : "＋")}</div>`;
  });
  $("tl-tracks").innerHTML =
    `<div class="tl-row">${vid}</div><div class="tl-row">${txt}</div>` +
    (showSfx ? `<div class="tl-row">${sfx}</div>` : "");
}
$("tl-tracks").addEventListener("click", (e) => {
  const cell = e.target.closest(".tl-cell");
  if (!cell) return;
  edSel = Number(cell.dataset.i);
  renderTimeline();
  renderSegDetail();
  activateTool("clip");
  // 매핑 가능한 출력이면 그 클립의 시작으로 시크 — 탭이 곧 미리보기
  const s = edSegs[edSel];
  if (outMappable && s && s.use && edPlayer.src) {
    edPlayer.currentTime = outTimelineMap()[edSel].t0 + 0.01;
  }
});

// ----- 선택 구간 상세 (자막 오버레이 근사 미리보기 포함) -----
function updateOverlay() {
  const ov = $("sd-overlay");
  const img = $("sd-frame");
  if (!ov || !img || edSel < 0) return;
  const text = styleChoice === "off" ? "" : (edSegs[edSel].caption || "").trim();
  ov.textContent = text;
  ov.style.display = text ? "" : "none";
  const size = () => {
    const h = img.clientHeight, w = img.clientWidth;
    if (!h || !w) return;
    const vertical = img.naturalHeight > img.naturalWidth;
    // 렌더러 비율 근사 — 숏츠(세로): 56px/1080 폭·하단 30%, 롱폼(가로): 36px/1080 높이·하단 7.4%
    ov.style.fontSize = (vertical ? w * 0.052 : h * 0.033) + "px";
    ov.style.bottom = (vertical ? h * 0.30 : h * 0.074) + "px";
  };
  if (img.complete) size(); else img.onload = size;
}

function renderSegDetail() {
  const box = $("seg-detail");
  if (edSel < 0 || !edSegs[edSel]) { box.classList.add("hidden"); return; }
  const s = edSegs[edSel];
  const mid = ((Number(s.start) + Number(s.end)) / 2).toFixed(1);
  const sfxOpts = (sfxLib || []).map((x) =>
    `<option value="${escHtml(x.name)}"${s.sfx === x.name ? " selected" : ""}>${escHtml(x.label || x.name)}</option>`).join("");
  // 트림 바 — 원본 클립 경계(clipStart/End)가 있으면 어느 구간을 쓰는지 보여주고
  // 핸들 드래그(앞뒤 트림)·이동·전체 사용·같은 클립에서 구간 추가를 지원한다
  const hasClip = s.clipStart != null && s.clipEnd != null && s.clipEnd - s.clipStart > 0.2;
  const trimHtml = hasClip ? `
    <div class="trim-line">
      <span class="trim-lab">클립 내 사용 구간 <em id="sd_trim_lab"></em></span>
      <button type="button" class="pos-off" id="sd_full">클립 전체</button>
      <button type="button" class="pos-off" id="sd_addcut">＋ 구간 추가</button>
    </div>
    <div class="trim-bar" id="sd_trim">
      <div class="trim-ghosts" id="sd_trim_ghosts"></div>
      <div class="trim-win" id="sd_trim_win">
        <span class="trim-h" data-h="l"></span><span class="trim-h" data-h="r"></span>
      </div>
    </div>` : "";
  box.innerHTML = `
    <div class="sd-head">
      <span>구간 ${edSel + 1} · ${secToMmss(s.start)}~${secToMmss(s.end)}</span>
      <label class="toggle"><input type="checkbox" id="sd_use"${s.use ? " checked" : ""}><span class="tg-box"></span>사용</label>
    </div>
    <div class="sd-grid">
      <div class="sd-preview">
        <img src="/api/jobs/${edJobId}/frame?t=${mid}" alt="구간 ${edSel + 1}" id="sd-frame">
        <div class="sub-overlay" id="sd-overlay"></div>
      </div>
      <div class="sd-fields">
        ${trimHtml}
        <span class="sd-time">
          <input type="number" id="sd_start" value="${s.start}" step="0.1" min="0"> ~
          <input type="number" id="sd_end" value="${s.end}" step="0.1" min="0"> 초
        </span>
        <textarea id="sd_caption" rows="2" placeholder="자막 — 엔터로 줄을 나누면 그대로 반영">${escHtml(s.caption)}</textarea>
        <input type="text" id="sd_hook" value="${escHtml(s.hook)}" placeholder="훅 배너 문구">
        ${sfxLib && sfxLib.length ? `<label class="sd-sfx">효과음
          <select id="sd_sfx"><option value="">없음</option>${sfxOpts}</select>
          <button type="button" class="play-btn" id="sd_sfx_play">▶</button></label>` : ""}
        <div class="sd-broll">
          <span class="trim-lab">B컷</span>
          <input type="file" id="sd_broll_file" accept="image/png,image/jpeg,image/webp" hidden>
          ${s.broll
            ? `<img class="sd-broll-thumb" src="/api/jobs/${edJobId}/broll/${encodeURIComponent(s.broll)}" alt="B컷">
               <button type="button" class="pos-off" id="sd_broll_btn">교체</button>
               <button type="button" class="pos-off" id="sd_broll_rm">제거</button>`
            : `<button type="button" class="pos-off" id="sd_broll_btn">＋ 이미지 추가</button>
               <span class="sd-note" style="margin:0">이 구간 화면을 덮는 컷어웨이 (오디오 유지 · 켄번즈)</span>`}
        </div>
      </div>
    </div>
    <p class="sd-note">미리보기는 줄바꿈·크기 확인용 근사치 — 움직임은 자막 탭의 스타일 데모 참고</p>`;
  updateOverlay();
  if (hasClip) initTrimBar(s);

  $("sd_use").onchange = () => { s.use = $("sd_use").checked; renderTimeline(); };
  $("sd_start").oninput = () => { s.start = parseFloat($("sd_start").value) || 0; syncTrimBar(s); };
  $("sd_end").oninput = () => { s.end = parseFloat($("sd_end").value) || 0; syncTrimBar(s); };
  $("sd_caption").oninput = () => {
    s.caption = $("sd_caption").value;
    updateOverlay();
    const cell = document.querySelector(`.tl-tcell[data-i="${edSel}"]`);
    if (cell) cell.textContent = (s.caption || "").split("\n")[0] || "–";
  };
  $("sd_hook").oninput = () => { s.hook = $("sd_hook").value; };
  const sfxSel = $("sd_sfx");
  if (sfxSel) {
    sfxSel.onchange = () => { s.sfx = sfxSel.value; renderTimeline(); };
    $("sd_sfx_play").onclick = () => {
      if (s.sfx) togglePreview(`/api/sfx/${encodeURIComponent(s.sfx)}`, `sfx:${s.sfx}`, $("sd_sfx_play"));
    };
  }

  // B컷 — 업로드 즉시 서버에 올리고 이름만 상태에 (적용 시 재생성에 합성)
  $("sd_broll_btn").onclick = () => $("sd_broll_file").click();
  $("sd_broll_file").onchange = async () => {
    const f = $("sd_broll_file").files[0];
    if (!f) return;
    $("edit-status").textContent = "B컷 업로드 중…";
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch(`/api/jobs/${edJobId}/broll`, { method: "POST", body: fd });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      s.broll = (await res.json()).name;
      $("edit-status").textContent = "";
      renderTimeline();
      renderSegDetail();
    } catch (err) {
      $("edit-status").textContent = `B컷 업로드 실패: ${err.message}`;
    }
  };
  const brollRm = $("sd_broll_rm");
  if (brollRm) brollRm.onclick = () => { s.broll = ""; renderTimeline(); renderSegDetail(); };

  box.classList.remove("hidden");
}

// ----- 트림 바 — 원본 클립 안에서 사용 구간 확인·이동·앞뒤 트림·구간 추가 -----
const TRIM_MIN = 0.4;  // 최소 구간 길이(초)

function syncTrimBar(s) {
  if (!$("sd_trim") || s.clipStart == null) return;
  const span = s.clipEnd - s.clipStart;
  const l = Math.max(0, (s.start - s.clipStart) / span) * 100;
  const r = Math.min(100, ((s.end - s.clipStart) / span) * 100);
  const win = $("sd_trim_win");
  win.style.left = `${l}%`;
  win.style.width = `${Math.max(2, r - l)}%`;
  $("sd_trim_lab").textContent = `${(s.end - s.start).toFixed(1)}s / 클립 ${span.toFixed(1)}s`;
  // 같은 클립에서 나온 다른 구간 — 고스트로 함께 보여 겹침을 피할 수 있게
  $("sd_trim_ghosts").innerHTML = edSegs
    .filter((o, j) => j !== edSel && o.use && o.clipStart === s.clipStart)
    .map((o) => {
      const gl = Math.max(0, (o.start - s.clipStart) / span) * 100;
      const gr = Math.min(100, ((o.end - s.clipStart) / span) * 100);
      return `<div class="trim-ghost" style="left:${gl}%;width:${Math.max(1.5, gr - gl)}%"></div>`;
    }).join("");
}

function afterTrimChange(s, final) {
  syncTrimBar(s);
  if (!final) return;
  s.start = Math.round(s.start * 10) / 10;
  s.end = Math.round(s.end * 10) / 10;
  $("sd_start").value = s.start;
  $("sd_end").value = s.end;
  $("sd-frame").src = `/api/jobs/${edJobId}/frame?t=${((s.start + s.end) / 2).toFixed(1)}`;
  renderTimeline();  // 셀 폭·길이 라벨 갱신 (선택 유지)
}

function initTrimBar(s) {
  syncTrimBar(s);
  const bar = $("sd_trim");
  const span = s.clipEnd - s.clipStart;
  let drag = null;  // {mode: 'l'|'r'|'move', startX, s0, e0}

  bar.addEventListener("pointerdown", (e) => {
    const h = e.target.closest(".trim-h");
    const win = e.target.closest(".trim-win");
    if (!h && !win) {
      // 바 빈 곳 탭 — 창을 그 위치로 이동(중심 정렬) 후 바로 드래그 이어가기
      const rect = bar.getBoundingClientRect();
      const t = s.clipStart + ((e.clientX - rect.left) / rect.width) * span;
      const len = s.end - s.start;
      s.start = Math.min(Math.max(s.clipStart, t - len / 2), s.clipEnd - len);
      s.end = s.start + len;
      afterTrimChange(s, false);
    }
    drag = { mode: h ? h.dataset.h : "move", startX: e.clientX, s0: s.start, e0: s.end };
    bar.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  bar.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const d = ((e.clientX - drag.startX) / bar.clientWidth) * span;
    if (drag.mode === "l") {
      s.start = Math.min(Math.max(s.clipStart, drag.s0 + d), s.end - TRIM_MIN);
    } else if (drag.mode === "r") {
      s.end = Math.max(Math.min(s.clipEnd, drag.e0 + d), s.start + TRIM_MIN);
    } else {
      const len = drag.e0 - drag.s0;
      s.start = Math.min(Math.max(s.clipStart, drag.s0 + d), s.clipEnd - len);
      s.end = s.start + len;
    }
    afterTrimChange(s, false);
  });
  ["pointerup", "pointercancel"].forEach((ev) =>
    bar.addEventListener(ev, () => { if (drag) { drag = null; afterTrimChange(s, true); } }));

  $("sd_full").onclick = () => { s.start = s.clipStart; s.end = s.clipEnd; afterTrimChange(s, true); };
  $("sd_addcut").onclick = () => {
    // 같은 클립에서 구간 하나 더 — 현재 창 뒤(자리 없으면 앞)에 같은 길이로
    const len = Math.min(Math.max(TRIM_MIN, s.end - s.start), span);
    let ns = s.end, ne = s.end + len;
    if (ne > s.clipEnd) { ne = s.start; ns = s.start - len; }
    if (ns < s.clipStart) { ns = s.clipStart; ne = Math.min(s.clipEnd, ns + len); }
    edSegs.splice(edSel + 1, 0, {
      use: true, start: Math.round(ns * 10) / 10, end: Math.round(ne * 10) / 10,
      clipStart: s.clipStart, clipEnd: s.clipEnd,
      caption: "", hook: "", sfx: "", tpl: s.tpl,
    });
    edSel += 1;
    renderTimeline();
    renderSegDetail();
  };
}

// ----- 발화 자막 교정 (speech 모드) -----
function renderTranscriptEdit() {
  const box = $("transcript-edit");
  if (!edData.transcript || !edData.transcript.length) { box.innerHTML = ""; return; }
  let html = `<div class="ed-grid-head"><span>발화 자막 교정 <small>Whisper 오인식 수정 — 고친 문장은 카라오케 대신 통자막으로</small></span></div>`;
  edData.transcript.forEach((t) => {
    html += `<div class="ed-trow" data-i="${t.i}">
      <span class="ed-tt">${secToMmss(t.start)}</span>
      <input type="text" class="ed-ttext" value="${escHtml(t.text)}">
    </div>`;
  });
  box.innerHTML = html;
}

// ----- 적용: 교정 저장 → 재생성 -----
$("apply-btn").addEventListener("click", async () => {
  if (!edData || !edJobId) return;
  stopPreview();
  const segments = [];
  const captions = [];
  for (let i = 0; i < edSegs.length; i++) {
    const s = edSegs[i];
    if (!s.use) continue;
    if (!(s.start < s.end)) { $("edit-status").textContent = `구간 ${i + 1}: 시작이 끝보다 앞서야 합니다`; return; }
    // 원본 필드는 tpl에서 계승 — 구간 추가·순서 변화에도 인덱스에 의존하지 않는다
    const seg = { ...(s.tpl || {}), start: s.start, end: s.end };
    if (s.clipStart != null) { seg.clip_start = s.clipStart; seg.clip_end = s.clipEnd; }
    if (s.hook.trim()) seg.hook = s.hook.trim(); else delete seg.hook;
    if (s.sfx) seg.sfx = s.sfx; else delete seg.sfx;
    if (s.broll) seg.broll = s.broll; else delete seg.broll;
    segments.push(seg);
    captions.push(s.caption.trim());
  }
  if (!segments.length) { $("edit-status").textContent = "구간을 최소 1개는 남겨야 합니다"; return; }

  const transcript = [...document.querySelectorAll("#transcript-edit .ed-trow")].map((r) => ({
    i: Number(r.dataset.i), text: r.querySelector(".ed-ttext").value,
  }));

  $("edit-status").textContent = "저장 중…";
  try {
    const res = await fetch(`/api/jobs/${edJobId}/analysis`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments, captions, transcript }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    $("edit-status").textContent = "";
    doRebuild();
  } catch (err) {
    $("edit-status").textContent = err.message;
  }
});
