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
- It creates one final edited output video in the project folder.

`cut-video.ps1` is a separate helper for one-off manual cuts when a single clip is needed.

## How To Run

From the project folder:

```powershell
python cut.py
```

If the source movie moves to another folder, run:

```powershell
python cut.py "C:\full\path\to\your-video.mkv"
```

## Important Repo Notes

- Do not commit full movie files or rendered video outputs to normal GitHub history.
- The source and output `.mkv` / `.mp4` files are intentionally ignored because they are too large for a normal GitHub repo.
- This repo is meant to store the code, setup, and editing instructions only.

## Main Files

- `cut.py`: main final-video scene removal script
- `cut-video.ps1`: manual FFmpeg trim helper
- `FFMPEG-QUICKSTART.md`: quick command examples
- `.vscode/settings.json`: VS Code terminal PATH helper for FFmpeg on this machine
