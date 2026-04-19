# Video Timestamp Cutter

## Main Goal

The main goal of this project is to remove specific scenes from a source movie by timestamp and produce one final edited video.

At the moment, the target movie is:

`C:\Users\abhay\Downloads\Telegram Desktop\Wolf.Creek.2.2013.720p.BluRay.@GrandCinemas.mkv`

The scene ranges currently marked for removal are:

- `00:12:08` to `00:12:29`
- `00:16:39` to `00:21:35`
- `00:22:44` to `00:23:03`
- `00:28:06` to `00:29:39`
- `00:32:05` to `00:32:16`
- `00:32:19` to `00:32:37`

## What The Code Does

`cut.py` is the main script.

- It finds FFmpeg and FFprobe on Windows.
- It loads the source movie.
- It removes the listed timestamp ranges.
- It creates one final edited output video named `wolf_clean.mkv` in the project folder.
- It uses fast stream-copy mode by default so Codespaces does not need to re-encode the full movie.
- It removes an extra 5 seconds before and after each marked range for safer scene removal.

`cut-video.ps1` is a separate helper for one-off manual cuts when a single clip is needed.

## How To Run

Cut the marked scenes from the default movie:

From the project folder:

```powershell
python cut.py
```

This works if the movie file is in the same folder as `cut.py`. The final video will be named `wolf_clean.mkv`.

If the source movie moves to another folder, run:

```powershell
python cut.py "C:\full\path\to\your-video.mkv"
```

Keep the quotes around paths that contain spaces, such as `Telegram Desktop`.

You do not need Google Colab for this project. It is better to run locally because the movie file is large, and uploading/downloading it from Colab would take extra time.

## Lightweight TV Tools

These operations avoid heavy video re-encoding.

Inspect a video before converting it:

```powershell
python cut.py inspect "C:\full\path\to\movie.mp4"
```

The inspect command shows:

- container format
- duration and file size
- video codec, resolution, profile, and pixel format
- audio codec, channels, sample rate, and language
- subtitle codec and language
- likely LG TV readiness
- whether Hindi audio or English subtitles are already present

Convert/remux a video to MKV without changing quality:

```powershell
python cut.py to-mkv "C:\full\path\to\movie.mp4"
```

Merge an uploaded subtitle file into an MKV:

```powershell
python cut.py add-sub "C:\full\path\to\movie.mkv" "C:\full\path\to\english.srt"
```

Heavy operations such as H.265 to H.264 conversion, resizing, subtitle burn-in, compression, and exact frame cuts still need full re-encoding. Codespaces may stop those long jobs, so the script keeps them optional.

## Frontend

Run the local web UI:

```powershell
python web_app.py
```

Then open:

```text
http://127.0.0.1:8787
```

The frontend supports:

- uploading a video file before inspection
- uploading small subtitle files
- inspecting TV-relevant video properties
- cutting the marked scenes with fast copy mode
- remuxing to MKV
- merging uploaded subtitles
- validating required inputs and safe `.mkv` output names

## Important Repo Notes

- Do not commit full movie files or rendered video outputs to normal GitHub history.
- The source and output `.mkv` / `.mp4` files are intentionally ignored because they are too large for a normal GitHub repo.
- This repo is meant to store the code, setup, and editing instructions only.

## Main Files

- `cut.py`: main final-video scene removal script
- `cut-video.ps1`: manual FFmpeg trim helper
- `FFMPEG-QUICKSTART.md`: quick command examples
- `.vscode/settings.json`: VS Code terminal PATH helper for FFmpeg on this machine
