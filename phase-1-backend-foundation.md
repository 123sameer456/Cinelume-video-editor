# Phase 1: Backend Foundation

**Goal:** a running FastAPI service that can accept big uploads, probe media, run a (dummy) job in a worker process, stream progress, and cancel cleanly. No real editing yet.

## Tasks

1. **Scaffold:** `pyproject.toml`, venv, ruff, pytest, `.env` support via pydantic-settings (`DATA_DIR`, `FFMPEG_PATH`, `FFPROBE_PATH`, limits from `00-options-schema.md`). Fail startup with a clear message if FFmpeg/ffprobe are not found.
2. **Options model:** implement `models/options.py` exactly as `00-options-schema.md`, including cross-field validators. `GET /api/options/defaults`.
3. **FFmpeg wrapper (`services/ffmpeg.py`):**
   - `run_ffmpeg(args, cwd, timeout, on_progress, cancel_event, log_path)`: uses `-progress pipe:1 -nostats -hide_banner`, parses `out_time_us`, writes stderr to the job log, decodes output as UTF-8 with `errors="replace"`.
   - On cancel/timeout: kill the whole process tree with psutil, return a structured error.
   - `probe(path)` via ffprobe JSON: duration, width/height after rotation, fps (`avg_frame_rate` vs `r_frame_rate`, flag `is_vfr`), rotation, pixel format, HDR flag (color transfer smpte2084/arib-std-b67), audio streams (index, channels, sample rate), `has_audio`, container start_time of each stream, file size.
   - `capabilities()`: ffmpeg version, filters present (`afftdn`, `arnndn`, `ass`, `sidechaincompress`, `loudnorm`), encoders present, and a **1-frame trial encode** to confirm which hardware encoders really work.
4. **Chunked uploads:** init / part / complete endpoints. Parts streamed straight to disk (never buffered in RAM). Resume support: `GET /api/uploads/{id}` lists received parts. On complete: assemble, ffprobe, **validate by probing, not by extension**, reject if no video stream, duration 0, over limits. Generate a thumbnail.
5. **Job system:** SQLite (WAL) tables `jobs`, `chunks`, `events`. `JobManager` singleton: queue, `MAX_CONCURRENT_JOBS`, spawn-based `ProcessPoolExecutor`, cancel flag shared with workers (multiprocessing Event or file flag). On startup: any `running` job becomes `interrupted`.
6. **Progress + SSE:** `GET /api/jobs/{id}/events` emits `{stage, overall_pct, stage_pct, eta_sec, message, chunks:[...]}`. Include a snapshot on connect so the UI can recover after a reconnect.
7. **Dummy pipeline:** a fake job that runs N chunks with sleeps and one real FFmpeg call (e.g., thumbnail), to prove progress, cancel, and restart behavior.
8. **Errors:** `core/errors.py` with a typed `PipelineError(code, message, stage, hint)`; FastAPI handler returns JSON. Codes to define now: `FFMPEG_MISSING`, `UNSUPPORTED_MEDIA`, `NO_VIDEO_STREAM`, `NO_AUDIO_STREAM`, `DISK_SPACE`, `LIMIT_EXCEEDED`, `FFMPEG_FAILED`, `CANCELLED`, `TIMEOUT`, `MODEL_MISSING`, `ASR_FAILED`.
9. **File serving:** `GET /api/jobs/{id}/files/{kind}` with HTTP Range (verify the installed Starlette version supports Range in `FileResponse`; otherwise implement it).
10. **Housekeeping:** TTL cleanup task for uploads and work dirs. Per-job log file. `/api/system` endpoint.

## Edge cases to handle in this phase

- No audio stream: allowed. Mark `has_audio=false`; later phases skip denoise/silence/subtitles and warn.
- Audio-only file or image: reject with `NO_VIDEO_STREAM`.
- Several audio tracks: use `audio_track_index`, default 0; expose the list in probe.
- Corrupt or truncated file: probe fails -> `UNSUPPORTED_MEDIA`, not a stack trace.
- Phone video with rotation metadata, odd width/height (yuv420p needs even dimensions), 10-bit/HEVC/HDR: record in probe; later phases normalize to 8-bit yuv420p SDR and warn for HDR.
- Container start_time not zero, or audio/video start offsets differing: record them; phase 2/5 normalize.
- Unicode and spaces in file names: store uploads under `<upload_id>/source.<ext>` and keep the original name only as metadata.
- Upload interrupted / duplicated parts / out-of-order parts / wrong size: verify each part size and total.
- Disk full during upload or processing: check free space first (`shutil.disk_usage`), map `OSError` to `DISK_SPACE`.
- Server restart mid-job: `interrupted`, and resumable later.
- Antivirus/Windows file locks on delete: retry delete with backoff.
- Uvicorn must not use multiple workers (job state lives in one process).

## Tests

- Options validation table tests (valid, boundary, cross-field failures).
- `probe()` against synthetic files: with/without audio, rotated, VFR (generate with `-vf setpts=random` style trick or `-vsync vfr`), odd dimensions.
- Upload: 1 GB synthetic file streamed, RAM stays flat (assert RSS growth under a threshold), resume after dropping a part.
- Cancel test: start a long `ffmpeg -f lavfi -i testsrc -t 600 ...`, cancel, assert process tree is gone in under 2 s.

## Acceptance criteria

- [ ] Server starts, `/api/system` shows ffmpeg version, encoders, cpu/ram, cuda availability (if any).
- [ ] 2 GB upload works without RAM spike and can resume.
- [ ] Dummy job shows live progress via SSE, survives an SSE reconnect, can be cancelled in under 2 s.
- [ ] Restarting the server marks running jobs `interrupted`.
- [ ] All listed error codes are produced by at least one test.
