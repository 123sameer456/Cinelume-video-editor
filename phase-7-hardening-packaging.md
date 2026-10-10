# Phase 7: Hardening, Tests, Security, Windows Packaging

**Goal:** make it reliable and easy to install and run on a Windows PC.

## Tasks

### 7.1 Test suites
- **Unit:** segments algorithm, envelope/auto-threshold, ASS builder, word repair, line grouping, options validators, cache keys, chunk planner.
- **Integration (synthetic media via FFmpeg `lavfi`):** build clips from tone bursts + digital silence + low noise + `testsrc2` video, including VFR, rotated, no-audio, odd-size, and multi-audio-track variants.
- **ASR:** `Transcriber` protocol with a fake for CI; one optional `@slow` test using the `tiny` model and a short recorded fixture.
- **End-to-end:** 10-minute synthetic job, chunked vs single chunk, with ffprobe assertions (duration, stream layout, A/V diff < 1 frame, loudness near target).
- **Soak:** run 10 jobs back to back and confirm no leaked processes, file handles, or temp folders.
- CI: GitHub Actions (Windows runner) installing FFmpeg, running lint + unit + integration.

### 7.2 Security
- Bind to `127.0.0.1` by default; if the user exposes it, require an access token (simple bearer token in `.env`) and restrictive CORS.
- Never trust file names or extensions; store by id; path traversal protection on every file endpoint.
- No `shell=True` anywhere; assert in a lint rule/test.
- Sanitize all user text that ends up in ASS or FFmpeg filter strings (subtitle words are data, not code; escape `\ { } : ' %` as required).
- Upload and duration limits enforced server-side. Rate-limit upload/job creation if exposed.
- Delete user data on request and by TTL.

### 7.3 Observability
- Structured logs, per-job log file, downloadable from the UI. A "Diagnostics" button bundles system info, FFmpeg capabilities, job manifest and last log lines into a zip (no media).

### 7.4 Packaging (Windows)
- `scripts/setup.ps1`: checks Python, installs FFmpeg via `winget install Gyan.FFmpeg` if missing, creates venv, installs requirements, builds or downloads the frontend, optionally pre-downloads a chosen Whisper model, optionally downloads an RNNoise model.
- `start.bat`: activates the venv, starts Uvicorn on `127.0.0.1:8000` (single worker), opens the browser.
- Optional: PyInstaller or an installer later (not required).
- Optional Docker (with NVIDIA runtime) documented but not the primary path.

### 7.5 Documentation
- `docs/USER_GUIDE.md` (what every option does with before/after tips: e.g., how to choose X and the threshold).
- `docs/TROUBLESHOOTING.md`: FFmpeg not found, CUDA DLL missing, font not found, subtitles out of sync, "silence not removed", long paths on Windows, antivirus locking files, out of disk.
- `docs/decisions.md`: every ambiguity resolved by the AI coder, benchmark numbers, known limitations.

## Final edge-case checklist (every box must be covered by a test or a documented decision)

**Media**
- [ ] No audio / multiple audio tracks / mono / 5.1
- [ ] VFR / rotation metadata / odd dimensions / HEVC / 10-bit / HDR
- [ ] Audio-video start offsets, audio shorter or longer than video
- [ ] Corrupt/truncated input, zero duration, extremely short (< 2 s) input
- [ ] Unicode and space characters in names/paths

**Silence + fades**
- [ ] No silence / all silence / constant loud noise floor
- [ ] X equal to a silence length; hundreds of cuts; segment shorter than 2x fade
- [ ] Speech blips dropped; first/last fade option

**Subtitles**
- [ ] No speech; hallucination loops; fast speech; very long word
- [ ] Language lock across chunks; RTL language rendering; font family not found
- [ ] Box/outline styles; colors with alpha; platform safe zone margin
- [ ] GPU missing or failing, model missing, model download interrupted

**Music + master**
- [ ] Short/long/corrupt track; music enabled without a track; volume 0
- [ ] No-audio video with music; silent audio and loudnorm; ducking on/off

**Cover**
- [ ] Black/blurry frames, very short video, rotated/HDR source, text too long, missing glyphs

**Chunking + system**
- [ ] No silence near a boundary; tiny last chunk; all-silent chunk; speech spanning a boundary
- [ ] Worker crash; server restart; resume; cancel; disk full; low RAM
- [ ] Two jobs queued; two browser tabs; SSE reconnect

## Definition of done (whole project)

- [ ] A 60-minute 1080p video finishes end to end, with correct sync, flat memory, working resume, on a mid-range Windows laptop.
- [ ] Every option in `00-options-schema.md` is exposed in the UI and verifiably changes the output.
- [ ] Fresh Windows machine can install and run using only `setup.ps1` + `start.bat`.
- [ ] All tests green in CI; `docs/` complete.
