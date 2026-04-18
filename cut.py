import argparse
import os
import shutil
import subprocess
from pathlib import Path


DEFAULT_VIDEO = Path(
    r"C:\Users\abhay\Downloads\Telegram Desktop\Wolf.Creek.2.2013.720p.BluRay.@GrandCinemas.mkv"
)

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


def resolve_video(video_arg: str | None) -> Path:
    if video_arg:
        video = Path(video_arg).expanduser()
        if not video.is_absolute():
            video = Path.cwd() / video
        if video.is_file():
            return video
        raise FileNotFoundError(f"Video file not found: {video}")

    if DEFAULT_VIDEO.is_file():
        return DEFAULT_VIDEO

    raise FileNotFoundError(
        "Video file not found. Pass the path explicitly, for example:\n"
        'python cut.py "C:\\path\\to\\your-video.mkv"'
    )


def build_keep_segments(duration: float) -> list[tuple[float, float]]:
    cuts = sorted((parse_timestamp(start), parse_timestamp(end)) for start, end in CUT_SEGMENTS)

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


def default_output_path(video: Path) -> Path:
    return Path.cwd() / f"{video.stem}_final_cut.mkv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove listed timestamp ranges from a video and create one final edited file."
    )
    parser.add_argument(
        "video",
        nargs="?",
        help="Optional path to the source video file.",
    )
    parser.add_argument(
        "--output",
        help="Optional path for the final edited video.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    video = resolve_video(args.video)
    ffmpeg = resolve_tool("ffmpeg")
    ffprobe = resolve_tool("ffprobe")
    duration = probe_duration(ffprobe, video)
    include_audio = has_audio_stream(ffprobe, video)
    keep_segments = build_keep_segments(duration)
    output_path = Path(args.output).expanduser() if args.output else default_output_path(video)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    print(f"Using FFmpeg: {ffmpeg}")
    print(f"Using video: {video}")
    print(f"Final output: {output_path}")
    print("Removing these ranges:")
    for start, end in CUT_SEGMENTS:
        print(f"  {start} -> {end}")
    print("Keeping these ranges:")
    for start, end in keep_segments:
        print(f"  {format_seconds(start)} -> {format_seconds(end)}")

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
            "veryfast",
            "-crf",
            "18",
        ]
    )

    if include_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])

    command.extend(["-sn", str(output_path)])

    subprocess.run(command, check=True)
    print("")
    print(f"Final edited video created: {output_path}")


if __name__ == "__main__":
    main()
