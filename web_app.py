import contextlib
import tempfile
import io
import json
import mimetypes
import os
import shutil
import subprocess
import threading
import time
import uuid
import warnings
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

warnings.filterwarnings("ignore", category=DeprecationWarning)
import cgi

import cut


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
UPLOAD_DIR = ROOT / "uploads"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def safe_relative_path(value: str, allowed_extensions: set[str], label: str) -> Path:
    if not value:
        raise ValueError(f"{label} is required.")

    path = (ROOT / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as error:
        raise ValueError(f"{label} must be inside the project folder.") from error

    if path.suffix.lower() not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"{label} must use one of these extensions: {allowed}.")

    if not path.is_file():
        raise ValueError(f"{label} was not found: {path.name}")

    return path


def safe_output_name(value: str | None, default_name: str) -> Path:
    name = (value or default_name).strip()
    if not name:
        name = default_name

    output = Path(name)
    if output.name != name:
        raise ValueError("Output name must be a filename only, not a folder path.")
    if output.suffix.lower() != ".mkv":
        raise ValueError("Output name must end with .mkv.")

    return ROOT / output.name


def capture_output(func, *args, **kwargs) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        func(*args, **kwargs)
    return buffer.getvalue().strip()


def workspace_files() -> dict:
    videos = []
    subtitles = []

    for path in sorted(ROOT.iterdir()):
        if not path.is_file():
            continue
        item = {
            "name": path.name,
            "size": path.stat().st_size,
        }
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(item)
        if path.suffix.lower() in SUBTITLE_EXTENSIONS:
            subtitles.append(item)

    if UPLOAD_DIR.exists():
        for path in sorted(UPLOAD_DIR.iterdir()):
            if not path.is_file():
                continue
            item = {
                "name": str(path.relative_to(ROOT)),
                "size": path.stat().st_size,
            }
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(item)
            if path.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitles.append(item)

    return {"videos": videos, "subtitles": subtitles}


def upload_file(handler: BaseHTTPRequestHandler, field_name: str, extensions: set[str]) -> Path:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Upload must be multipart/form-data.")

    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": handler.headers.get("Content-Length", "0"),
        },
    )
    field = form[field_name] if field_name in form else None
    if field is None or not getattr(field, "filename", ""):
        raise ValueError("Upload file is required.")

    filename = Path(field.filename).name
    if Path(filename).suffix.lower() not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise ValueError(f"Upload must use one of these extensions: {allowed}.")

    UPLOAD_DIR.mkdir(exist_ok=True)
    destination = UPLOAD_DIR / filename
    with destination.open("wb") as target:
        shutil.copyfileobj(field.file, target)
    return destination


def language_set(streams: list[dict]) -> set[str]:
    languages = set()
    for stream in streams:
        tags = stream.get("tags") or {}
        language = tags.get("language")
        if language:
            languages.add(language.lower())
    return languages


def inspect_summary(ffprobe: str, video: Path) -> dict:
    data = cut.probe_media(ffprobe, video)
    media_format = data.get("format") or {}
    streams = data.get("streams") or []
    video_streams = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video" and not cut.is_attached_picture(stream)
    ]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]

    format_names = media_format.get("format_name", "unknown")
    video_codecs = {stream.get("codec_name", "unknown") for stream in video_streams}
    audio_codecs = {stream.get("codec_name", "unknown") for stream in audio_streams}
    subtitle_codecs = {stream.get("codec_name", "unknown") for stream in subtitle_streams}
    audio_languages = language_set(audio_streams)
    subtitle_languages = language_set(subtitle_streams)

    is_mkv = "matroska" in format_names or "webm" in format_names
    video_ok = bool(video_codecs) and video_codecs <= cut.TV_FRIENDLY_VIDEO_CODECS
    audio_ok = not audio_codecs or audio_codecs <= cut.TV_FRIENDLY_AUDIO_CODECS
    text_subtitles = not subtitle_codecs or subtitle_codecs <= cut.TEXT_SUBTITLE_CODECS
    has_hindi_audio = bool(audio_languages & {"hin", "hi", "hindi"})
    has_english_subtitle = bool(subtitle_languages & {"eng", "en", "english"})

    return {
        "light": [
            {
                "label": "MKV container",
                "ok": is_mkv,
                "detail": "Ready" if is_mkv else "Use To MKV. This is light.",
            },
            {
                "label": "TV-friendly video codec",
                "ok": video_ok,
                "detail": ", ".join(sorted(video_codecs)) or "No video stream found",
            },
            {
                "label": "TV-friendly audio codec",
                "ok": audio_ok,
                "detail": ", ".join(sorted(audio_codecs)) or "No audio stream found",
            },
            {
                "label": "Text subtitle format",
                "ok": text_subtitles,
                "detail": ", ".join(sorted(subtitle_codecs)) or "No subtitle track",
            },
            {
                "label": "Hindi audio",
                "ok": has_hindi_audio,
                "detail": "Present" if has_hindi_audio else "Not found or not labelled",
            },
            {
                "label": "English subtitles",
                "ok": has_english_subtitle,
                "detail": "Present" if has_english_subtitle else "Upload/merge .srt if needed",
            },
        ],
        "heavy": [
            {
                "label": "H.265/AV1/VP9 to H.264",
                "ok": video_ok,
                "detail": "Not needed" if video_ok else "Needs full video re-encode",
            },
            {
                "label": "Resize/compress video",
                "ok": False,
                "detail": "Heavy operation. Avoid in Codespaces.",
            },
            {
                "label": "Burn subtitles into picture",
                "ok": False,
                "detail": "Heavy operation. Prefer Add subtitles instead.",
            },
            {
                "label": "Exact frame cutting",
                "ok": False,
                "detail": "Heavy operation. Current fast cuts are keyframe-based.",
            },
        ],
    }


def next_output_path(video: Path) -> Path:
    base_name = f"{video.stem}_ready"
    candidate = ROOT / f"{base_name}.mkv"
    index = 2
    while candidate.exists():
        candidate = ROOT / f"{base_name}_{index}.mkv"
        index += 1
    return candidate


def parse_timestamp_ranges(items: list[dict]) -> list[tuple[str, str]]:
    ranges: list[tuple[str, str]] = []
    parsed: list[tuple[int, float, float]] = []
    for index, item in enumerate(items, start=1):
        start = str(item.get("start", "")).strip()
        end = str(item.get("end", "")).strip()
        if not start or not end:
            raise ValueError(f"Scene {index} is incomplete. Add both start and end time.")
        start_seconds = cut.parse_timestamp(start)
        end_seconds = cut.parse_timestamp(end)
        if start_seconds >= end_seconds:
            raise ValueError(f"Scene {index} start time must be smaller than end time.")
        ranges.append((start, end))
        parsed.append((index, start_seconds, end_seconds))

    parsed.sort(key=lambda item: item[1])
    for previous, current in zip(parsed, parsed[1:]):
        if current[1] <= previous[2]:
            raise ValueError(
                f"Scene {current[0]} overlaps with scene {previous[0]}. Keep each range separate."
            )
    return ranges


def estimate_process_seconds(video: Path, summary: dict, timestamp_ranges: list[tuple[str, str]]) -> int:
    size_mib = video.stat().st_size / 1024 / 1024
    estimate = 8 + int(size_mib / 25)
    if not next(item["ok"] for item in summary["light"] if item["label"] == "MKV container"):
        estimate += 12
    if not next(item["ok"] for item in summary["light"] if item["label"] == "TV-friendly audio codec"):
        estimate += 18
    subtitle_ok = next(item["ok"] for item in summary["light"] if item["label"] == "English subtitles")
    if not subtitle_ok:
        estimate += 6
    estimate += len(timestamp_ranges) * 5
    return max(10, estimate)


def update_job(job_id: str, **changes) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(changes)


def append_job_operation(job_id: str, message: str) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["operations"].append(message)


def request_job_stop(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise ValueError("Process job was not found.")
        job["cancel_event"].set()
        process = job.get("process")
    if process and process.poll() is None:
        process.terminate()


def run_command_for_job(job_id: str, command: list[str]) -> None:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    update_job(job_id, process=process)
    try:
        while True:
            if JOBS[job_id]["cancel_event"].is_set():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RuntimeError("Processing stopped by user.")
            code = process.poll()
            if code is not None:
                if code != 0:
                    stderr = process.stderr.read().strip() if process.stderr else ""
                    raise ValueError(stderr or f"Command failed with exit code {code}.")
                return
            time.sleep(0.2)
    finally:
        update_job(job_id, process=None)


def remux_to_mkv_job(job_id: str, ffmpeg: str, video: Path, output_path: Path) -> None:
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-map",
        "0",
        "-c",
        "copy",
        str(output_path),
    ]
    run_command_for_job(job_id, command)


def convert_audio_to_aac_job(job_id: str, ffmpeg: str, ffprobe: str, video: Path, output_path: Path) -> None:
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
    ]
    if cut.has_audio_stream(ffprobe, video):
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.extend(["-an"])
    command.extend(["-map", "0:s?", "-c:s", "copy", str(output_path)])
    run_command_for_job(job_id, command)


def create_copy_cut_video_job(
    job_id: str,
    ffmpeg: str,
    video: Path,
    output_path: Path,
    keep_segments: list[tuple[float, float]],
    include_audio: bool,
    temp_dir: Path,
) -> None:
    segment_paths: list[Path] = []
    for index, (start, end) in enumerate(keep_segments):
        segment_path = temp_dir / f"part_{index:03d}.mkv"
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            cut.filter_seconds(start),
            "-to",
            cut.filter_seconds(end),
            "-i",
            str(video),
            "-map",
            "0:v:0",
        ]
        if include_audio:
            command.extend(["-map", "0:a:0"])
        command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", str(segment_path)])
        run_command_for_job(job_id, command)
        segment_paths.append(segment_path)
        update_job(
            job_id,
            progress=min(85, 20 + int(((index + 1) / max(len(keep_segments), 1)) * 45)),
            progress_text=f"Cutting scene parts {index + 1}/{len(keep_segments)}",
        )

    concat_list_path = temp_dir / "concat.txt"
    concat_list_path.write_text(
        "".join(f"file '{cut.quote_concat_path(path)}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    concat_command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-map",
        "0",
        "-c",
        "copy",
        str(output_path),
    ]
    run_command_for_job(job_id, concat_command)


def add_subtitle_job(job_id: str, ffmpeg: str, video: Path, subtitle: Path, output_path: Path) -> None:
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-i",
        str(subtitle),
        "-map",
        "0",
        "-map",
        "1:0",
        "-c",
        "copy",
        "-c:s",
        "srt",
        "-metadata:s:s:0",
        "language=eng",
        str(output_path),
    ]
    run_command_for_job(job_id, command)


def process_video_workflow(
    job_id: str,
    video: Path,
    subtitle: Path | None,
    timestamp_ranges: list[tuple[str, str]],
) -> dict:
    ffmpeg = cut.resolve_tool("ffmpeg")
    ffprobe = cut.resolve_tool("ffprobe")
    summary = inspect_summary(ffprobe, video)
    current_video = video
    operations: list[str] = []

    video_ok = next(
        item["ok"] for item in summary["light"] if item["label"] == "TV-friendly video codec"
    )
    audio_ok = next(
        item["ok"] for item in summary["light"] if item["label"] == "TV-friendly audio codec"
    )

    if not video_ok:
        raise ValueError(
            "This video needs full video re-encode to become TV-ready. "
            "That heavy step is not enabled in this lightweight web workflow yet."
        )

    with tempfile.TemporaryDirectory(prefix="web-video-process-", dir=ROOT) as temp_dir_name:
        temp_dir = Path(temp_dir_name)

        if not audio_ok:
            update_job(job_id, stage="Converting audio", progress=20, progress_text="Preparing AAC audio")
            converted = temp_dir / f"{current_video.stem}_audio_fixed.mkv"
            convert_audio_to_aac_job(job_id, ffmpeg, ffprobe, current_video, converted)
            current_video = converted
            operations.append("Audio converted to AAC in MKV successfully.")
            append_job_operation(job_id, operations[-1])
        else:
            is_mkv = next(
                item["ok"] for item in summary["light"] if item["label"] == "MKV container"
            )
            if not is_mkv:
                update_job(job_id, stage="Converting container", progress=20, progress_text="Preparing MKV container")
                remuxed = temp_dir / f"{current_video.stem}_remux.mkv"
                remux_to_mkv_job(job_id, ffmpeg, current_video, remuxed)
                current_video = remuxed
                operations.append("Container converted to MKV successfully.")
                append_job_operation(job_id, operations[-1])

        if timestamp_ranges:
            duration = cut.probe_duration(ffprobe, current_video)
            for index, (_, end) in enumerate(timestamp_ranges, start=1):
                if cut.parse_timestamp(end) > duration:
                    raise ValueError(
                        f"Scene {index} ends after the video duration ({cut.format_seconds(duration)})."
                    )
            include_audio = cut.has_audio_stream(ffprobe, current_video)
            keep_segments = cut.build_keep_segments_from_cuts(duration, timestamp_ranges)
            cut_output = temp_dir / f"{current_video.stem}_cut.mkv"
            update_job(job_id, stage="Cutting timestamps", progress=25, progress_text="Splitting good scenes")
            create_copy_cut_video_job(
                job_id, ffmpeg, current_video, cut_output, keep_segments, include_audio, temp_dir
            )
            current_video = cut_output
            operations.append(f"Timestamp cuts applied successfully for {len(timestamp_ranges)} range(s).")
            append_job_operation(job_id, operations[-1])

        if subtitle is not None:
            update_job(job_id, stage="Merging subtitle", progress=88, progress_text="Adding subtitle track")
            sub_output = temp_dir / f"{current_video.stem}_subbed.mkv"
            add_subtitle_job(job_id, ffmpeg, current_video, subtitle, sub_output)
            current_video = sub_output
            operations.append(f"English subtitle merged successfully from {subtitle.name}.")
            append_job_operation(job_id, operations[-1])

        update_job(job_id, stage="Finalizing output", progress=95, progress_text="Saving final file")
        final_output = next_output_path(video)
        shutil.copy2(current_video, final_output)
        operations.append(f"Final video prepared successfully: {final_output.name}.")
        append_job_operation(job_id, operations[-1])

    return {
        "output": final_output,
        "operations": operations,
    }


class VideoToolHandler(BaseHTTPRequestHandler):
    server_version = "VideoTool/1.0"

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = unquote(parsed_url.path)
        if path == "/":
            self.serve_file(WEB_DIR / "index.html")
            return
        if path == "/api/files":
            self.send_json(workspace_files())
            return
        if path == "/api/process/status":
            self.handle_process_status(parsed_url.query)
            return
        if path.startswith("/download/"):
            self.serve_download(ROOT / path.removeprefix("/download/"))
            return

        target = (WEB_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(WEB_DIR)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_file(target)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/inspect":
                self.handle_inspect()
                return
            if self.path == "/api/cut":
                self.handle_cut()
                return
            if self.path == "/api/to-mkv":
                self.handle_to_mkv()
                return
            if self.path == "/api/add-sub":
                self.handle_add_sub()
                return
            if self.path == "/api/upload-subtitle":
                self.handle_upload_subtitle()
                return
            if self.path == "/api/upload-video":
                self.handle_upload_video()
                return
            if self.path == "/api/process/start":
                self.handle_process_start()
                return
            if self.path == "/api/process/stop":
                self.handle_process_stop()
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, subprocess.CalledProcessError, FileNotFoundError) as error:
            self.send_json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def handle_inspect(self) -> None:
        payload = self.read_json()
        video = safe_relative_path(payload.get("video", ""), VIDEO_EXTENSIONS, "Video")
        ffprobe = cut.resolve_tool("ffprobe")
        report = capture_output(cut.inspect_video, ffprobe, video)
        summary = inspect_summary(ffprobe, video)
        self.send_json({"ok": True, "report": report, "summary": summary})

    def handle_cut(self) -> None:
        payload = self.read_json()
        video_value = payload.get("video") or cut.LOCAL_DEFAULT_VIDEO.name
        video = safe_relative_path(video_value, VIDEO_EXTENSIONS, "Video")
        output = safe_output_name(payload.get("output"), "wolf_clean.mkv")
        padding = float(payload.get("padding", cut.DEFAULT_CUT_PADDING_SECONDS))
        if padding < 0 or padding > 30:
            raise ValueError("Padding must be between 0 and 30 seconds.")

        ffmpeg = cut.resolve_tool("ffmpeg")
        ffprobe = cut.resolve_tool("ffprobe")
        duration = cut.probe_duration(ffprobe, video)
        include_audio = cut.has_audio_stream(ffprobe, video)
        keep_segments = cut.build_keep_segments(duration, padding)
        temp_output = output.with_suffix(f".tmp{output.suffix}")
        if temp_output.exists():
            temp_output.unlink()

        cut.create_copy_cut_video(ffmpeg, video, temp_output, keep_segments, include_audio)
        temp_output.replace(output)
        self.send_json(
            {
                "ok": True,
                "message": f"Cut video created: {output.name}",
                "download": f"/download/{output.name}",
            }
        )

    def handle_to_mkv(self) -> None:
        payload = self.read_json()
        video = safe_relative_path(payload.get("video", ""), VIDEO_EXTENSIONS, "Video")
        output = safe_output_name(payload.get("output"), f"{video.stem}_tv.mkv")
        ffmpeg = cut.resolve_tool("ffmpeg")
        cut.remux_to_mkv(ffmpeg, video, str(output))
        self.send_json(
            {
                "ok": True,
                "message": f"MKV created: {output.name}",
                "download": f"/download/{output.name}",
            }
        )

    def handle_add_sub(self) -> None:
        payload = self.read_json()
        video = safe_relative_path(payload.get("video", ""), VIDEO_EXTENSIONS, "Video")
        subtitle = safe_relative_path(
            payload.get("subtitle", ""), SUBTITLE_EXTENSIONS, "Subtitle"
        )
        output = safe_output_name(payload.get("output"), f"{video.stem}_subbed.mkv")
        ffmpeg = cut.resolve_tool("ffmpeg")
        cut.add_subtitle(ffmpeg, str(video), str(subtitle), str(output))
        self.send_json(
            {
                "ok": True,
                "message": f"Subtitle merged: {output.name}",
                "download": f"/download/{output.name}",
            }
        )

    def handle_upload_subtitle(self) -> None:
        destination = upload_file(self, "subtitle", SUBTITLE_EXTENSIONS)
        self.send_json(
            {
                "ok": True,
                "message": f"Uploaded subtitle: {destination.relative_to(ROOT)}",
                "file": str(destination.relative_to(ROOT)),
            }
        )

    def handle_upload_video(self) -> None:
        destination = upload_file(self, "video", VIDEO_EXTENSIONS)
        self.send_json(
            {
                "ok": True,
                "message": f"Uploaded video: {destination.relative_to(ROOT)}",
                "file": str(destination.relative_to(ROOT)),
            }
        )

    def handle_process_start(self) -> None:
        payload = self.read_json()
        video = safe_relative_path(payload.get("video", ""), VIDEO_EXTENSIONS, "Video")
        subtitle_value = str(payload.get("subtitle", "")).strip()
        subtitle = None
        if subtitle_value:
            subtitle = safe_relative_path(subtitle_value, SUBTITLE_EXTENSIONS, "Subtitle")

        timestamp_ranges = parse_timestamp_ranges(payload.get("timestamps", []))
        ffprobe = cut.resolve_tool("ffprobe")
        summary = inspect_summary(ffprobe, video)
        estimate = estimate_process_seconds(video, summary, timestamp_ranges)
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "state": "queued",
            "state_label": "Queued",
            "stage": "Queued",
            "progress": 0,
            "progress_text": "Waiting to start",
            "operations": [],
            "error": "",
            "download": "",
            "file": "",
            "estimate_seconds": estimate,
            "cancel_event": threading.Event(),
            "process": None,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job

        def runner() -> None:
            try:
                update_job(
                    job_id,
                    state="running",
                    state_label="Running",
                    stage="Starting",
                    progress=5,
                    progress_text="Preparing workflow",
                )
                result = process_video_workflow(job_id, video, subtitle, timestamp_ranges)
                output = result["output"]
                update_job(
                    job_id,
                    state="success",
                    state_label="Completed",
                    stage="Completed",
                    progress=100,
                    progress_text="All operations completed",
                    download=f"/download/{output.name}",
                    file=output.name,
                )
            except RuntimeError as error:
                update_job(
                    job_id,
                    state="stopped",
                    state_label="Stopped",
                    stage="Stopped",
                    error=str(error),
                    progress_text="Processing stopped",
                )
            except Exception as error:
                update_job(
                    job_id,
                    state="error",
                    state_label="Error",
                    stage="Error",
                    error=str(error),
                    progress_text="Processing failed",
                )

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        self.send_json(
            {
                "ok": True,
                "job_id": job_id,
                "estimate_seconds": estimate,
            }
        )

    def handle_process_status(self, query_string: str) -> None:
        job_id = parse_qs(query_string).get("id", [""])[0]
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                raise ValueError("Process job was not found.")
            payload = {
                "ok": True,
                "state": job["state"],
                "state_label": job["state_label"],
                "stage": job["stage"],
                "progress": job["progress"],
                "progress_text": job["progress_text"],
                "operations": list(job["operations"]),
                "error": job["error"],
                "download": job["download"],
                "file": job["file"],
                "estimate_seconds": job["estimate_seconds"],
            }
        self.send_json(payload)

    def handle_process_stop(self) -> None:
        payload = self.read_json()
        job_id = str(payload.get("job_id", "")).strip()
        request_job_stop(job_id)
        self.send_json({"ok": True, "message": "Stop requested."})

    def serve_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_size = path.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = file_size - 1
        status = HTTPStatus.OK

        if range_header and range_header.startswith("bytes="):
            range_value = range_header.removeprefix("bytes=").split(",", 1)[0]
            start_text, _, end_text = range_value.partition("-")
            if start_text:
                start = int(start_text)
            if end_text:
                end = min(int(end_text), file_size - 1)
            status = HTTPStatus.PARTIAL_CONTENT

        if start < 0 or end >= file_size or start > end:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return

        chunk_length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(chunk_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()

        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = chunk_length
                while remaining > 0:
                    data = source.read(min(64 * 1024, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def serve_download(self, path: Path) -> None:
        path = path.resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path.suffix.lower() not in VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_file(path)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    port = 8787
    while True:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), VideoToolHandler)
            break
        except OSError as error:
            if error.errno != 98:
                raise
            port += 1

    print(f"Video tool frontend running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
