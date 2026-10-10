# Phase 6: React Frontend

**Goal:** a clean UI to upload a video, tune every option, watch progress, and get the video + cover. The server is the validator and the source of truth for defaults.

## Stack

Vite + React 18 + TypeScript, Tailwind (+ Radix primitives), TanStack Query, react-hook-form, `openapi-typescript` for API types (regenerate from the FastAPI OpenAPI JSON). Dev: Vite proxy to `127.0.0.1:8000`. Production: `vite build` output served by FastAPI.

## Screens

1. **Upload**
   - Drag and drop. Custom chunked uploader (8 MB parts, 3 parallel, retry with backoff, resume via `GET /api/uploads/{id}`). Overall progress and speed.
   - After completion show a file card: name, duration, resolution, fps, audio present, size, thumbnail. Show warnings from probe (VFR, HDR, no audio, over duration limit).
2. **Configure** (one section per pipeline step, each with an enable toggle, collapsed by default except the first):
   - **Audio cleanup:** method, strength slider, high-pass.
   - **Silence:** "Cut silence longer than ___ s" (X), threshold dB with an "auto" switch, padding, minimum speech, fade on/off, fade length, fade video/audio, fade color.
   - **Subtitles:** model size (with downloaded/not-downloaded state and a download button), language, mode, font, size, bold, text color, highlight color, background mode + color + opacity, position, margin, words per line, uppercase, pop animation. A **live approximate preview** box using CSS (clearly labeled approximate; animation loops the sample sentence) so color changes are visible instantly.
   - **Music:** on/off toggle, track picker + upload, volume slider 0-100, loop, fades, ducking. Short in-browser audio preview at the chosen relative volume (approximate).
   - **Cover:** on/off, random/manual time (scrubber on the uploaded video using a `<video>` element), text lines, font, color, stroke, strip, position, aspect. "Generate / re-roll" calls `POST /api/cover` and shows the result immediately without starting a job.
   - **Output:** resolution, fit, fps, quality (crf), preset, encoder, loudness normalization + target.
   - **Advanced / performance:** chunking mode, chunk length, workers, ASR workers, degrade-on-failure.
   - "Reset section to defaults" and "Reset all". Inline server validation errors on the field.
3. **Progress**
   - Overall bar + ETA, current stage, per-stage bars, per-chunk grid (color by state: queued / running / done / failed), warnings list, expandable live log, Cancel button (confirmation).
   - SSE client with auto-reconnect; on reconnect fetch the snapshot first. The URL contains the job id so a browser refresh returns to the same job.
4. **Result**
   - Video player (uses Range requests), cover preview, downloads (video, cover, transcript JSON, log), job warnings, stats (original vs final duration, time removed).
   - **Re-run with changed options** (reuses upload and the cache from phase 5, so only affected stages run).
5. **History:** list of jobs with status, delete.
6. **Settings / System:** FFmpeg status, encoders, GPU, models installed + download progress, storage used and cleanup.

## Cross-cutting behavior

- Global banner when FFmpeg is missing or the chosen Whisper model is not downloaded (with a one-click download).
- Error mapping: backend `code` -> friendly message + hint (table in `frontend/src/lib/errors.ts`).
- Client-side hints (never blocking): low contrast text/background, subtitles inside platform UI zone, `min_silence_sec` too small, fade longer than padding.
- Persist the last-used options in `localStorage` (as defaults for the form only).
- Responsive layout (usable on a tablet), keyboard accessible, no color-only meaning for chunk states.

## Edge cases to handle

- Refresh or tab close mid-upload: resume.
- Refresh mid-job: restore from the URL job id.
- SSE drops or the laptop sleeps: reconnect + snapshot; never show a stuck 99%.
- Backend restarted: show `interrupted` with a "Resume" button.
- Huge file selected: warn about duration/size limits before uploading starts.
- Unsupported file type: friendly message from the server probe.
- Many fonts: virtualized list or searchable select.
- Large video preview in the browser: use object URLs from the server's file endpoint, not blobs in memory; revoke any object URLs.
- Music preview with a file the browser cannot decode: fall back to "no preview".
- Two tabs open: job state is read from the server, so both are consistent.
- Slider values mapping (`volume` 0-100) must send integers; floats are rounded client-side.

## Tests

- Component tests for option sections (render defaults, validation messages).
- Uploader unit tests with a mocked server (retry, resume).
- SSE hook tests (reconnect, snapshot).
- One Playwright end-to-end test against a real backend with a 10-second synthetic video and the fake transcriber.

## Acceptance criteria

- [ ] Full flow works: upload -> configure -> run -> result, with every option from `00-options-schema.md` reachable in the UI.
- [ ] Refresh mid-job and mid-upload recovers correctly.
- [ ] Changing only subtitle colors and re-running is clearly faster than the first run.
- [ ] Cover re-roll works without starting a job.
