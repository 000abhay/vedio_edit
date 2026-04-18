# FFmpeg Timestamp Cutting

FFmpeg is installed on this PC. If a normal terminal still does not recognize `ffmpeg`, close it and open a new PowerShell window first.

## Fast cut without re-encoding

This is the fastest option and keeps the original quality.

```powershell
.\cut-video.ps1 -InputPath ".\input.mp4" -Start "00:00:12" -End "00:00:27" -OutputPath ".\clip.mp4" -Overwrite
```

## More accurate cut

Use this when you want the cut to land more precisely on the timestamps.

```powershell
.\cut-video.ps1 -InputPath ".\input.mp4" -Start "00:00:12.500" -End "00:00:27.800" -OutputPath ".\clip-accurate.mp4" -Reencode -Overwrite
```

## Use FFmpeg directly

Fast copy:

```powershell
ffmpeg -ss 00:00:12 -to 00:00:27 -i ".\input.mp4" -c copy ".\clip.mp4"
```

More accurate re-encode:

```powershell
ffmpeg -i ".\input.mp4" -ss 00:00:12.500 -to 00:00:27.800 -c:v libx264 -crf 18 -preset medium -c:a aac -b:a 192k ".\clip-accurate.mp4"
```

## Notes

- `-Reencode` is slower but usually gives cleaner start and end points.
- If you skip `-OutputPath`, the script auto-generates a file name from the timestamps.
- Supported timestamp examples: `12`, `01:25`, `00:01:25.500`
