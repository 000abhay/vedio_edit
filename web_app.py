import contextlib
import tempfile
import io
import json
import mimetypes
import os
import queue
import re
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
H264_VIDEO_PRESET = "veryfast"
H264_VIDEO_CRF = "24"
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


def auto_output_path(video: Path) -> Path:
    return ROOT / f"{cut.short_video_prefix(video)}_updated.mkv"


def auto_output_regex(video: Path) -> re.Pattern[str]:
    prefix = re.escape(cut.short_video_prefix(video))
    return re.compile(rf"^{prefix}_(?:updated|project\d+|tv\d+|sub\d+)\.mkv$", re.IGNORECASE)


def cleanup_auto_outputs(video: Path, keep: Path) -> None:
    output_pattern = auto_output_regex(video)
    for path in ROOT.iterdir():
        if not path.is_file() or path == keep:
            continue
        if output_pattern.fullmatch(path.name):
            path.unlink()


def temp_output_path(output: Path) -> Path:
    return output.with_suffix(f".tmp{output.suffix}")


def replace_output_file(source: Path, destination: Path) -> None:
    temporary_output = temp_output_path(destination)
    if temporary_output.exists():
        temporary_output.unlink()
    if source.resolve() == destination.resolve():
        return
    shutil.copy2(source, temporary_output)
    temporary_output.replace(destination)


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
                "detail": "Not needed" if video_ok else "Will convert to H.264 with full re-encode",
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
                "ok": True,
                "detail": "Enabled in web workflow. Timestamp cuts are re-encoded for better accuracy.",
            },
        ],
    }


def subtitle_stream_infos(ffprobe: str, video: Path) -> list[dict]:
    data = cut.probe_media(ffprobe, video)
    streams = data.get("streams") or []
    subtitle_infos: list[dict] = []
    subtitle_index = 0
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        codec_name = str(stream.get("codec_name") or "").lower()
        subtitle_infos.append(
            {
                "ffmpeg_index": subtitle_index,
                "codec_name": codec_name,
                "language": str(tags.get("language") or "").strip(),
                "title": str(tags.get("title") or "").strip(),
                "default": bool(disposition.get("default")),
                "forced": bool(disposition.get("forced")),
                "text_based": codec_name in cut.TEXT_SUBTITLE_CODECS,
            }
        )
        subtitle_index += 1
    return subtitle_infos


def parse_srt_timestamp(value: str) -> float:
    return cut.parse_timestamp(value.strip().replace(",", "."))


def format_srt_timestamp(value: float) -> str:
    total_milliseconds = max(0, round(value * 1000))
    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    seconds = (total_milliseconds % 60_000) // 1000
    milliseconds = total_milliseconds % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def parse_srt_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    for raw_block in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        lines = raw_block.split("\n")
        if not any(line.strip() for line in lines):
            continue

        timing_index = None
        for index, line in enumerate(lines):
            if "-->" in line:
                timing_index = index
                break
        if timing_index is None:
            continue

        timing_line = lines[timing_index]
        start_text, _, end_text = timing_line.partition("-->")
        start_value = start_text.strip().split()[0]
        end_value = end_text.strip().split()[0]
        blocks.append(
            {
                "start": parse_srt_timestamp(start_value),
                "end": parse_srt_timestamp(end_value),
                "text_lines": lines[timing_index + 1 :],
            }
        )
    return blocks


def render_srt_blocks(blocks: list[dict]) -> str:
    rows: list[str] = []
    for index, block in enumerate(blocks, start=1):
        rows.append(str(index))
        rows.append(f"{format_srt_timestamp(block['start'])} --> {format_srt_timestamp(block['end'])}")
        rows.extend(block["text_lines"] or [""])
        rows.append("")
    return "\n".join(rows).strip() + ("\n" if rows else "")


def retime_srt_text(text: str, keep_segments: list[tuple[float, float]]) -> str:
    blocks = parse_srt_blocks(text)
    if not blocks:
        return ""

    segment_offsets: list[tuple[float, float, float]] = []
    output_offset = 0.0
    for start, end in keep_segments:
        segment_offsets.append((start, end, output_offset))
        output_offset += end - start

    shifted_blocks: list[dict] = []
    for block in blocks:
        for keep_start, keep_end, offset in segment_offsets:
            overlap_start = max(block["start"], keep_start)
            overlap_end = min(block["end"], keep_end)
            if overlap_end <= overlap_start:
                continue
            shifted_blocks.append(
                {
                    "start": offset + (overlap_start - keep_start),
                    "end": offset + (overlap_end - keep_start),
                    "text_lines": list(block["text_lines"]),
                }
            )
    shifted_blocks.sort(key=lambda item: (item["start"], item["end"]))
    return render_srt_blocks(shifted_blocks)


def next_output_path(video: Path) -> Path:
    return auto_output_path(video)


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
    video_codec_ok = next(
        item["ok"] for item in summary["light"] if item["label"] == "TV-friendly video codec"
    )
    if not next(item["ok"] for item in summary["light"] if item["label"] == "TV-friendly audio codec"):
        estimate += 18
    subtitle_ok = next(item["ok"] for item in summary["light"] if item["label"] == "English subtitles")
    if not subtitle_ok:
        estimate += 6
    if not video_codec_ok and not timestamp_ranges:
        estimate += max(60, int(size_mib / 6))
    if timestamp_ranges:
        estimate += max(20, int(size_mib / 12))
        estimate += len(timestamp_ranges) * 10
    return max(10, estimate)


FFMPEG_PROGRESS_KEYS = {
    "bitrate",
    "drop_frames",
    "dup_frames",
    "fps",
    "frame",
    "out_time",
    "out_time_ms",
    "out_time_us",
    "progress",
    "speed",
    "stream_0_0_q",
    "total_size",
}


def ffmpeg_progress_command(command: list[str]) -> list[str]:
    if "-progress" in command:
        return command
    insert_at = 1
    if "-hide_banner" in command:
        insert_at = command.index("-hide_banner") + 1
    return command[:insert_at] + ["-nostats", "-progress", "pipe:2"] + command[insert_at:]


def is_ffmpeg_progress_line(line: str) -> bool:
    key = line.split("=", 1)[0].strip()
    return key in FFMPEG_PROGRESS_KEYS or (key.startswith("stream_") and key.endswith("_q"))


def parse_ffmpeg_time(value: str) -> float | None:
    value = value.strip()
    if not value or value.upper() == "N/A":
        return None
    if re.fullmatch(r"-?\d+", value):
        return max(0.0, int(value) / 1_000_000)
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})(?:\.(\d+))?", value)
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        total += float(f"0.{fraction}")
    return max(0.0, total)


def progress_from_ffmpeg_line(line: str) -> float | None:
    if "=" not in line:
        return None
    key, value = line.strip().split("=", 1)
    if key == "out_time_us" or key == "out_time_ms":
        return parse_ffmpeg_time(value)
    if key == "out_time":
        return parse_ffmpeg_time(value)
    return None


def update_job_progress(job_id: str, progress: float) -> None:
    progress = max(0, min(100, int(round(progress))))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["progress"] = max(int(job.get("progress", 0)), progress)


def job_timing_snapshot(job: dict) -> tuple[int, int | None]:
    now = time.time()
    started_at = job.get("started_at")
    finished_at = job.get("finished_at")
    if not started_at:
        return 0, job.get("estimate_seconds")

    end_at = finished_at or now
    elapsed = max(0, int(round(end_at - started_at)))
    state = job.get("state")
    if state in {"success", "error", "stopped"}:
        return elapsed, 0

    progress = max(0, min(100, float(job.get("progress") or 0)))
    if progress >= 1:
        remaining = int(round(elapsed * ((100 - progress) / progress)))
    else:
        remaining = int(job.get("estimate_seconds") or 0) - elapsed
    return elapsed, max(0, remaining)


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


def run_command_for_job(
    job_id: str,
    command: list[str],
    progress_range: tuple[int, int] | None = None,
    duration_seconds: float | None = None,
) -> None:
    command_to_run = ffmpeg_progress_command(command) if progress_range and duration_seconds else command
    process = subprocess.Popen(
        command_to_run,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    update_job(job_id, process=process)
    stderr_queue: queue.Queue[str] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stderr() -> None:
        if not process.stderr:
            return
        for line in process.stderr:
            stderr_queue.put(line.rstrip())

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    def drain_stderr() -> None:
        while True:
            try:
                line = stderr_queue.get_nowait()
            except queue.Empty:
                return
            if not line:
                continue
            stderr_lines.append(line)
            elapsed_media_seconds = progress_from_ffmpeg_line(line)
            if (
                elapsed_media_seconds is not None
                and progress_range
                and duration_seconds
                and duration_seconds > 0
            ):
                start, end = progress_range
                fraction = min(1.0, elapsed_media_seconds / duration_seconds)
                update_job_progress(job_id, start + ((end - start) * fraction))

    try:
        while True:
            drain_stderr()
            if JOBS[job_id]["cancel_event"].is_set():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RuntimeError("Processing stopped by user.")
            code = process.poll()
            if code is not None:
                stderr_thread.join(timeout=0.5)
                drain_stderr()
                if code != 0:
                    stderr = "\n".join(
                        line for line in stderr_lines if not is_ffmpeg_progress_line(line)
                    ).strip()
                    raise ValueError(stderr or f"Command failed with exit code {code}.")
                if progress_range:
                    update_job_progress(job_id, progress_range[1])
                return
            time.sleep(0.2)
    finally:
        update_job(job_id, process=None)


def remux_to_mkv_job(
    job_id: str,
    ffmpeg: str,
    ffprobe: str,
    video: Path,
    output_path: Path,
) -> None:
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
    run_command_for_job(
        job_id,
        command,
        progress_range=(5, 20),
        duration_seconds=cut.probe_duration(ffprobe, video),
    )


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
    run_command_for_job(
        job_id,
        command,
        progress_range=(5, 20),
        duration_seconds=cut.probe_duration(ffprobe, video),
    )


def convert_video_to_h264_job(
    job_id: str,
    ffmpeg: str,
    ffprobe: str,
    video: Path,
    output_path: Path,
) -> None:
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
        "libx264",
        "-preset",
        H264_VIDEO_PRESET,
        "-crf",
        H264_VIDEO_CRF,
        "-pix_fmt",
        "yuv420p",
    ]
    if cut.has_audio_stream(ffprobe, video):
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k", "-ac:a", "2"])
    else:
        command.extend(["-an"])
    command.extend(["-map", "0:s?", "-c:s", "copy", str(output_path)])
    run_command_for_job(
        job_id,
        command,
        progress_range=(5, 84),
        duration_seconds=cut.probe_duration(ffprobe, video),
    )


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


def create_precise_cut_video_job(
    job_id: str,
    ffmpeg: str,
    video: Path,
    output_path: Path,
    keep_segments: list[tuple[float, float]],
    include_audio: bool,
) -> None:
    filter_complex = cut.build_filter_complex(keep_segments, include_audio)
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
    ]
    if include_audio:
        command.extend(["-map", "[outa]"])
    command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"])
    if include_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-sn", str(output_path)])
    output_duration = sum(end - start for start, end in keep_segments)
    run_command_for_job(
        job_id,
        command,
        progress_range=(25, 84),
        duration_seconds=output_duration,
    )


def extract_subtitle_stream_job(
    job_id: str,
    ffmpeg: str,
    video: Path,
    stream_index: int,
    output_path: Path,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-map",
        f"0:s:{stream_index}",
        "-c:s",
        "srt",
        str(output_path),
    ]
    run_command_for_job(job_id, command)


def rebuild_subtitles_after_cut_job(
    job_id: str,
    ffmpeg: str,
    video: Path,
    subtitle_tracks: list[dict],
    output_path: Path,
    duration_seconds: float,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
    ]
    for track in subtitle_tracks:
        command.extend(["-i", str(track["path"])])
    command.extend(["-map", "0"])
    for input_index in range(1, len(subtitle_tracks) + 1):
        command.extend(["-map", f"{input_index}:0"])
    command.extend(["-c", "copy", "-c:s", "srt"])

    for subtitle_index, track in enumerate(subtitle_tracks):
        language = str(track.get("language") or "").strip()
        title = str(track.get("title") or "").strip()
        disposition_flags: list[str] = []
        if track.get("default"):
            disposition_flags.append("default")
        if track.get("forced"):
            disposition_flags.append("forced")
        if language:
            command.extend([f"-metadata:s:s:{subtitle_index}", f"language={language}"])
        if title:
            command.extend([f"-metadata:s:s:{subtitle_index}", f"title={title}"])
        if disposition_flags:
            command.extend([f"-disposition:s:{subtitle_index}", "+".join(disposition_flags)])

    command.append(str(output_path))
    run_command_for_job(
        job_id,
        command,
        progress_range=(84, 88),
        duration_seconds=duration_seconds,
    )


def prepare_shifted_subtitle_tracks(
    job_id: str,
    ffmpeg: str,
    ffprobe: str,
    video: Path,
    keep_segments: list[tuple[float, float]],
    temp_dir: Path,
) -> tuple[list[dict], list[str]]:
    tracks: list[dict] = []
    dropped_codecs: list[str] = []
    for info in subtitle_stream_infos(ffprobe, video):
        if not info["text_based"]:
            dropped_codecs.append(info["codec_name"] or "unknown")
            continue

        extracted_path = temp_dir / f"subtitle_{info['ffmpeg_index']:02d}.srt"
        shifted_path = temp_dir / f"subtitle_{info['ffmpeg_index']:02d}_shifted.srt"
        extract_subtitle_stream_job(job_id, ffmpeg, video, info["ffmpeg_index"], extracted_path)
        text = extracted_path.read_text(encoding="utf-8-sig", errors="replace")
        shifted_text = retime_srt_text(text, keep_segments)
        if not shifted_text.strip():
            continue
        shifted_path.write_text(shifted_text, encoding="utf-8")
        tracks.append(
            {
                "path": shifted_path,
                "language": info["language"],
                "title": info["title"],
                "default": info["default"],
                "forced": info["forced"],
            }
        )
    return tracks, dropped_codecs


def add_subtitle_job(
    job_id: str,
    ffmpeg: str,
    ffprobe: str,
    video: Path,
    subtitle: Path,
    output_path: Path,
) -> None:
    existing_subtitle_count = len(subtitle_stream_infos(ffprobe, video))
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
        f"-metadata:s:s:{existing_subtitle_count}",
        "language=eng",
        str(output_path),
    ]
    run_command_for_job(
        job_id,
        command,
        progress_range=(88, 95),
        duration_seconds=cut.probe_duration(ffprobe, video),
    )


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
    video_codec_detail = next(
        item["detail"] for item in summary["light"] if item["label"] == "TV-friendly video codec"
    )
    audio_ok = next(
        item["ok"] for item in summary["light"] if item["label"] == "TV-friendly audio codec"
    )

    with tempfile.TemporaryDirectory(prefix="web-video-process-", dir=ROOT) as temp_dir_name:
        temp_dir = Path(temp_dir_name)

        if not video_ok and not timestamp_ranges:
            update_job(
                job_id,
                stage="Converting video",
                progress=5,
                progress_text="Converting video to H.264",
            )
            converted = temp_dir / f"{current_video.stem}_h264.mkv"
            convert_video_to_h264_job(job_id, ffmpeg, ffprobe, current_video, converted)
            current_video = converted
            summary = inspect_summary(ffprobe, current_video)
            audio_ok = next(
                item["ok"] for item in summary["light"] if item["label"] == "TV-friendly audio codec"
            )
            operations.append(
                f"Video converted to H.264 for TV playback ({video_codec_detail} source, {H264_VIDEO_PRESET}, CRF {H264_VIDEO_CRF})."
            )
            append_job_operation(job_id, operations[-1])
        elif not video_ok:
            operations.append(
                f"Video codec ({video_codec_detail}) will be converted to H.264 during timestamp cuts."
            )
            append_job_operation(job_id, operations[-1])

        if not audio_ok:
            update_job(job_id, stage="Converting audio", progress=5, progress_text="Preparing AAC audio")
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
                update_job(job_id, stage="Converting container", progress=5, progress_text="Preparing MKV container")
                remuxed = temp_dir / f"{current_video.stem}_remux.mkv"
                remux_to_mkv_job(job_id, ffmpeg, ffprobe, current_video, remuxed)
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
            cut_output_duration = sum(end - start for start, end in keep_segments)
            preserved_subtitle_tracks, dropped_subtitle_codecs = prepare_shifted_subtitle_tracks(
                job_id, ffmpeg, ffprobe, current_video, keep_segments, temp_dir
            )
            cut_output = temp_dir / f"{current_video.stem}_cut.mkv"
            update_job(
                job_id,
                stage="Cutting timestamps",
                progress=25,
                progress_text="Re-encoding exact timestamp cuts",
            )
            create_precise_cut_video_job(job_id, ffmpeg, current_video, cut_output, keep_segments, include_audio)
            current_video = cut_output
            operations.append(
                f"Timestamp cuts applied accurately with re-encode for {len(timestamp_ranges)} range(s)."
            )
            append_job_operation(job_id, operations[-1])
            if preserved_subtitle_tracks:
                update_job(
                    job_id,
                    stage="Rebuilding subtitles",
                    progress=84,
                    progress_text="Retiming existing subtitle tracks",
                )
                subtitle_output = temp_dir / f"{current_video.stem}_subbed_existing.mkv"
                rebuild_subtitles_after_cut_job(
                    job_id,
                    ffmpeg,
                    current_video,
                    preserved_subtitle_tracks,
                    subtitle_output,
                    cut_output_duration,
                )
                current_video = subtitle_output
                operations.append(
                    f"Preserved {len(preserved_subtitle_tracks)} existing subtitle track(s) after timestamp cuts."
                )
                append_job_operation(job_id, operations[-1])
            if dropped_subtitle_codecs:
                skipped = ", ".join(sorted(set(dropped_subtitle_codecs)))
                operations.append(
                    f"Skipped unsupported embedded subtitle track(s) during timestamp cuts: {skipped}."
                )
                append_job_operation(job_id, operations[-1])

        if subtitle is not None:
            update_job(job_id, stage="Merging subtitle", progress=88, progress_text="Adding subtitle track")
            sub_output = temp_dir / f"{current_video.stem}_subbed.mkv"
            add_subtitle_job(job_id, ffmpeg, ffprobe, current_video, subtitle, sub_output)
            current_video = sub_output
            operations.append(f"English subtitle merged successfully from {subtitle.name}.")
            append_job_operation(job_id, operations[-1])

        update_job(job_id, stage="Finalizing output", progress=95, progress_text="Saving final file")
        final_output = next_output_path(video)
        replace_output_file(current_video, final_output)
        cleanup_auto_outputs(video, final_output)
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
        output = safe_output_name(payload.get("output"), auto_output_path(video).name)
        padding = float(payload.get("padding", cut.DEFAULT_CUT_PADDING_SECONDS))
        if padding < 0 or padding > 30:
            raise ValueError("Padding must be between 0 and 30 seconds.")

        ffmpeg = cut.resolve_tool("ffmpeg")
        ffprobe = cut.resolve_tool("ffprobe")
        duration = cut.probe_duration(ffprobe, video)
        include_audio = cut.has_audio_stream(ffprobe, video)
        keep_segments = cut.build_keep_segments(duration, padding)
        temp_output = temp_output_path(output)
        if temp_output.exists():
            temp_output.unlink()

        cut.create_copy_cut_video(ffmpeg, video, temp_output, keep_segments, include_audio)
        temp_output.replace(output)
        cleanup_auto_outputs(video, output)
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
        output = safe_output_name(payload.get("output"), auto_output_path(video).name)
        ffmpeg = cut.resolve_tool("ffmpeg")
        temp_output = temp_output_path(output)
        if temp_output.exists():
            temp_output.unlink()
        cut.remux_to_mkv(ffmpeg, video, str(temp_output))
        temp_output.replace(output)
        cleanup_auto_outputs(video, output)
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
        output = safe_output_name(payload.get("output"), auto_output_path(video).name)
        ffmpeg = cut.resolve_tool("ffmpeg")
        temp_output = temp_output_path(output)
        if temp_output.exists():
            temp_output.unlink()
        cut.add_subtitle(ffmpeg, str(video), str(subtitle), str(temp_output))
        temp_output.replace(output)
        cleanup_auto_outputs(video, output)
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
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": 0,
            "remaining_seconds": estimate,
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
                    started_at=time.time(),
                    finished_at=None,
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
                    finished_at=time.time(),
                )
            except RuntimeError as error:
                update_job(
                    job_id,
                    state="stopped",
                    state_label="Stopped",
                    stage="Stopped",
                    error=str(error),
                    progress_text="Processing stopped",
                    finished_at=time.time(),
                )
            except Exception as error:
                update_job(
                    job_id,
                    state="error",
                    state_label="Error",
                    stage="Error",
                    error=str(error),
                    progress_text="Processing failed",
                    finished_at=time.time(),
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
            elapsed_seconds, remaining_seconds = job_timing_snapshot(job)
            job["elapsed_seconds"] = elapsed_seconds
            job["remaining_seconds"] = remaining_seconds
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
                "elapsed_seconds": elapsed_seconds,
                "remaining_seconds": remaining_seconds,
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
