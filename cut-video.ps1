param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$Start,

    [Parameter(Mandatory = $true)]
    [string]$End,

    [string]$OutputPath,

    [switch]$Reencode,

    [switch]$Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-FfmpegPath {
    $command = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-essentials_build\bin\ffmpeg.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    throw "FFmpeg was not found. Open a new terminal and run 'ffmpeg -version', or reinstall FFmpeg."
}

function Sanitize-Timestamp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return ($Value -replace '[^0-9A-Za-z]+', '-').Trim('-')
}

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$inputItem = Get-Item -LiteralPath $resolvedInput
$ffmpegPath = Resolve-FfmpegPath

if (-not $OutputPath) {
    $startToken = Sanitize-Timestamp -Value $Start
    $endToken = Sanitize-Timestamp -Value $End
    $OutputPath = Join-Path -Path $inputItem.DirectoryName -ChildPath ("{0}_{1}_to_{2}{3}" -f $inputItem.BaseName, $startToken, $endToken, $inputItem.Extension)
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)

$commonArgs = @(
    "-hide_banner"
)

if ($Overwrite) {
    $commonArgs += "-y"
}
else {
    $commonArgs += "-n"
}

if ($Reencode) {
    $ffmpegArgs = @(
        "-i", $resolvedInput,
        "-ss", $Start,
        "-to", $End,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        $resolvedOutput
    )
}
else {
    # Fast trim without re-encoding. This is quickest, but the cut may snap to nearby keyframes.
    $ffmpegArgs = @(
        "-ss", $Start,
        "-to", $End,
        "-i", $resolvedInput,
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        $resolvedOutput
    )
}

Write-Host "Using FFmpeg:" $ffmpegPath
Write-Host "Input:" $resolvedInput
Write-Host "Output:" $resolvedOutput
Write-Host "Mode:" ($(if ($Reencode) { "re-encode for more accurate cuts" } else { "stream copy for fastest cuts" }))

& $ffmpegPath @commonArgs @ffmpegArgs

if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg exited with code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Clip created successfully:"
Write-Host $resolvedOutput
