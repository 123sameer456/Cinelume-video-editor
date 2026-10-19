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

## Phase 2 - Audio cleanup, silence detection, cut + fades

- **`compute_keep_segments` takes `hop_sec` in seconds.** The plan's `hop` parameter was
  ambiguous; the first implementation passed the hop in samples, so `run_length` was
  compared against `min_silence_sec` in the wrong unit and nothing was ever cut. The
  parameter is now `hop_sec` (seconds) and the envelope exposes both `hop` (samples)
  and `hop_sec`.
- **All-silent audio returns an empty list.** `compute_keep_segments` returns `[]` when
  >= 99% of the envelope is silent, and `stage_audio` raises `NO_SPEECH`. "Never drop
  everything" in `_drop_short` therefore applies to speech only.
- **afftdn mapping:** `nr = 6 + 0.24 * strength` (6..30 dB), `nf = -70 + 0.4 * strength`
  (-70..-30 dB), plus a gentle `alimiter=limit=0.98` guard. Measured on synthetic
  broadband white noise, afftdn only drops the RMS by ~1-2 dB (it is per-band spectral
  subtraction), so the test asserts "speech preserved and never worse" rather than a
  large drop; the spectral-gate method carries the strong-reduction assertion.
- **`noisereduce` is a self-contained numpy spectral gate** (STFT 1024/256, Hann,
  noise profile from the quietest 0.5 s, oversubtraction = 1 + 2*strength/100,
  spectral floor, 20 s blocks with 1 s crossfade and overlap-add). Installing the
  `noisereduce` package would drag in numba/librosa; this keeps memory bounded with no
  extra dependency. Verified to reduce the noise floor by > 5 dB while preserving tone.
- **`arnndn` falls back to `afftdn` with a warning** when no `.rnnn` model exists in
  `data/models/rnnoise/`, rather than failing the whole job for an optional denoiser.
- **Output frame rate uses `-r {fps}`.** Using `-fps_mode cfr` alone silently defaulted
  the output to 25 fps, which also truncated batched renders.
- **`concat` collapses sub-frame segments.** Segments shorter than one frame (0.04 s at
  30 fps) make the concat filter emit almost nothing. This is unreachable through the
  options (`min_speech_sec` >= 0.03 s and snapped to the frame grid), and the batching
  test uses realistic 0.10 s segments over a 36 s source (300 segments, 3 batches).
- **Fades:** fade-in on every segment except the first and fade-out on every segment
  except the last; `fade_first_last` additionally fades the very start/end. Fade length
  per segment is `min(fade_sec, segment_duration / 2)`.
- **VFR sources** are normalized by the `fps=` filter and the cut points are snapped to
  the frame grid; the test generates a real VFR file (`select` + `-fps_mode vfr`) and
  checks A/V diff stays under one frame.

## Phase 3 - Transcription and animated subtitles

- **PyAV is pinned to `>=11,<19`.** faster-whisper 1.2 still calls
  `av.open(..., metadata_errors="ignore")`, which PyAV 19 removed, so every transcription
  failed with "open() got an unexpected keyword argument 'metadata_errors'". PyAV 18.1.0
  ships a `cp311-abi3` wheel that works on Python 3.13 (PyAV 14 has no 3.13 wheel and
  needs MSVC to build).
- **A failed CUDA init can hang the next attempt in the same process.** On this machine
  CUDA is detected but `cublas64_12.dll` is missing: the first attempt fails cleanly, a
  *second* CUDA attempt hangs forever in the smoke transcription. Device selection keeps
  a process-wide `_cuda_broken` latch, so after a CUDA failure all later instances go
  straight to CPU. A genuine out-of-memory still retries the smaller compute type.
- **Model downloads run in a child process** (`app/_asr_download.py`) so cancel can kill
  the whole process tree; the child's stderr goes to a file, because piping it deadlocks
  once tqdm fills the pipe buffer. Progress is the model directory size vs an approximate
  per-size total. `model.bin` must exist afterwards or the download is treated as failed
  and partial files are removed.
- **Sentence punctuation always starts a new caption** (the plan lists punctuation as a
  break trigger without a word-count gate), and **static mode escapes text too** - the
  first version emitted the raw caption text.
- **Box background uses `BorderStyle=3` with OutlineColour and BackColour both set** to
  the box colour, because libass fills the box from OutlineColour while VSFilter uses
  BackColour; setting both covers either implementation. A `\p1` drawing fallback is not
  implemented yet (visual check pending).
- **Fonts:** five OFL families are bundled from the google/fonts repo (Montserrat,
  Poppins, Inter, Noto Sans, Noto Naskh Arabic) with the license text under
  `assets/fonts/licenses/`. Family names are read from the files with Pillow.
- **Real ASR smoke run:** the tiny model downloads, loads fully offline
  (`local_files_only=True`), and transcription completes. On synthetic tone-burst audio
  Whisper returns no words, so subtitles are skipped with a `NO_SPEECH_DETECTED` warning
  and the job continues - the plumbing is verified; transcription quality needs real
  speech, which no fixture in this repo provides.

## Phase 4 - Music, cover, final audio master

- **`sidechaincompress`'s `threshold` is a LINEAR amplitude** in [0.000976563, 1], not dB.
  `ducking_strength` maps to a dB value (-50..-10) which is then converted with
  `10**(dB/20)`. Passing dB directly failed with "Value for parameter 'threshold' out of
  range".
- **`loudnorm=print_format=json` prints to stderr at INFO level**, so the measurement
  passes run with `-loglevel info`; with the default `warning` level the report is
  suppressed and the measurement looks like it found nothing. Also `normalization_type`
  in that report is a *string* ("dynamic"), so the parser skips non-numeric values
  instead of raising.
- **The master's output label depends on loudnorm**: `[aout]` when it is skipped and
  `[out]` when it is applied; `-map` has to match or ffmpeg says the label "does not
  exist in any defined filter graph".
- **Loop clicks are avoided by micro-fading both edges of the cached `normalized.wav`**
  (10 ms) instead of an `acrossfade` at the loop point: both ends meet at silence, so
  joining them is continuous.
- **Music validation rejects any video stream** - a video file that happens to have audio
  is not a music track.
- **`amix=inputs=2:duration=first:normalize=0`** is used; this FFmpeg 7.1.1 supports
  `normalize`. Speech is split with `asplit` so it can be the sidechain first and then be
  mixed in.
- **Ducking depth is not asserted numerically** - at a background volume the change in
  the mixed RMS is far too small to assert reliably; the test checks the graph ordering
  (speech used as the sidechain before being mixed) and that ducking produces output.
- **Cover frames** are extracted at 480px wide for scoring and 1280 for the final render;
  Pillow's Raqm is unavailable on Windows wheels, so RTL text is shaped with
  `arabic-reshaper` + `python-bidi`.
- **libsndfile cannot open MP4**, so the tests decode through ffmpeg before measuring RMS.
- **The CLI cleans up its temp workdir in a `finally`**, so the cover is copied out
  before the directory is removed (the first version copied from a deleted folder).

## Phase 5 - Chunking, parallel processing, merge, resume

- **`chunking="on"` chunks regardless of `chunk_threshold_sec`.** The plan's rule is
  "off, or auto and duration <= threshold -> one chunk", so the threshold only applies to
  `auto`; the first implementation applied it to `on` too and never chunked short videos.
- **`verify_merge` compares against the chunk OUTPUT durations, not source spans.** The
  first version compared the merged file with `sum(chunk.src durations)` and failed with
  "Merged duration 4.1s does not match the chunks (12s)" - the whole point of the cut is
  to make the output shorter. The expected duration now comes from each chunk's
  `audio_stage.json` (`output_duration`).
- **An entirely-silent CHUNK is skipped, not fatal.** `stage_audio` returns an empty
  segment list with a warning; the orchestrator marks the chunk `skipped` and omits it
  from the merge; `merge_chunks` raises `NO_SPEECH` only when *every* chunk is silent.
  `run_single_chunk` (CLI) still raises `NO_SPEECH` for a fully silent file.
- **Stage submitters, not futures.** `_submit_audio` returns callables that submit a
  *fresh* future, because retrying a future that already failed with
  `BrokenProcessPool` just returns the same exception again.
- **`_retry` checks the cancel event before every attempt.** Without that, cancelling
  killed the workers, the broken futures triggered pool rebuilds, and the job ended
  `failed` instead of `cancelled`.
- **ASR workers are persistent** (`ProcessPoolExecutor(initializer=init_asr_worker)`), so
  the Whisper model loads once per worker. The pool is created lazily and only when
  subtitles are enabled; a missing model fails the job with `MODEL_MISSING` before any
  work starts.
- **Subtitle ASS is built in the main process** after stage B, from the `words.json` the
  ASR worker wrote - words never cross a process boundary inside the model.
- **Debug scripts must guard `if __name__ == "__main__"`**: a script that creates a
  multiprocessing Manager at import time makes every spawn child re-import the main
  module, which recurses and hangs. This bit the ad-hoc debugging scripts; the app
  itself (`uvicorn app.main:app`) is fine.
- **`scripts/benchmark.py`** runs the same synthetic clip through 1/2/4 workers and
  prints wall time, speedup and peak RSS; run it before tuning the defaults.

## Phase 6 - React frontend

- **Plain CSS instead of Tailwind + Radix.** The plan listed Tailwind "(+ Radix
  primitives)"; the UI is built with a small hand-rolled design system in `index.css`
  (CSS variables + BEM-ish classes). That keeps the dependency tree small, avoids a
  PostCSS setup, and every option from the schema is still reachable. Swapping to
  Tailwind later is a pure styling change.
- **React 18 + TanStack Query** are the only runtime dependencies; `react-hook-form` was
  not needed because the option forms are simple controlled inputs with server-side
  validation surfaced through banners.
- **`openapi-typescript` types are generated into `src/api/schema.d.ts`** (with the saved
  `openapi.json` and an `npm run openapi` script) and the app-level types in
  `src/api/types.ts` mirror the backend models so the UI compiles without the server.
- **The uploader is a plain async worker pool** (8 MB parts, 3 parallel, exponential
  retry, resume via the server's `received_parts`) rather than a library.
- **Section collapsing** in the Configure screen is a header click on each card, with the
  Silence section open by default (per the plan).
- **Playwright e2e is deferred** to Phase 7 along with the rest of the test suites; the
  verification for this phase is `npm run build` (tsc strict) plus the built `dist/`
  being served by FastAPI at `/`.

## Phase 7 - Hardening, security, packaging

- **Static security tests** (in `tests/test_security.py`) assert there is no
  `shell=True`, no `os.system`/`os.popen` and no shell module anywhere in `app/`. The
  first version of that test matched the *docstring* that says "never shell=True", so it
  now walks the AST and checks only real code nodes.
- **Diagnostics bundle** lives at `GET /api/jobs/{id}/diagnostics`: a zip with system
  info, options, the chunk manifest and the last 200 log lines - explicitly no media.
- **`safe_child`** guards every path built from a client-supplied name; the file
  endpoints never join raw user text (the `kind` is looked up in a fixed table).
- **Windows packaging**: `scripts/setup.ps1` installs Python/FFmpeg/Node via winget when
  missing, creates the venv, installs `backend[asr,dev]`, builds the frontend and
  optionally pre-downloads a Whisper model. `scripts/start.bat` starts uvicorn on
  `127.0.0.1:8000` (single worker) and opens the browser.
- **CI** (`.github/workflows/ci.yml`) runs ruff + pytest on a Windows runner with FFmpeg
  from Chocolatey, and builds the frontend. The two `@slow` ASR tests are skipped
  unless `CINELUME_SLOW=1`.
- **The equivalence test now accepts `skipped` chunks**: a 120s source with speech only
  in the first 6s produces three entirely-silent chunks, which the plan says must be
  skipped rather than fail the job.

### Final edge-case checklist

| Area | Cases | Where |
|---|---|---|
| Media | no audio / multi-audio / mono / 5.1 | `test_probe_*`, `test_denoise`, `test_master` |
| Media | VFR / rotation / odd dims / HDR / 10-bit | `test_probe_monkeypatched`, `test_vfr_source_*` |
| Media | offsets, short audio, corrupt, image, text file | `test_probe_*`, `test_upload_audio_only_rejected` |
| Silence | none / all / constant noise / X equal / hundreds / short segments | `test_segments_*`, `test_cut_*` |
| Silence | blips dropped, first/last fade | `test_segments_*`, `test_fades_are_visible_*` |
| Subtitles | no speech, hallucination loops, fast speech, long words, RTL, box/outline, colours, GPU missing, model missing | `test_subtitles_*`, `test_transcribe_pipeline_*` |
| Music | short/long/corrupt track, enabled without track, volume 0, no-audio video, silent + loudnorm, ducking | `test_music_*`, `test_master_*` |
| Cover | black frames, reproducible seed, wrapping, RTL, aspect, jpg | `test_cover_*` |
| Chunking | boundary silence, hard boundary, tiny last chunk, equivalence, resume, cache keys, cancel | `test_chunker_*`, `test_stage_cache_*` |
| System | two jobs queued, SSE reconnect, upload resume, part out of range, diagnostics zip, access token | `test_jobs_api_*`, `test_uploads_api_*`, `test_security_paths_*` |







