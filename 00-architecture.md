# 00 Architecture

## Stack

- **Backend:** Python 3.11+, FastAPI, Uvicorn, pydantic v2 + pydantic-settings, SQLite (WAL) for jobs, psutil, numpy, soundfile, Pillow, faster-whisper (CTranslate2), huggingface_hub (model download)
- **Media:** FFmpeg + ffprobe (full build, 5.x or newer; must include `libass`, `afftdn`, `sidechaincompress`; `arnndn` optional)
- **Frontend:** Vite + React 18 + TypeScript, Tailwind, TanStack Query, react-hook-form, openapi-typescript
- **Target OS:** Windows 10/11 first. Linux/macOS should work but is not the focus.
- **Deployment:** local-first. Backend binds to `127.0.0.1`. The built frontend is served by FastAPI so one process is enough.
- **No Redis / Celery.** Job queue = SQLite + `ProcessPoolExecutor(spawn)` + asyncio coordinator.

## Folder layout

```
auto-editor/
  backend/
    app/
      main.py
      api/        uploads.py jobs.py music.py cover.py system.py models.py fonts.py
      core/       config.py logging.py errors.py paths.py
      models/     options.py job.py
      services/   ffmpeg.py probe.py denoise.py envelope.py segments.py render.py
                  transcribe.py subtitles.py music.py cover.py master.py chunker.py merger.py
      pipeline/   orchestrator.py stages.py progress.py cache.py
      workers/    media_pool.py asr_pool.py
      storage/    jobs_db.py
      assets/fonts/   (open-license fonts only)
    tests/
    scripts/      benchmark.py setup.ps1
  frontend/
  data/           uploads/ work/<job_id>/ outputs/<job_id>/ models/ music/
  docs/decisions.md
```

## Pipeline (per chunk, then merged)

```
probe -> plan chunks (split at silences)
for each chunk (parallel):
  A  extract audio -> denoise -> RMS envelope -> keep-segments -> render CUT AUDIO (cheap, audio only)
  B  transcribe cut audio (faster-whisper)  -> words with timestamps already on the output timeline
  C  build ASS subtitles -> ONE video pass from the ORIGINAL file:
        trim segments + fades + concat + scale/crop + burn ASS + encode  -> chunk_out.mkv (h264 + PCM audio)
merge: concat chunk_out files (stream copy)
master: music mix + loudness normalize + AAC encode (video stream copy) -> final.mp4
cover: pick frame from ORIGINAL video -> Pillow text -> cover.png
```

Why this order:
- Video is lossy-encoded **once**. No intermediate video generations.
- Transcribing the already-cut audio means subtitle timestamps match the final timeline with no remapping math.
- Chunk outputs use PCM audio so there are no AAC priming gaps or clicks at chunk joins. AAC is encoded once, in master.
- Loudness normalization happens once on the whole file, so chunks never have different volume.

## Design rules for the AI coder (duration awareness)

1. **Filter graph size:** a long video can have hundreds of cuts. Cap `max_segments_per_graph` (default 120). If a chunk has more, render sub-batches to temp files and concat them. Always pass graphs with `-filter_complex_script file` (Windows command-line length limit).
2. **Constant frame rate:** phone videos are often variable frame rate. The video pass must force CFR (`fps=` filter) and snap all cut times to the frame grid, otherwise audio/video drift.
3. **Time-based timeouts:** `timeout = max(120, 6 * duration_of_input_in_this_call)`.
4. **Disk budget:** before starting, estimate needed space (about 3x input size) and fail early with a clear message.
5. **Memory budget:** workers = clamp(cpu_count // 2, 1, 4), lowered if free RAM is low. FFmpeg `-threads` = max(1, cpu_count // workers).
6. **ASR concurrency is separate** from FFmpeg concurrency (see phase 5). Whisper model is loaded once per ASR worker process, never per chunk.
7. **Progress** is weighted by duration, never by a count of steps.
8. **Everything is cancellable** at any point, and every long loop checks the cancel flag.
9. **Cache keys:** each stage hashes only the options that affect it plus its input hash, so changing subtitle colors does not redo denoise or transcription.

## Job states

`queued -> running -> (completed | failed | cancelled | interrupted)`

`interrupted` = server restarted mid-job. It can be resumed from completed stage markers.

## API surface (final)

```
GET    /api/system                 ffmpeg version, encoders, cuda, cpu/ram, models installed
GET    /api/options/defaults       default JobOptions
POST   /api/models/{size}/download start model download (SSE progress)
GET    /api/fonts                  fonts available for subtitles/cover
POST   /api/uploads                init chunked upload -> upload_id
PUT    /api/uploads/{id}/parts/{n} upload one part
POST   /api/uploads/{id}/complete  assemble + probe + validate
GET    /api/uploads/{id}           probe info + thumbnail url
GET/POST/DELETE /api/music         music library
POST   /api/cover                  render a cover for an upload (re-roll without a full job)
POST   /api/jobs                   {upload_id, options} -> job
GET    /api/jobs  /  /{id}         list / state
GET    /api/jobs/{id}/events       SSE progress
POST   /api/jobs/{id}/cancel
POST   /api/jobs/{id}/resume
DELETE /api/jobs/{id}
GET    /api/jobs/{id}/files/{kind} video | cover | transcript | log   (HTTP Range supported)
```
