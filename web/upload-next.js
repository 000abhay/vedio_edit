const uploadSelectedFile = document.querySelector("#uploadSelectedFile");
const featureTags = document.querySelector("#featureTags");
const formatNeeds = document.querySelector("#formatNeeds");
const timestampRows = document.querySelector("#timestampRows");
const addTimestampButton = document.querySelector("#addTimestampButton");
const timestampValidation = document.querySelector("#timestampValidation");
const currentProperties = document.querySelector("#currentProperties");
const pendingChanges = document.querySelector("#pendingChanges");
const nextStatus = document.querySelector("#nextStatus");
const runButton = document.querySelector("#runButton");
const downloadButton = document.querySelector("#downloadButton");
const stopButton = document.querySelector("#stopButton");
const estimateTime = document.querySelector("#estimateTime");
const processState = document.querySelector("#processState");
const needCardTemplate = document.querySelector("#needCardTemplate");

let inspectData = null;
let currentProfile = null;
let subtitleSelection = "";
let runReady = false;
let downloadUrl = "";
let activeJobId = "";

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

async function uploadFile(endpoint, fieldName, file) {
  const formData = new FormData();
  formData.append(fieldName, file);
  const response = await fetch(endpoint, { method: "POST", body: formData });
  const text = await response.text();
  const data = JSON.parse(text);
  if (!response.ok || !data.ok) throw new Error(data.error || "Upload failed.");
  return data;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function parseReport(report) {
  const lines = report.split("\n");
  const pick = (prefix) => lines.find((line) => line.startsWith(prefix))?.slice(prefix.length) || "unknown";
  const videoLine = lines.find((line) => line.trim().startsWith("#0:"))?.trim() || "unknown";
  const audioLine = lines.find((line) => line.trim().startsWith("#1:"))?.trim() || "unknown";
  const subtitlePresent = report.includes("Subtitle streams:\n  none") ? "none" : "present";
  const subtitleLine = lines.find((line) => line.trim().startsWith("#2:"))?.trim() || subtitlePresent;
  return {
    container: pick("Container: "),
    duration: pick("Duration: "),
    size: pick("Size: "),
    video: videoLine,
    audio: audioLine,
    subtitle: subtitleLine,
  };
}

function findCheck(label) {
  return [...(inspectData?.summary?.light || []), ...(inspectData?.summary?.heavy || [])].find(
    (item) => item.label === label
  );
}

function createProfileRow(label, value) {
  return `<div class="profile-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function createTimestampProfileRow(index, start, end) {
  return createProfileRow(`Scene ${index}`, `${start} -> ${end}`);
}

function renderProperties(report) {
  currentProfile = parseReport(report);
  currentProperties.innerHTML = [
    createProfileRow("Container", currentProfile.container),
    createProfileRow("Duration", currentProfile.duration),
    createProfileRow("Size", currentProfile.size),
    createProfileRow("Video", currentProfile.video),
    createProfileRow("Audio", currentProfile.audio),
    createProfileRow("Subtitle", currentProfile.subtitle),
  ].join("");
}

function activeNeeds() {
  const mkv = findCheck("MKV container");
  const videoCodec = findCheck("TV-friendly video codec");
  const audioCodec = findCheck("TV-friendly audio codec");
  const textSubtitle = findCheck("Text subtitle format");
  const englishSubtitle = findCheck("English subtitles");

  const needs = [];
  if (mkv && !mkv.ok) {
    needs.push({
      key: "mkv",
      tag: "MKV",
      title: "MKV Container",
      copy: "This video is not already in MKV format. Switching to MKV is a light process and is recommended for LG TV playback.",
      body: '<div class="mini-note">Current container will be replaced with MKV.</div>',
    });
  }
  if (videoCodec && !videoCodec.ok) {
    needs.push({
      key: "video",
      tag: "Video",
      title: "Video Codec",
      copy: "The current video codec is not TV-friendly. This change may require a heavy re-encode, so we show it only when needed.",
      body: `<div class="mini-note warn">Current: ${escapeHtml(videoCodec.detail)}</div>`,
    });
  }
  if (audioCodec && !audioCodec.ok) {
    needs.push({
      key: "audio",
      tag: "Audio",
      title: "Audio Codec",
      copy: "The audio codec is not TV-friendly. Audio-only conversion is usually lighter than full video conversion.",
      body: `<div class="mini-note">Current: ${escapeHtml(audioCodec.detail)}</div>`,
    });
  }
  if ((textSubtitle && !textSubtitle.ok) || (englishSubtitle && !englishSubtitle.ok)) {
    needs.push({
      key: "subtitle",
      tag: "Subtitle",
      title: "English Subtitle",
      copy: "English subtitles are missing or not in a TV-friendly text format. Upload a subtitle file only if you need it.",
      body: `
        <label class="field">
          <span>Existing subtitle</span>
          <select id="nextSubtitleSelect">
            <option value="">No subtitle selected</option>
          </select>
        </label>

        <form id="nextSubtitleUpload" class="upload-box inline-upload">
          <label class="file-picker">
            <span>Choose subtitle</span>
            <input id="nextSubtitleFile" name="subtitle" type="file" accept=".srt,.ass,.ssa,.vtt" />
          </label>
          <p id="nextSubtitleName" class="file-name">No subtitle selected</p>
          <button type="submit">Upload subtitle</button>
        </form>
      `,
    });
  }
  return needs;
}

function showFeatureTags(needs) {
  const tags = needs.map((item) => item.tag);
  tags.push("Timestamp");

  featureTags.innerHTML = "";
  for (const tag of tags) {
    const badge = document.createElement("span");
    badge.className = "feature-tag";
    badge.textContent = tag;
    featureTags.appendChild(badge);
  }
}

function renderNeedCards() {
  const needs = activeNeeds();
  showFeatureTags(needs);
  formatNeeds.innerHTML = "";

  if (!needs.length) {
    formatNeeds.innerHTML = `
      <section class="option-card need-card ready-card">
        <div class="option-head">
          <h3>MKV / Subtitle / Audio</h3>
          <span class="option-state ready">Already ready</span>
        </div>
        <p class="option-copy">
          This video already looks good for the light-format steps. You only need timestamp ranges if you want to remove scenes.
        </p>
      </section>
    `;
    bindSubtitleControls();
    return;
  }

  for (const need of needs) {
    const node = needCardTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.need = need.key;
    node.querySelector(".need-title").textContent = need.title;
    node.querySelector(".need-copy").textContent = need.copy;
    node.querySelector(".need-body").innerHTML = need.body;
    formatNeeds.appendChild(node);
  }

  bindSubtitleControls();
}

function timestampTemplate(start = "", end = "") {
  return `
    <div class="timestamp-row">
      <div class="timestamp-pill">Scene</div>
      <label class="field compact-field compact-timestamp-field">
        <span>Start</span>
        <input class="timestamp-input" data-kind="start" type="text" value="${escapeHtml(start)}" placeholder="00:00:00" />
      </label>
      <div class="timestamp-separator">to</div>
      <label class="field compact-field compact-timestamp-field">
        <span>End</span>
        <input class="timestamp-input" data-kind="end" type="text" value="${escapeHtml(end)}" placeholder="00:00:00" />
      </label>
      <button class="ghost-button remove-row" type="button">Remove</button>
    </div>
  `;
}

function ensureTimestampRows() {
  if (!timestampRows.children.length) {
    timestampRows.insertAdjacentHTML("beforeend", timestampTemplate());
  }
}

function timeToSeconds(value) {
  if (!/^\d{2}:\d{2}:\d{2}$/.test(value)) return Number.NaN;
  const [hh, mm, ss] = value.split(":").map(Number);
  if (hh > 99 || mm > 59 || ss > 59) return Number.NaN;
  return hh * 3600 + mm * 60 + ss;
}

function durationToSeconds(value) {
  const match = String(value).trim().match(/^(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?$/);
  if (!match) return Number.NaN;
  const [, hh, mm, ss] = match.map(Number);
  return hh * 3600 + mm * 60 + ss;
}

function collectTimestamps() {
  const rows = [...timestampRows.querySelectorAll(".timestamp-row")];
  return rows.map((row) => ({
    start: row.querySelector('[data-kind="start"]').value.trim(),
    end: row.querySelector('[data-kind="end"]').value.trim(),
  }));
}

function validateTimestamps() {
  const ranges = collectTimestamps();
  const activeRanges = ranges
    .map((item, index) => ({ ...item, index: index + 1 }))
    .filter((item) => item.start || item.end);
  const parsedRanges = [];
  const maxDuration = durationToSeconds(currentProfile?.duration || "");

  for (const item of activeRanges) {
    if (!item.start || !item.end) {
      return `Scene ${item.index} is incomplete. Add both start and end time.`;
    }

    const start = timeToSeconds(item.start);
    const end = timeToSeconds(item.end);
    if (!Number.isFinite(start)) {
      return `Scene ${item.index} start time must use HH:MM:SS with minutes and seconds below 60.`;
    }
    if (!Number.isFinite(end)) {
      return `Scene ${item.index} end time must use HH:MM:SS with minutes and seconds below 60.`;
    }
    if (start >= end) {
      return `Scene ${item.index} start time must be smaller than end time.`;
    }
    if (Number.isFinite(maxDuration) && end > maxDuration) {
      return `Scene ${item.index} ends after the video duration (${currentProfile.duration}).`;
    }
    parsedRanges.push({ ...item, startSeconds: start, endSeconds: end });
  }

  parsedRanges.sort((a, b) => a.startSeconds - b.startSeconds);
  for (let index = 1; index < parsedRanges.length; index += 1) {
    const previous = parsedRanges[index - 1];
    const current = parsedRanges[index];
    if (current.startSeconds <= previous.endSeconds) {
      return `Scene ${current.index} overlaps with scene ${previous.index}. Keep each range separate.`;
    }
  }
  return "";
}

function selectedSubtitleName() {
  const select = document.querySelector("#nextSubtitleSelect");
  const uploadName = document.querySelector("#nextSubtitleName");
  return (select && select.value.trim()) || (uploadName && uploadName.textContent.trim()) || subtitleSelection || "";
}

function setDashboardLines(lines) {
  nextStatus.innerHTML = lines.map((line) => `<div class="status-line">${escapeHtml(line)}</div>`).join("");
}

function estimateSeconds() {
  const sizeText = currentProfile?.size || "";
  const sizeMatch = sizeText.match(/([\d.]+)\s+MiB/);
  const sizeMiB = sizeMatch ? Number(sizeMatch[1]) : 0;
  const ranges = collectTimestamps().filter((item) => item.start && item.end).length;
  const mkv = findCheck("MKV container");
  const audioCodec = findCheck("TV-friendly audio codec");
  const englishSubtitle = findCheck("English subtitles");
  const textSubtitle = findCheck("Text subtitle format");
  let seconds = 8 + Math.ceil(sizeMiB / 25);
  if (mkv && !mkv.ok) seconds += 12;
  if (audioCodec && !audioCodec.ok) seconds += 18;
  if ((englishSubtitle && !englishSubtitle.ok) || (textSubtitle && !textSubtitle.ok)) seconds += 6;
  seconds += ranges * 5;
  return Math.max(10, seconds);
}

function formatEstimate(totalSeconds) {
  if (totalSeconds < 60) return `~${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `~${minutes}m ${seconds}s`;
}

function updateEstimate() {
  estimateTime.textContent = formatEstimate(estimateSeconds());
}

function renderPendingChanges() {
  const rows = [];
  const mkv = findCheck("MKV container");
  const videoCodec = findCheck("TV-friendly video codec");
  const audioCodec = findCheck("TV-friendly audio codec");
  const englishSubtitle = findCheck("English subtitles");
  const textSubtitle = findCheck("Text subtitle format");
  const ranges = collectTimestamps().filter((item) => item.start && item.end);

  rows.push(createProfileRow("Video", getVideoFromUrl().split("/").pop() || "unknown"));

  if (mkv && !mkv.ok) rows.push(createProfileRow("Container", "Convert to MKV"));
  if (videoCodec && !videoCodec.ok) rows.push(createProfileRow("Video codec", "Needs TV-ready conversion"));
  if (audioCodec && !audioCodec.ok) rows.push(createProfileRow("Audio codec", "Convert audio track"));

  if ((englishSubtitle && !englishSubtitle.ok) || (textSubtitle && !textSubtitle.ok)) {
    const subtitle = selectedSubtitleName();
    rows.push(
      createProfileRow(
        "Subtitle",
        subtitle && subtitle !== "No subtitle selected" ? subtitle : "Need English subtitle"
      )
    );
  }

  rows.push(createProfileRow("Timestamps", `${ranges.length} range(s)`));
  for (const [index, item] of ranges.entries()) {
    rows.push(createTimestampProfileRow(index + 1, item.start, item.end));
  }

  pendingChanges.innerHTML = rows.join("");
  updateEstimate();
}

function resetRunState() {
  runReady = false;
  downloadUrl = "";
  activeJobId = "";
  downloadButton.hidden = true;
  stopButton.hidden = true;
  processState.textContent = "Waiting";
}

async function loadSubtitleOptions() {
  const select = document.querySelector("#nextSubtitleSelect");
  if (!select) return;

  const response = await fetch("/api/files");
  const data = await response.json();
  select.innerHTML = '<option value="">No subtitle selected</option>';
  for (const file of data.subtitles) {
    const option = document.createElement("option");
    option.value = file.name;
    option.textContent = file.name;
    select.appendChild(option);
  }
  if (subtitleSelection) select.value = subtitleSelection;
}

function bindSubtitleControls() {
  const select = document.querySelector("#nextSubtitleSelect");
  const fileInput = document.querySelector("#nextSubtitleFile");
  const uploadForm = document.querySelector("#nextSubtitleUpload");
  const nameNode = document.querySelector("#nextSubtitleName");

  if (select) {
    select.addEventListener("change", () => {
      subtitleSelection = select.value;
      resetRunState();
      renderPendingChanges();
    });
  }

  if (fileInput && nameNode) {
    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      nameNode.textContent = file ? file.name : "No subtitle selected";
      resetRunState();
      renderPendingChanges();
    });
  }

  if (uploadForm && fileInput && nameNode) {
    uploadForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!fileInput.files.length) {
        setDashboardLines(["Choose a subtitle file first."]);
        return;
      }

      try {
        const data = await uploadFile("/api/upload-subtitle", "subtitle", fileInput.files[0]);
        subtitleSelection = data.file;
        setDashboardLines([data.message]);
        fileInput.value = "";
        nameNode.textContent = data.file;
        await loadSubtitleOptions();
        resetRunState();
        renderPendingChanges();
      } catch (error) {
        setDashboardLines([error.message]);
      }
    });
  }
}

async function pollJob(jobId) {
  while (activeJobId === jobId) {
    const response = await fetch(`/api/process/status?id=${encodeURIComponent(jobId)}`);
    const data = await response.json();
    processState.textContent = data.state_label || data.state;

    const lines = [];
    if (data.stage) lines.push(`Stage: ${data.stage}`);
    if (data.progress_text) lines.push(`Progress: ${data.progress_text}`);
    for (const item of data.operations || []) lines.push(item);
    if (data.error) lines.push(`Error: ${data.error}`);
    if (!lines.length) lines.push("Processing started...");
    setDashboardLines(lines);

    if (data.state === "success") {
      runReady = true;
      downloadUrl = data.download;
      downloadButton.hidden = false;
      stopButton.hidden = true;
      runButton.disabled = false;
      activeJobId = "";
      return;
    }

    if (data.state === "error" || data.state === "stopped") {
      runReady = false;
      downloadButton.hidden = true;
      stopButton.hidden = true;
      runButton.disabled = false;
      activeJobId = "";
      return;
    }

    await new Promise((resolve) => setTimeout(resolve, 900));
  }
}

async function loadPage() {
  const video = getVideoFromUrl();
  if (!video) {
    setDashboardLines(["Missing video path. Go back and inspect a file first."]);
    return;
  }

  uploadSelectedFile.textContent = `Video: ${video}`;

  try {
    inspectData = await requestJson("/api/inspect", { video });
    renderProperties(inspectData.report);
    renderNeedCards();
    ensureTimestampRows();
    await loadSubtitleOptions();
    renderPendingChanges();
    setDashboardLines(["Review the needed changes and timestamp ranges, then continue."]);
  } catch (error) {
    setDashboardLines([error.message]);
  }
}

addTimestampButton.addEventListener("click", () => {
  const error = validateTimestamps();
  if (error) {
    timestampValidation.textContent = error;
    return;
  }
  timestampRows.insertAdjacentHTML("beforeend", timestampTemplate());
  resetRunState();
  renderPendingChanges();
});

timestampRows.addEventListener("click", (event) => {
  const button = event.target.closest(".remove-row");
  if (!button) return;
  button.closest(".timestamp-row").remove();
  ensureTimestampRows();
  timestampValidation.textContent = validateTimestamps();
  resetRunState();
  renderPendingChanges();
});

timestampRows.addEventListener("input", () => {
  timestampValidation.textContent = validateTimestamps();
  resetRunState();
  renderPendingChanges();
});

runButton.addEventListener("click", async () => {
  const error = validateTimestamps();
  timestampValidation.textContent = error;
  if (error) {
    resetRunState();
    processState.textContent = "Validation error";
    setDashboardLines([`Run stopped: ${error}`]);
    return;
  }

  const englishSubtitle = findCheck("English subtitles");
  const textSubtitle = findCheck("Text subtitle format");
  const ranges = collectTimestamps().filter((item) => item.start && item.end);
  const subtitle = selectedSubtitleName();
  const subtitleNeeded = (englishSubtitle && !englishSubtitle.ok) || (textSubtitle && !textSubtitle.ok);

  if (subtitleNeeded && (!subtitle || subtitle === "No subtitle selected")) {
    processState.textContent = "Validation error";
    setDashboardLines(["Run stopped: subtitle is still needed before final export."]);
    return;
  }

  runButton.disabled = true;
  resetRunState();
  processState.textContent = "Starting";
  stopButton.hidden = false;
  setDashboardLines(["Starting processing job..."]);

  try {
    const data = await requestJson("/api/process/start", {
      video: getVideoFromUrl(),
      subtitle: subtitleNeeded ? subtitle : "",
      timestamps: ranges,
    });
    activeJobId = data.job_id;
    processState.textContent = "Running";
    await pollJob(data.job_id);
  } catch (runError) {
    processState.textContent = "Error";
    setDashboardLines([`Run stopped: ${runError.message}`]);
    runButton.disabled = false;
  }
});

stopButton.addEventListener("click", async () => {
  if (!activeJobId) return;
  stopButton.disabled = true;
  try {
    await requestJson("/api/process/stop", { job_id: activeJobId });
    processState.textContent = "Stopping";
    setDashboardLines(["Stop requested. Waiting for the current step to end..."]);
  } catch (error) {
    setDashboardLines([`Could not stop process: ${error.message}`]);
  } finally {
    stopButton.disabled = false;
  }
});

downloadButton.addEventListener("click", () => {
  if (!runReady) {
    setDashboardLines(["Run the checks first, then download will be available."]);
    return;
  }
  if (!downloadUrl) {
    setDashboardLines(["Download link is missing. Run the process again."]);
    return;
  }
  window.location.href = downloadUrl;
});

loadPage();
