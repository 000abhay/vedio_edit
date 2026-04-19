import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PureWindowsPath


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_VIDEO = Path(
    r"C:\Users\abhay\Downloads\Telegram Desktop\Wolf.Creek.2.2013.720p.BluRay.@GrandCinemas.mkv"
)
LOCAL_DEFAULT_VIDEO = SCRIPT_DIR / PureWindowsPath(str(DEFAULT_VIDEO)).name
DEFAULT_OUTPUT_NAME = "wolf_clean.mkv"
DEFAULT_CUT_PADDING_SECONDS = 5.0
LIGHT_COMMANDS = {"cut", "inspect", "to-mkv", "add-sub"}
TV_FRIENDLY_VIDEO_CODECS = {"h264", "mpeg4", "mpeg2video"}
TV_FRIENDLY_AUDIO_CODECS = {"aac", "ac3", "mp3"}
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}

CUT_SEGMENTS = [
    ("00:12:08", "00:12:29"),
    ("00:16:39", "00:21:35"),
    ("00:22:44", "00:23:03"),
    ("00:28:06", "00:29:39"),
    ("00:32:05", "00:32:16"),
    ("00:32:19", "00:32:37"),
]


def parse_timestamp(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 1:
        return float(parts[0])
    raise ValueError(f"Invalid timestamp: {value}")


def format_seconds(value: float) -> str:
    total_milliseconds = round(value * 1000)
    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    seconds = (total_milliseconds % 60_000) / 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def filter_seconds(value: float) -> str:
    return f"{value:.3f}"


def resolve_tool(name: str) -> str:
    tool = shutil.which(name)
    if tool:
        return tool

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path.home() / "ffmpeg-bin" / f"{name}.exe",
        Path(local_app_data)
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-8.1-essentials_build"
        / "bin"
        / f"{name}.exe",
        Path(local_app_data)
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-8.1-full_build"
        / "bin"
        / f"{name}.exe",
    ]

    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except PermissionError:
            continue

    raise FileNotFoundError(
        f"{name} was not found. Open a new PowerShell window and run '{name} -version'."
    )


def probe_duration(ffprobe: str, video: Path) -> float:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def probe_media(ffprobe: str, video: Path) -> dict:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def has_audio_stream(ffprobe: str, video: Path) -> bool:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return bool(result.stdout.strip())


def resolve_path(path_arg: str) -> Path:
    path = Path(path_arg).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def resolve_existing_file(path_arg: str, label: str = "File") -> Path:
    path = resolve_path(path_arg)
    if path.is_file():
        return path
    raise FileNotFoundError(f"{label} not found: {path}")


def resolve_video(video_arg: str | None) -> Path:
    if video_arg:
        return resolve_existing_file(video_arg, "Video file")

    if DEFAULT_VIDEO.is_file():
        return DEFAULT_VIDEO

    if LOCAL_DEFAULT_VIDEO.is_file():
        return LOCAL_DEFAULT_VIDEO

    raise FileNotFoundError(
        "Video file not found. Pass the path explicitly, for example:\n"
        'python cut.py "C:\\path\\to\\your-video.mkv"'
    )


def build_keep_segments_from_cuts(
    duration: float,
    cuts_to_remove: list[tuple[str, str]] | list[tuple[float, float]],
    cut_padding: float = 0.0,
) -> list[tuple[float, float]]:
    cuts: list[tuple[float, float]] = []
    for start, end in cuts_to_remove:
        if isinstance(start, str):
            start_value = parse_timestamp(start)
        else:
            start_value = start
        if isinstance(end, str):
            end_value = parse_timestamp(end)
        else:
            end_value = end
        padded_start = max(0.0, start_value - cut_padding)
        padded_end = min(duration, end_value + cut_padding)
        if padded_start >= duration or padded_end <= 0:
            continue
        cuts.append((padded_start, padded_end))

    cuts.sort()

    merged: list[tuple[float, float]] = []
    for start, end in cuts:
        if end <= start:
            raise ValueError(f"Invalid cut segment: {start} -> {end}")

        if not merged:
            merged.append((start, end))
            continue

        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    keep_segments: list[tuple[float, float]] = []
    current = 0.0
    for start, end in merged:
        if start > duration:
            break
        clipped_start = max(0.0, min(start, duration))
        clipped_end = max(0.0, min(end, duration))
        if clipped_start > current:
            keep_segments.append((current, clipped_start))
        current = max(current, clipped_end)

    if current < duration:
        keep_segments.append((current, duration))

    if not keep_segments:
        raise ValueError("The cut segments remove the entire video.")

    return keep_segments


def build_keep_segments(duration: float, cut_padding: float = 0.0) -> list[tuple[float, float]]:
    return build_keep_segments_from_cuts(duration, CUT_SEGMENTS, cut_padding)


def expected_duration(keep_segments: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in keep_segments)


def build_filter_complex(keep_segments: list[tuple[float, float]], include_audio: bool) -> str:
    parts: list[str] = []

    for index, (start, end) in enumerate(keep_segments):
        parts.append(
            f"[0:v:0]trim=start={filter_seconds(start)}:end={filter_seconds(end)},setpts=PTS-STARTPTS[v{index}]"
        )
        if include_audio:
            parts.append(
                f"[0:a:0]atrim=start={filter_seconds(start)}:end={filter_seconds(end)},asetpts=PTS-STARTPTS[a{index}]"
            )

    concat_inputs: list[str] = []
    for index in range(len(keep_segments)):
        concat_inputs.append(f"[v{index}]")
        if include_audio:
            concat_inputs.append(f"[a{index}]")

    if include_audio:
        parts.append(
            "".join(concat_inputs) + f"concat=n={len(keep_segments)}:v=1:a=1[outv][outa]"
        )
    else:
        parts.append(
            "".join(concat_inputs) + f"concat=n={len(keep_segments)}:v=1:a=0[outv]"
        )

    return ";".join(parts)


def quote_concat_path(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def default_output_path(video: Path) -> Path:
    return Path.cwd() / DEFAULT_OUTPUT_NAME


def short_video_prefix(video: Path) -> str:
    prefix = video.stem[:10]
    prefix = re.sub(r"[^A-Za-z0-9]+", "_", prefix).strip("_")
    return prefix or "video"


def numbered_output_path(video: Path, tag: str, directory: Path | None = None) -> Path:
    base_dir = directory or Path.cwd()
    prefix = short_video_prefix(video)
    counter = 1
    while True:
        candidate = base_dir / f"{prefix}_{tag}{counter}.mkv"
        if not candidate.exists():
            return candidate
        counter += 1


def default_remux_output_path(video: Path) -> Path:
    return numbered_output_path(video, "tv")


def default_subtitle_output_path(video: Path) -> Path:
    return numbered_output_path(video, "sub")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lightweight video tools: cut scenes, inspect files, remux to MKV, and merge subtitles."
    )
    parser.add_argument(
        "items",
        nargs="*",
        help=(
            "Optional command/path. Commands: cut, inspect, to-mkv, add-sub. "
            "Default command is cut."
        ),
    )
    parser.add_argument(
        "--output",
        help="Optional path for the final edited video.",
    )
    parser.add_argument(
        "--mode",
        default="copy",
        choices=["copy", "reencode"],
        help="Use copy for fast cuts, or reencode for exact frame cuts.",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=DEFAULT_CUT_PADDING_SECONDS,
        help="Extra seconds removed before and after each cut range.",
    )
    parser.add_argument(
        "--preset",
        default="ultrafast",
        choices=[
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
        ],
        help="FFmpeg x264 speed preset. Faster presets render faster but may create larger files.",
    )
    parser.add_argument(
        "--crf",
        default="23",
        help="Video quality value. Lower means better quality/larger file; 18-23 is a common range.",
    )
    args = parser.parse_args()
    normalize_command_args(args)
    return args


def normalize_command_args(args: argparse.Namespace) -> None:
    items = args.items
    if items and items[0] in LIGHT_COMMANDS:
        args.command = items[0]
        command_items = items[1:]
    else:
        args.command = "cut"
        command_items = items

    args.video = None
    args.subtitle = None

    if args.command in {"cut", "inspect", "to-mkv"}:
        args.video = " ".join(command_items) if command_items else None
        return

    if args.command == "add-sub":
        if len(command_items) < 2:
            raise SystemExit("Error: add-sub needs a video path and a subtitle path.")
        if len(command_items) > 2:
            raise SystemExit(
                "Error: add-sub needs exactly two paths. Quote paths that contain spaces."
            )
        args.video, args.subtitle = command_items


def main() -> None:
    args = parse_args()
    try:
        ffmpeg = resolve_tool("ffmpeg")
        ffprobe = resolve_tool("ffprobe")
        if args.command == "inspect":
            inspect_video(ffprobe, resolve_video(args.video))
            return
        if args.command == "to-mkv":
            remux_to_mkv(ffmpeg, resolve_video(args.video), args.output)
            return
        if args.command == "add-sub":
            add_subtitle(ffmpeg, args.video, args.subtitle, args.output)
            return

        video = resolve_video(args.video)
        duration = probe_duration(ffprobe, video)
        include_audio = has_audio_stream(ffprobe, video)
        keep_segments = build_keep_segments(duration, args.padding)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as error:
        raise SystemExit(f"Error: {error}") from error
    output_path = Path(args.output).expanduser() if args.output else default_output_path(video)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    print(f"Using FFmpeg: {ffmpeg}")
    print(f"Using video: {video}")
    print(f"Final output: {output_path}")
    print(f"Mode: {args.mode}")
    print(f"Cut padding: {args.padding:.3f} seconds on each side")
    print("Removing these ranges:")
    for start, end in CUT_SEGMENTS:
        print(f"  {start} -> {end}")
    print("Keeping these ranges:")
    for start, end in keep_segments:
        print(f"  {format_seconds(start)} -> {format_seconds(end)}")
    target_duration = expected_duration(keep_segments)
    print(f"Expected final duration: {format_seconds(target_duration)}")

    temp_output_path = output_path.with_suffix(f".tmp{output_path.suffix}")
    if temp_output_path.exists():
        temp_output_path.unlink()

    if args.mode == "copy":
        create_copy_cut_video(
            ffmpeg=ffmpeg,
            video=video,
            output_path=temp_output_path,
            keep_segments=keep_segments,
            include_audio=include_audio,
        )
    else:
        create_reencoded_video(
            ffmpeg=ffmpeg,
            video=video,
            output_path=temp_output_path,
            keep_segments=keep_segments,
            include_audio=include_audio,
            preset=args.preset,
            crf=args.crf,
        )

    actual_duration = probe_duration(ffprobe, temp_output_path)
    allowed_duration_difference = 60 if args.mode == "copy" else 2
    if abs(actual_duration - target_duration) > allowed_duration_difference:
        raise SystemExit(
            "Error: FFmpeg output duration did not match the expected duration.\n"
            f"Expected: {format_seconds(target_duration)}\n"
            f"Actual:   {format_seconds(actual_duration)}\n"
            f"Partial output left at: {temp_output_path}"
        )

    temp_output_path.replace(output_path)

    print("")
    print(f"Final edited video created: {output_path}")


def format_size(size_text: str | None) -> str:
    if not size_text:
        return "unknown"
    size = int(size_text)
    mib = size / 1024 / 1024
    return f"{mib:.1f} MiB ({size} bytes)"


def stream_language(stream: dict) -> str:
    tags = stream.get("tags") or {}
    return tags.get("language", "unknown")


def describe_stream(index: int, stream: dict) -> str:
    codec = stream.get("codec_name", "unknown")
    language = stream_language(stream)
    stream_type = stream.get("codec_type", "unknown")
    if stream_type == "video":
        width = stream.get("width", "?")
        height = stream.get("height", "?")
        profile = stream.get("profile", "unknown")
        pix_fmt = stream.get("pix_fmt", "unknown")
        return (
            f"#{index}: {codec}, {width}x{height}, profile={profile}, "
            f"pixel_format={pix_fmt}, language={language}"
        )
    if stream_type == "audio":
        channels = stream.get("channels", "?")
        sample_rate = stream.get("sample_rate", "unknown")
        return (
            f"#{index}: {codec}, channels={channels}, sample_rate={sample_rate}, "
            f"language={language}"
        )
    if stream_type == "subtitle":
        return f"#{index}: {codec}, language={language}"
    return f"#{index}: {codec}, language={language}"


def is_attached_picture(stream: dict) -> bool:
    disposition = stream.get("disposition") or {}
    return bool(disposition.get("attached_pic"))


def inspect_video(ffprobe: str, video: Path) -> None:
    data = probe_media(ffprobe, video)
    media_format = data.get("format") or {}
    streams = data.get("streams") or []
    video_streams = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video" and not is_attached_picture(stream)
    ]
    attached_pictures = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video" and is_attached_picture(stream)
    ]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]

    format_names = media_format.get("format_name", "unknown")
    duration = float(media_format.get("duration") or 0)

    print(f"File: {video}")
    print(f"Container: {format_names}")
    print(f"Duration: {format_seconds(duration)}")
    print(f"Size: {format_size(media_format.get('size'))}")
    print("")
    print("Video streams:")
    if video_streams:
        for stream in video_streams:
            print(f"  {describe_stream(stream.get('index', 0), stream)}")
    else:
        print("  none")
    if attached_pictures:
        print(f"  attached cover images: {len(attached_pictures)}")
    print("")
    print("Audio streams:")
    if audio_streams:
        for stream in audio_streams:
            print(f"  {describe_stream(stream.get('index', 0), stream)}")
    else:
        print("  none")
    print("")
    print("Subtitle streams:")
    if subtitle_streams:
        for stream in subtitle_streams:
            print(f"  {describe_stream(stream.get('index', 0), stream)}")
    else:
        print("  none")

    video_codecs = {stream.get("codec_name") for stream in video_streams}
    audio_codecs = {stream.get("codec_name") for stream in audio_streams}
    subtitle_codecs = {stream.get("codec_name") for stream in subtitle_streams}
    audio_languages = {stream_language(stream).lower() for stream in audio_streams}
    subtitle_languages = {stream_language(stream).lower() for stream in subtitle_streams}
    is_mkv = "matroska" in format_names or "webm" in format_names
    video_ok = bool(video_codecs) and video_codecs <= TV_FRIENDLY_VIDEO_CODECS
    audio_ok = not audio_codecs or audio_codecs <= TV_FRIENDLY_AUDIO_CODECS
    subtitle_ok = not subtitle_codecs or subtitle_codecs <= TEXT_SUBTITLE_CODECS
    has_hindi_audio = bool(audio_languages & {"hin", "hi", "hindi"})
    has_english_subtitle = bool(subtitle_languages & {"eng", "en", "english"})

    print("")
    print("TV readiness:")
    print(f"  MKV container: {'yes' if is_mkv else 'no, use to-mkv'}")
    print(f"  Video codec likely OK: {'yes' if video_ok else 'maybe no, may need heavy re-encode'}")
    print(f"  Audio codec likely OK: {'yes' if audio_ok else 'maybe no, convert audio only'}")
    print(f"  Subtitle codec likely OK: {'yes' if subtitle_ok else 'maybe no, convert to SRT'}")
    print(f"  Hindi audio found: {'yes' if has_hindi_audio else 'no/unknown'}")
    print(f"  English subtitles found: {'yes' if has_english_subtitle else 'no/unknown'}")


def remux_to_mkv(ffmpeg: str, video: Path, output_arg: str | None) -> None:
    output_path = resolve_path(output_arg) if output_arg else default_remux_output_path(video)
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
    subprocess.run(command, check=True)
    print(f"MKV created: {output_path}")


def convert_audio_to_aac_mkv(ffmpeg: str, video: Path, output_arg: str | None) -> None:
    output_path = resolve_path(output_arg) if output_arg else default_remux_output_path(video)
    ffprobe = resolve_tool("ffprobe")
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

    if has_audio_stream(ffprobe, video):
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"])
    else:
        command.extend(["-an"])

    command.extend(["-map", "0:s?", "-c:s", "copy", str(output_path)])
    subprocess.run(command, check=True)
    print(f"Audio converted: {output_path}")


def add_subtitle(
    ffmpeg: str,
    video_arg: str,
    subtitle_arg: str,
    output_arg: str | None,
) -> None:
    video = resolve_existing_file(video_arg, "Video file")
    subtitle = resolve_existing_file(subtitle_arg, "Subtitle file")
    output_path = resolve_path(output_arg) if output_arg else default_subtitle_output_path(video)
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
    subprocess.run(command, check=True)
    print(f"Subtitle merged: {output_path}")


def create_copy_cut_video(
    ffmpeg: str,
    video: Path,
    output_path: Path,
    keep_segments: list[tuple[float, float]],
    include_audio: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="video-cut-", dir=Path.cwd()) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        segment_paths: list[Path] = []

        for index, (start, end) in enumerate(keep_segments):
            segment_path = temp_dir / f"part_{index:03d}.mkv"
            segment_command = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                filter_seconds(start),
                "-to",
                filter_seconds(end),
                "-i",
                str(video),
                "-map",
                "0:v:0",
            ]
            if include_audio:
                segment_command.extend(["-map", "0:a:0"])
            segment_command.extend(
                [
                    "-c",
                    "copy",
                    "-avoid_negative_ts",
                    "make_zero",
                    str(segment_path),
                ]
            )
            subprocess.run(segment_command, check=True)
            segment_paths.append(segment_path)

        concat_list_path = temp_dir / "concat.txt"
        concat_list_path.write_text(
            "".join(f"file '{quote_concat_path(path)}'\n" for path in segment_paths),
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
        subprocess.run(concat_command, check=True)


def create_reencoded_video(
    ffmpeg: str,
    video: Path,
    output_path: Path,
    keep_segments: list[tuple[float, float]],
    include_audio: bool,
    preset: str,
    crf: str,
) -> None:
    filter_complex = build_filter_complex(keep_segments, include_audio)
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-i",
        str(video),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
    ]

    if include_audio:
        command.extend(["-map", "[outa]"])

    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            crf,
        ]
    )

    if include_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])

    command.extend(["-sn", str(output_path)])

    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Error: FFmpeg failed with exit code {error.returncode}.") from error


if __name__ == "__main__":
    main()
