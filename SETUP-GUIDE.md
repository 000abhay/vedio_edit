# Project Setup Guide

This file explains what is required to run this project and how to set up the environment.

## Requirements

You need these tools:

- Python 3.12 or newer
- `ffmpeg`
- `ffprobe`

Python libraries:

- No third-party Python packages are required right now.
- The project uses Python standard library modules only.

## Project Files Used To Run

Main files:

- `web_app.py` - starts the local web interface
- `cut.py` - runs video tools from the command line
- `web/` - frontend HTML, CSS, and JavaScript files

## Environment Setup

Open a terminal in the project folder and run these commands.

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Activate the virtual environment

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```cmd
.venv\Scripts\activate.bat
```

Linux or macOS:

```bash
source .venv/bin/activate
```

### 3. Upgrade pip

```bash
python -m pip install --upgrade pip
```

### 4. Install Python packages

No extra Python packages are needed for this project.

## Install FFmpeg

This project needs both `ffmpeg` and `ffprobe`.

### Windows

Option 1:

```powershell
winget install Gyan.FFmpeg
```

Option 2:

- Download FFmpeg manually
- Add the `bin` folder to your system `PATH`

Check installation:

```bash
ffmpeg -version
ffprobe -version
```

### Ubuntu or Debian

```bash
sudo apt update
sudo apt install ffmpeg
```

### macOS with Homebrew

```bash
brew install ffmpeg
```

## Commands To Run The Project

### Start the web app

```bash
python web_app.py
```

Then open:

```text
http://127.0.0.1:8787
```

If port `8787` is busy, the app automatically tries the next port.

### Run the main cut script

Use the default video:

```bash
python cut.py
```

Use a specific video file:

```bash
python cut.py "path/to/your-video.mkv"
```

### Inspect a video

```bash
python cut.py inspect "path/to/movie.mp4"
```

### Convert a video to MKV

```bash
python cut.py to-mkv "path/to/movie.mp4"
```

### Add subtitle to a video

```bash
python cut.py add-sub "path/to/movie.mkv" "path/to/subtitle.srt"
```

## Quick Start

If your friend only wants to run the web app, these are the main commands:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python web_app.py
```

Also make sure `ffmpeg` and `ffprobe` are installed and available in `PATH`.

## Notes

- Keep video files in the project folder or in the `uploads/` folder when using the web app.
- Large `.mkv` files are not included in the small share zip unless added separately.
- If `ffmpeg` is not found, install it first and then open a new terminal window.
