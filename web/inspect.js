const report = document.querySelector("#report");
const statusLog = document.querySelector("#statusLog");
const selectedFile = document.querySelector("#selectedFile");
const lightChecks = document.querySelector("#lightChecks");
const heavyChecks = document.querySelector("#heavyChecks");
const nextButton = document.querySelector("#nextButton");
const reportCards = document.querySelector("#reportCards");

function getVideoFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get("video") || "";
}

async function requestJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const data = JSON.parse(text);
  if (!response.ok || !data.ok) throw new Error(data.error || "Operation failed.");
  return data;
}

function renderChecks(target, checks) {
  target.innerHTML = "";
  for (const check of checks || []) {
    const row = document.createElement("div");
    row.className = `check-row ${check.ok ? "ok" : ""}`;
    row.innerHTML = `
      <div>
        <span class="check-title">${escapeHtml(check.label)}</span>
        <span class="check-detail">${escapeHtml(check.detail)}</span>
      </div>
    `;
    target.appendChild(row);
  }
}

function parseReport(reportText) {
  const lines = reportText.split("\n");
  const pick = (prefix) => lines.find((line) => line.startsWith(prefix))?.slice(prefix.length) || "unknown";
  const videoLine = lines.find((line) => line.trim().startsWith("#0:"))?.trim() || "unknown";
  const audioLine = lines.find((line) => line.trim().startsWith("#1:"))?.trim() || "unknown";
  const subtitleLine = lines.find((line) => line.trim().startsWith("#2:"))?.trim() || "none";
  return {
    container: pick("Container: "),
    duration: pick("Duration: "),
    size: pick("Size: "),
    video: videoLine,
    audio: audioLine,
    subtitle: subtitleLine,
  };
}

function renderReportCards(parsed) {
  reportCards.innerHTML = `
    <div class="dashboard-card"><span>Container</span><strong>${escapeHtml(parsed.container)}</strong></div>
    <div class="dashboard-card"><span>Duration</span><strong>${escapeHtml(parsed.duration)}</strong></div>
    <div class="dashboard-card"><span>Size</span><strong>${escapeHtml(parsed.size)}</strong></div>
  `;
  report.innerHTML = `
    <div class="report-row"><span>Video</span><strong>${escapeHtml(parsed.video)}</strong></div>
    <div class="report-row"><span>Audio</span><strong>${escapeHtml(parsed.audio)}</strong></div>
    <div class="report-row"><span>Subtitle</span><strong>${escapeHtml(parsed.subtitle)}</strong></div>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadInspectPage() {
  const video = getVideoFromUrl();
  if (!video) {
    statusLog.textContent = "Missing video path. Go back and inspect a file first.";
    report.textContent = "No video selected.";
    return;
  }

  selectedFile.textContent = `Inspected: ${video}`;
  nextButton.href = `/upload-next.html?video=${encodeURIComponent(video)}`;

  try {
    const data = await requestJson("/api/inspect", { video });
    const parsed = parseReport(data.report);
    renderReportCards(parsed);
    renderChecks(lightChecks, data.summary?.light);
    renderChecks(heavyChecks, data.summary?.heavy);
    statusLog.innerHTML = `
      <div class="status-line"><strong>Inspect complete</strong></div>
      <div class="status-line">Light and heavy checks are ready.</div>
      <div class="status-line">Use Upload to continue to the next route.</div>
    `;
  } catch (error) {
    statusLog.textContent = error.message;
    report.textContent = "Could not load inspect result.";
  }
}

loadInspectPage();
