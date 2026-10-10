# Decisions

Ambiguities resolved while implementing, with the safest default chosen.

## Phase 1 - Backend foundation

- **Build backend:** switched from hatchling to setuptools. Hatchling's editable
  install produced metadata only and did not put `app` on `sys.path`, so `import app`
  failed. Setuptools' editable install works reliably on Windows.
- **Upload part numbering is 0-based.** The uploader sends parts `0..total_parts-1`
  to `PUT /api/uploads/{id}/parts/{n}`. Documented so the frontend matches.
- **`extra="forbid"` on all option models.** The server is the validator; unknown keys
  are rejected instead of silently ignored. Frontend types are generated from OpenAPI.
- **`GET /api/system` CUDA detection** tries `ctranslate2.get_cuda_device_count()` and
  falls back to "a hardware H.264 encoder trial succeeded" before the ASR stack is
  installed (phase 3).
- **Hardware encoder probing** runs a real 1-frame `testsrc` encode per encoder and only
  reports encoders that actually succeed (verified on this machine: `h264_nvenc` and
  `h264_qsv` pass, `h264_amf` fails).
- **Resume in phase 1 re-runs the dummy job from scratch.** Stage-level skip via `.done`
  hash markers arrives with the real stages in phase 5.
- **Dummy pipeline artifacts:** to exercise Range file serving before real rendering,
  the dummy master stage writes `output.mp4` (a short re-encode), `thumb.jpg`, a copy as
  `cover.png`, and a placeholder `transcript.json`.
- **`interrupted` is resumable** but is not auto-restarted on boot; queued jobs are
  re-enqueued, running jobs become `interrupted` (per spec).
- **`-loglevel warning`** for job FFmpeg calls: keeps useful warnings without the very
  verbose `info` stream. Per-call stderr is appended to the per-job log.
- **Pseudo-media is rejected in `probe()`:** ffprobe happily "reads" arbitrary text as
  ANSI art via its `tty` demuxer (`format_name=tty`, `codec_name=ansi`), which otherwise
  looks like a valid 640x400/25fps clip. `probe()` raises `UNSUPPORTED_MEDIA` for the
  `tty` format / `ansi` codec, and for files with zero streams.
- **Part size is flexible.** The server advertises `part_size = 8 MiB` but accepts any
  part up to `MAX_PART_BYTES` (64 MiB) and does not bound the part *count* by the
  advertised size; the assembled total is verified at complete time. This keeps resume
  working for clients that choose a different part size.
- **`PipelineError` is picklable.** Job stages run in spawned worker processes, so
  exceptions cross a process boundary. `__reduce__` reconstructs the error with every
  constructor argument (code/message/stage/chunk/hint); otherwise unpickling raised a
  `TypeError` and corrupted the `ProcessPoolExecutor` for later jobs.
- **Flat-memory upload test is service-level.** Starlette's `TestClient`/httpx ASGI
  transport accumulates the request body, so an in-process RSS assertion over HTTP
  measures the test harness, not the server. The test streams 256 MiB through
  `save_part` directly (RSS stays flat); the real end-to-end path uses
  `request.stream()` under uvicorn and is exercised by the live smoke run.

