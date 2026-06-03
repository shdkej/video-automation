// video-automation 웹 UI — 업로드 → 폴링 → 결과 렌더

const $ = (id) => document.getElementById(id);
const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

const MODE_HINTS = {
  scene: "ffmpeg 씬 감지. API 키 불필요, 무료. 자막은 speech 모드에서만 들어갑니다.",
  speech: "Whisper로 음성을 받아 LLM이 핵심 구간 선정 + 한국어 자막. .env에 OPENAI_API_KEY 또는 ANTHROPIC_API_KEY 필요.",
  vision: "정적/무음 영상용. 모자이크를 비전 LLM이 분석. API 키 필요.",
};

$("mode").addEventListener("change", (e) => {
  $("mode-hint").textContent = MODE_HINTS[e.target.value] || "";
});
$("mode-hint").textContent = MODE_HINTS[$("mode").value];

let pollTimer = null;

$("job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData($("job-form"));
  // 체크박스는 체크 안 되면 FormData에 없음 → 명시적으로 boolean 보정
  fd.set("shorts_blur", $("shorts_blur").checked);
  fd.set("no_subtitle", $("no_subtitle").checked);

  $("submit-btn").disabled = true;
  try {
    const res = await fetch("/api/jobs", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const { job_id } = await res.json();
    hide($("form-section"));
    show($("progress-section"));
    startPolling(job_id);
  } catch (err) {
    showError(err.message);
  } finally {
    $("submit-btn").disabled = false;
  }
});

function startPolling(jobId) {
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      $("bar-fill").style.width = (job.progress || 0) + "%";
      $("stage-text").textContent = `${job.stage || ""} (${job.progress || 0}%)`;

      if (job.status === "done") {
        clearInterval(pollTimer);
        renderResults(jobId, job);
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        showError(job.error || "알 수 없는 오류");
      }
    } catch (err) {
      clearInterval(pollTimer);
      showError(err.message);
    }
  }, 1000);
}

function fileUrl(jobId, name) {
  return `/api/jobs/${jobId}/file/${encodeURIComponent(name)}`;
}

function videoCard(jobId, name, label, vertical) {
  const url = fileUrl(jobId, name);
  return `
    <div class="result-item ${vertical ? "vertical" : ""}">
      <div class="result-label">${label}</div>
      <video src="${url}" controls preload="metadata"></video>
      <a class="dl" href="${url}" download>${name} 다운로드</a>
    </div>`;
}

function imgCard(jobId, name, label) {
  const url = fileUrl(jobId, name);
  return `
    <div class="result-item">
      <div class="result-label">${label}</div>
      <img src="${url}" alt="${label}">
      <a class="dl" href="${url}" download>${name} 다운로드</a>
    </div>`;
}

function renderResults(jobId, job) {
  hide($("progress-section"));
  show($("result-section"));
  if (job.segment_count != null) {
    $("seg-info").textContent = `· 선정 구간 ${job.segment_count}개`;
  }
  const o = job.outputs || {};
  let html = "";

  if (o.longform) html += videoCard(jobId, o.longform, "롱폼 (16:9)", false);
  (o.shorts || []).forEach((n, i) =>
    (html += videoCard(jobId, n, `숏츠 ${i + 1} (9:16)`, true))
  );
  if (o.intro) html += videoCard(jobId, o.intro, "인트로", false);
  (o.thumbnail || []).forEach((n, i) =>
    (html += imgCard(jobId, n, `썸네일 ${i + 1}`))
  );

  $("results").innerHTML = html || "<p>생성된 산출물이 없습니다.</p>";
}

function showError(msg) {
  if (pollTimer) clearInterval(pollTimer);
  hide($("form-section"));
  hide($("progress-section"));
  hide($("result-section"));
  show($("error-section"));
  $("error-text").textContent = msg;
}

function reset() {
  hide($("result-section"));
  hide($("error-section"));
  hide($("progress-section"));
  show($("form-section"));
  $("results").innerHTML = "";
  $("bar-fill").style.width = "0%";
}

$("reset-btn").addEventListener("click", reset);
$("error-reset-btn").addEventListener("click", reset);
