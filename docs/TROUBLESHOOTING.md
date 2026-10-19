# Troubleshooting

## FFmpeg is missing / "FFMPEG_MISSING"

The backend needs a **full** FFmpeg build (not the minimal "essentials" for playback).

```
winget install --id Gyan.FFmpeg -e
```

Then restart the terminal so `PATH` updates. If FFmpeg is installed somewhere unusual,
set `FFMPEG_PATH` / `FFPROBE_PATH` in `.env` to the full exe paths.

Symptoms of a partial build: `filters` entries false in `/api/system`, or
"Unknown filter" errors mentioning `afftdn`, `ass`, `loudnorm` or `sidechaincompress`.

## CUDA / GPU problems

The UI shows CUDA as unavailable when `cublas64_12.dll` / `cudnn` cannot be loaded. That
is not an error — the job simply runs on CPU with a warning in the job warnings.

To enable GPU: install CUDA 12.x, put cuBLAS/cuDNN on `PATH`, and download a model.
If a CUDA attempt ever hangs, Cinelume remembers it for the process and falls back to
CPU on the next attempt.

Out of memory on the GPU: retry with a smaller compute type happens automatically; if
that also fails, the job runs on CPU.

## The Whisper model is not downloaded

`GET /api/system` lists each model and its status; the System screen has a Download
button. Downloads run in a child process, are resumable after interruption, and partial
files are removed on failure. Loading is fully offline afterwards.

## Subtitles are out of sync

Timestamps are produced from the already-cut audio, so they match the output timeline by
construction. If they drift, check that you are not comparing against the *source* video
— the output is shorter than the input because silences were removed.

## "Nothing was cut" / "silence not removed"

The threshold is what counts as silence. Try **auto threshold**, or raise
`threshold_db` towards -25. A constant background noise louder than the threshold means
nothing is detected as silence — auto threshold estimates the floor from the file.

## Windows long paths / antivirus

Long paths (deep `data\work\<job>\chunk_NNN\...` trees) can exceed the 260-character
limit. Enable long paths:

```
git config --global core.longpaths true
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Antivirus (Defender real-time scanning) can lock freshly written files for a moment;
deletions retry with backoff. Add the `data\` folder to exclusions for the best speed.

## Out of disk

The job needs roughly 3x the input size (chunk outputs, merged file, final MP4). The
job fails fast with `DISK_SPACE` and keeps completed chunk outputs, so after freeing
space you can resume from History without redoing finished stages.

## A worker crashed / BrokenProcessPool

The orchestrator rebuilds the process pool and retries the stage twice. If it still
fails the job is marked failed with the stage in the error; download the diagnostics zip
from `GET /api/jobs/{id}/diagnostics` (system info + manifest + log tail, no media).

## The UI cannot reach the server

The dev server proxies `/api` to `127.0.0.1:8000`. If you changed `PORT` in `.env`,
update `frontend/vite.config.ts`. In production the built `frontend/dist` is served by
FastAPI itself, so one process is enough.

## Two jobs at once

`MAX_CONCURRENT_JOBS=1` by default to avoid CPU oversubscription. The second job shows
as queued and starts when the first finishes.
