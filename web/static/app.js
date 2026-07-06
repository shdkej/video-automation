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

function appendSubOpts(fd, subMode) {
  const animated = subMode === "fade" || subMode === "kinetic" || subMode === "impact";
  fd.append("no_subtitle", subMode === "off");
  fd.append("sub_engine", animated ? "remotion" : "pil");
  fd.append("sub_style", subMode === "kinetic" || subMode === "impact" ? subMode : "fade");
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
dz.addEventListener("drop", (e) => {
  const ok = [...e.dataTransfer.files].filter(
    (f) => f.type.startsWith("video/") || f.type.startsWith("audio/") || MEDIA_RE.test(f.name)
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

function renderFileList() {
  const ul = $("file-list");
  ul.innerHTML = "";
  let vn = 0; // 영상만 순번, 오디오는 ♪
  pickedFiles.forEach((f, i) => {
    const isAudio = AUDIO_RE.test(f.name) || f.type.startsWith("audio/");
    const li = document.createElement("li");
    li.className = "file-row" + (isAudio ? " audio" : "");
    const idx = document.createElement("span");
    idx.className = "fr-idx" + (isAudio ? " audio" : "");
    idx.textContent = isAudio ? "♪" : String(++vn);
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
  pollTimer = setInterval(async () => {
    // 탭이 백그라운드면 5초 간격으로만 — 모바일 배터리·데이터 절약
    if (document.hidden) {
      const now = Date.now();
      if (now - lastHiddenPoll < 5000) return;
      lastHiddenPoll = now;
    }
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      const p = job.progress || 0;
      $("bar-fill").style.width = p + "%";
      $("stage-text").textContent = `${job.stage || ""} · ${p}%`;
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
    } catch (err) { stopPolling(); showError(err.message); }
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
      if (job.status === "running") {
        banner.textContent = `⏳ 다른 작업 처리 중 · ${job.progress || 0}% — 보러 가기`;
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
  initEditor(jobId, job);
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
    const job = await res.json();
    [$("form-section"), $("error-section"), $("result-section"), $("progress-section")].forEach(hide);
    currentJobId = id;
    if (job.status === "running") {
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
    if (job.status === "running") openJob(last.id);
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
  let html = `<div class="cmp-head">효과 비교 <small>왼쪽 풀 효과 · 오른쪽 클린</small></div>`;
  for (let i = 0; i < n; i++) {
    html += `<div class="cmp-row" data-i="${i}">
      <button type="button" class="cta-sm cmp-play" data-i="${i}">숏츠 ${i + 1} 동시 재생 ▶</button>
      <div class="cmp-videos">
        <video src="${fileUrl(jobId, full[i])}" preload="metadata" muted></video>
        <video src="${fileUrl(jobId, clean[i])}" preload="metadata" muted></video>
      </div>
    </div>`;
  }
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
const TC_TEMPLATE = `
  <div class="ed-row-flex">
    <div class="ed-col">
      <textarea class="tc-text thumb-input" rows="2" placeholder="비우면 자동 (훅 문구) — 엔터로 줄바꿈"></textarea>
      <div class="font-picks tc-fonts">
        <button type="button" data-font="pretendard" class="selected" style="font-family:'Pretendard';font-weight:800">프리텐다드</button>
        <button type="button" data-font="blackhan" style="font-family:'BlackHanSansW'">블랙한산스</button>
        <button type="button" data-font="dohyeon" style="font-family:'DoHyeonW'">도현</button>
        <button type="button" data-font="jua" style="font-family:'JuaW'">주아</button>
        <button type="button" data-font="nanumpen" style="font-family:'NanumPenW'">나눔손글씨</button>
      </div>
      <div class="chip-line">크기
        <input type="range" class="tc-scale" min="50" max="200" step="5" value="150">
        <span class="scale-val tc-scale-val">150%</span>
      </div>
      <div class="chip-line">굵기
        <div class="chips tc-weights">
          <button type="button" data-weight="normal">보통</button>
          <button type="button" data-weight="bold" class="selected">굵게</button>
          <button type="button" data-weight="heavy">아주 굵게</button>
        </div>
      </div>
      <div class="chip-line">효과
        <div class="chips tc-effects">
          <button type="button" data-effect="none" class="selected">없음</button>
          <button type="button" data-effect="fireworks">폭죽</button>
          <button type="button" data-effect="fire">불꽃</button>
          <button type="button" data-effect="sparkle">반짝이</button>
        </div>
      </div>
      <div class="chip-line">위치
        <div class="pos-grid tc-pos">
          ${TC_POSITIONS.map((p) =>
            `<button type="button" data-pos="${p}"${p === "top-center" ? ' class="selected"' : ""}></button>`).join("")}
        </div>
        <button type="button" class="pos-off tc-off">글자 없음</button>
      </div>
    </div>
    <div class="thumb-preview hidden tc-preview">
      <img class="tc-img" alt="썸네일 미리보기">
      <p class="sd-note tc-note"></p>
    </div>
  </div>`;

function createThumbControls(rootId, getBase, getAutoText) {
  const root = $(rootId);
  root.innerHTML = TC_TEMPLATE;
  const q = (sel) => root.querySelector(sel);
  // 기본값: 크기 150%·상단 중앙 (백엔드 DEFAULT_THUMB_*와 동일)
  const state = { text: "", font: "pretendard", scale: 1.5, weight: "bold", effect: "none", pos: "top-center" };
  let timer = null;
  let lastUrl = null;

  async function renderPreview() {
    const wrap = q(".tc-preview");
    const note = q(".tc-note");
    note.textContent = "미리보기 준비 중…";
    const base = await getBase();
    if (!base) { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    const fd = new FormData();
    fd.append("text", state.pos === "off" ? "" : (state.text.trim() || (getAutoText ? getAutoText() : "")));
    fd.append("pos", state.pos === "off" ? "bottom-center" : state.pos);
    fd.append("font", state.font);
    fd.append("scale", state.scale);
    fd.append("weight", state.weight);
    fd.append("effect", state.effect);
    if (base.blob) fd.append("frame", base.blob, "frame.jpg");
    else if (base.jobId) { fd.append("job_id", base.jobId); fd.append("t", base.t); }
    try {
      const res = await fetch("/api/thumb-preview", { method: "POST", body: fd });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = url;
      q(".tc-img").src = url;
      note.textContent = base.note || "";
    } catch (err) {
      note.textContent = `미리보기 실패 (${err.message}) — 산출엔 영향 없음`;
    }
  }
  function refresh(now = false) {
    clearTimeout(timer);
    timer = setTimeout(renderPreview, now ? 0 : 400);
  }
  const syncSel = (sel, attr, val) =>
    root.querySelectorAll(`${sel} button`).forEach((b) => b.classList.toggle("selected", b.dataset[attr] === val));

  q(".tc-text").addEventListener("input", () => { state.text = q(".tc-text").value; refresh(); });
  q(".tc-fonts").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-font]");
    if (!b) return;
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
    state.weight = b.dataset.weight;
    syncSel(".tc-weights", "weight", state.weight);
    refresh(true);
  });
  q(".tc-effects").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-effect]");
    if (!b) return;
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
      Object.assign(state, { text: "", font: "pretendard", scale: 1.5, weight: "bold", effect: "none", pos: "top-center" });
      q(".tc-text").value = "";
      q(".tc-scale").value = 150;
      q(".tc-scale-val").textContent = "150%";
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
    caption: (edData.captions || [])[i] || "",
    hook: s.hook || "",
    sfx: s.sfx || "",
  }));
  renderBgmList(job);
  renderTimeline();
  renderTranscriptEdit();

  // 썸네일 타이틀 — 원본 프레임 위 서버 렌더 미리보기 (편집기 인스턴스)
  subScale = 1;
  syncSubScaleButtons();
  editorTC.reset();
  editorTC.refresh(true);
}

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
  document.querySelector(".tl-head-sfx").style.display = showSfx ? "" : "none";
  let vid = "", txt = "", sfx = "";
  edSegs.forEach((s, i) => {
    const w = Math.max(72, Math.round((Number(s.end) - Number(s.start)) * PX_PER_SEC));
    const mid = ((Number(s.start) + Number(s.end)) / 2).toFixed(1);
    const cls = `tl-cell${i === edSel ? " selected" : ""}${s.use ? "" : " excluded"}`;
    vid += `<div class="${cls} tl-vcell" data-i="${i}" style="width:${w}px;background-image:url('/api/jobs/${edJobId}/frame?t=${mid}')"><span class="tl-dur">${(s.end - s.start).toFixed(1)}s</span></div>`;
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
        <span class="sd-time">
          <input type="number" id="sd_start" value="${s.start}" step="0.1" min="0"> ~
          <input type="number" id="sd_end" value="${s.end}" step="0.1" min="0"> 초
        </span>
        <textarea id="sd_caption" rows="2" placeholder="자막 — 엔터로 줄을 나누면 그대로 반영">${escHtml(s.caption)}</textarea>
        <input type="text" id="sd_hook" value="${escHtml(s.hook)}" placeholder="훅 배너 문구">
        ${sfxLib && sfxLib.length ? `<label class="sd-sfx">효과음
          <select id="sd_sfx"><option value="">없음</option>${sfxOpts}</select>
          <button type="button" class="play-btn" id="sd_sfx_play">▶</button></label>` : ""}
      </div>
    </div>
    <p class="sd-note">미리보기는 줄바꿈·크기 확인용 근사치 — 움직임은 위 스타일 데모 참고</p>`;
  updateOverlay();

  $("sd_use").onchange = () => { s.use = $("sd_use").checked; renderTimeline(); };
  $("sd_start").oninput = () => { s.start = parseFloat($("sd_start").value) || 0; };
  $("sd_end").oninput = () => { s.end = parseFloat($("sd_end").value) || 0; };
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
  box.classList.remove("hidden");
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
    const seg = { ...edData.segments[i], start: s.start, end: s.end };
    if (s.hook.trim()) seg.hook = s.hook.trim(); else delete seg.hook;
    if (s.sfx) seg.sfx = s.sfx; else delete seg.sfx;
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
