// REEL ROOM — 업로드 → 폴링 → 결과
const $ = (id) => document.getElementById(id);
const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

const MODE_NOTES = {
  scene: "ffmpeg 씬 감지로 컷 포인트를 찾습니다. API 키 불필요·무료. 자막은 speech 모드에서만 들어갑니다.",
  speech: "Whisper가 음성을 받아 적고 LLM이 핵심 구간을 고른 뒤 한국어 자막을 입힙니다. .env에 OPENAI_API_KEY 또는 ANTHROPIC_API_KEY 필요.",
  vision: "정적·무음 영상용. 시점별 모자이크 한 장을 비전 LLM이 분석합니다. API 키 필요.",
};

let pickedFiles = []; // 업로드 순서 = 타임라인 순서
let pollTimer = null;

// 자막 select(off/pil/fade/kinetic) → 백엔드 옵션 3종으로 분해
const KO_COUNT = ["", "한", "두", "세", "네"];
function pickedOutputs() {
  return [...document.querySelectorAll(".out-pick:checked")].map((el) => el.value);
}
function refreshCtaLabel() {
  const n = pickedOutputs().length;
  $("cta-label").textContent = n === 0 ? "산출물을 선택하세요" : `${KO_COUNT[n]} 가지 만들기`;
}
document.querySelectorAll(".out-pick").forEach((el) => el.addEventListener("change", refreshCtaLabel));

function appendSubOpts(fd, subMode) {
  const animated = subMode === "fade" || subMode === "kinetic";
  fd.append("no_subtitle", subMode === "off");
  fd.append("sub_engine", animated ? "remotion" : "pil");
  fd.append("sub_style", subMode === "kinetic" ? "kinetic" : "fade");
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
selectMode("scene");

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
}

// ---------- 제출 ----------
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
  appendSubOpts(fd, $("sub_mode").value);
  const picked = pickedOutputs();
  if (picked.length === 0) { showError("산출물을 하나 이상 선택해주세요."); return; }
  picked.forEach((o) => fd.append("outputs", o));

  $("submit-btn").disabled = true;
  try {
    const res = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const { job_id } = await res.json();
    saveRecentJob(job_id, $("mode").value);
    hide($("form-section"));
    show($("progress-section"));
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
function startPolling(jobId) {
  currentJobId = jobId;
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      const p = job.progress || 0;
      $("bar-fill").style.width = p + "%";
      $("stage-text").textContent = `${job.stage || ""} · ${p}%`;
      updateStepper(p);
      if (job.status === "done") { clearInterval(pollTimer); renderResults(jobId, job); }
      else if (job.status === "error") { clearInterval(pollTimer); showError(job.error || "알 수 없는 오류"); }
    } catch (err) { clearInterval(pollTimer); showError(err.message); }
  }, 1000);
}

// ---------- 결과 ----------
const fileUrl = (jobId, name) => `/api/jobs/${jobId}/file/${encodeURIComponent(name)}`;

function cut(jobId, name, label, fmt, { vertical = false, image = false } = {}) {
  const url = fileUrl(jobId, name);
  const media = image
    ? `<img src="${url}" alt="${label}">`
    : `<video src="${url}" controls preload="metadata"></video>`;
  return `<div class="cut ${vertical ? "vertical" : ""}">
    <div class="cut-label"><span>${label}</span><span class="fmt">${fmt}</span></div>
    ${media}
    <a class="dl" href="${url}" download>${name}</a>
  </div>`;
}

function renderResults(jobId, job) {
  updateStepper(100);
  hide($("progress-section"));
  show($("result-section"));
  const bits = [];
  if (job.source_count > 1) bits.push(`${job.source_count}개 소스 결합`);
  if (job.segment_count != null) bits.push(`선정 구간 ${job.segment_count}개`);
  $("seg-info").textContent = bits.join(" · ");

  const o = job.outputs || {};
  let html = "";
  if (o.longform) html += cut(jobId, o.longform, "롱폼", "16:9");
  (o.shorts || []).forEach((n, i) => (html += cut(jobId, n, `숏츠 ${i + 1}`, "9:16", { vertical: true })));
  (o.shorts_clean || []).forEach((n, i) => (html += cut(jobId, n, `숏츠 ${i + 1} 클린`, "9:16", { vertical: true })));
  if (o.intro) html += cut(jobId, o.intro, "인트로", "hook");
  (o.thumbnail || []).forEach((n, i) => (html += cut(jobId, n, `썸네일 ${i + 1}`, "JPG", { image: true })));
  $("results").innerHTML = html || "<p>생성된 산출물이 없습니다.</p>";

  // 재생성 폼을 직전 옵션으로 초기화
  $("rb_shorts").value = $("job-form").shorts_count.value;
  $("rb_thumb").value = $("job-form").thumbnail_count.value;
  $("rb_blur").checked = $("shorts_blur").checked;
  $("rb_jumpcut").checked = $("shorts_jumpcut").checked;
  $("rb_punchin").checked = $("shorts_punchin").checked;
  $("rb_clean").checked = $("shorts_clean").checked;
  $("rb_sub").value = $("sub_mode").value;
}

// 분석 재사용 재생성 — 산출 옵션만 바꿔 다시
$("rebuild-btn").addEventListener("click", async () => {
  if (!currentJobId) return;
  const fd = new FormData();
  fd.append("shorts_count", $("rb_shorts").value);
  fd.append("thumbnail_count", $("rb_thumb").value);
  fd.append("shorts_blur", $("rb_blur").checked);
  fd.append("shorts_jumpcut", $("rb_jumpcut").checked);
  fd.append("shorts_punchin", $("rb_punchin").checked);
  fd.append("shorts_clean", $("rb_clean").checked);
  appendSubOpts(fd, $("rb_sub").value);

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
});

function showError(msg) {
  if (pollTimer) clearInterval(pollTimer);
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

function saveRecentJob(id, mode) {
  const list = recentJobs().filter((j) => j.id !== id);
  list.unshift({ id, mode, ts: Date.now() });
  localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 15)));
  renderJobPanel();
}

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
    li.innerHTML = `<span class="jp-title">${fmtTs(j.ts)} · ${j.mode || "?"}</span>
      <span class="jp-meta"><span>#${j.id.slice(0, 6)}</span><span class="jp-badge" data-badge></span></span>`;
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
  if (pollTimer) clearInterval(pollTimer);
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

// 초기 렌더 + 새로고침 복귀 — 가장 최근 잡이 아직 돌고 있으면 자동으로 이어서 보여준다
renderJobPanel();
(async () => {
  const last = recentJobs()[0];
  if (!last) return;
  try {
    const res = await fetch(`/api/jobs/${last.id}`);
    if (!res.ok) return;
    const job = await res.json();
    if (job.status === "running") openJob(last.id);
  } catch { /* 서버 정리됨 — 조용히 무시 */ }
})();
