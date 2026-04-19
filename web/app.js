const videoFile = document.querySelector("#videoFile");
const videoName = document.querySelector("#videoName");
const workspaceVideoSelect = document.querySelector("#workspaceVideoSelect");
const inspectButton = document.querySelector("#inspectButton");
const entryValidation = document.querySelector("#entryValidation");

let inspectedVideo = "";

function setValidation(message) {
  entryValidation.textContent = message || "";
}

function validVideoName(name) {
  return /\.(mkv|mp4|avi|mov|m4v|webm)$/i.test(name);
}

function validateInspect() {
  const file = videoFile.files[0];
  const existing = workspaceVideoSelect.value.trim();
  if (!file && !existing && !inspectedVideo) return "Choose a video file first.";
  if (file && !validVideoName(file.name)) {
    return "Video must be .mkv, .mp4, .avi, .mov, .m4v, or .webm.";
  }
  return "";
}

async function parseJsonResponse(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    if (response.status === 413) {
      throw new Error(
        "Upload is too large for browser upload here. Use the workspace video selector instead."
      );
    }
    if (!response.ok) {
      throw new Error(`Server error ${response.status}. Restart the app and try again.`);
    }
    throw new Error("Server returned invalid JSON. Restart the app and try again.");
  }
}

async function uploadFile(endpoint, fieldName, file) {
  const formData = new FormData();
  formData.append(fieldName, file);
  const response = await fetch(endpoint, { method: "POST", body: formData });
  const data = await parseJsonResponse(response);
  if (!response.ok || !data.ok) throw new Error(data.error || "Upload failed.");
  return data;
}

async function loadFiles() {
  const response = await fetch("/api/files");
  const data = await response.json();

  workspaceVideoSelect.innerHTML = '<option value="">No workspace video selected</option>';
  for (const file of data.videos) {
    const option = document.createElement("option");
    option.value = file.name;
    option.textContent = `${file.name} (${formatSize(file.size)})`;
    workspaceVideoSelect.appendChild(option);
  }
}

function formatSize(size) {
  if (size > 1024 * 1024 * 1024) return `${(size / 1024 / 1024 / 1024).toFixed(2)} GiB`;
  if (size > 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MiB`;
  if (size > 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${size} B`;
}

async function inspectSelectedVideo() {
  const error = validateInspect();
  if (error) {
    setValidation(error);
    return;
  }

  inspectButton.disabled = true;
  setValidation("");

  try {
    if (videoFile.files[0]) {
      const uploaded = await uploadFile("/api/upload-video", "video", videoFile.files[0]);
      inspectedVideo = uploaded.file;
      videoFile.value = "";
      videoName.textContent = `Uploaded: ${inspectedVideo}`;
    } else if (workspaceVideoSelect.value.trim()) {
      inspectedVideo = workspaceVideoSelect.value.trim();
      videoName.textContent = `Workspace video: ${inspectedVideo}`;
    }

    const target = `/inspect.html?video=${encodeURIComponent(inspectedVideo)}`;
    window.location.href = target;
  } catch (uploadError) {
    setValidation(uploadError.message);
  } finally {
    inspectButton.disabled = false;
  }
}

videoFile.addEventListener("change", () => {
  const file = videoFile.files[0];
  videoName.textContent = file ? `${file.name} (${formatSize(file.size)})` : "No video selected";
  if (file) {
    workspaceVideoSelect.value = "";
  }
  setValidation(validateInspect());
});

workspaceVideoSelect.addEventListener("change", () => {
  if (workspaceVideoSelect.value.trim()) {
    videoFile.value = "";
    videoName.textContent = "No video selected";
  }
  setValidation(validateInspect());
});

inspectButton.addEventListener("click", inspectSelectedVideo);
loadFiles();
