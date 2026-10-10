# Phase 5: Chunking, Parallel Processing, Merge, Resume

**Goal:** long videos are split at natural silences into chunks that are processed in parallel, then merged seamlessly. Short videos use the same code path with one chunk. Includes progress, ETA, retry, resume, cancel.

## Concurrency design

- **Orchestrator** (asyncio, main process) owns a per-chunk state machine:
  `A (audio: extract + denoise + envelope + segments + cut-audio)` -> `B (transcribe)` -> `C (video pass with ASS)`.
- **Two pools:**
  - `media_pool`: `ProcessPoolExecutor(spawn)` running stages A and C (FFmpeg + numpy). Size = `performance.workers` or auto = clamp(cpu_count // 2, 1, 4), reduced when free RAM is low.
  - `asr_pool`: persistent worker processes (size `asr_workers`, default 1). The Whisper model is loaded **once per worker** by the pool initializer and reused for every chunk. On GPU keep this at 1.
- Stages are **pipelined across chunks**: while chunk 1 is transcribing, chunk 2 can be in stage A and chunk 0 in stage C. Use bounded queues so disk usage does not explode.
- FFmpeg threads per call = max(1, cpu_count // workers).
- Global `MAX_CONCURRENT_JOBS=1` by default to avoid CPU oversubscription.

## Tasks

### 5.1 Chunk planner (`services/chunker.py`)
1. If `chunking=off`, or `auto` and duration <= `chunk_threshold_sec`: plan = one chunk `[0, duration]`.
2. Otherwise compute a **coarse** silence map for the whole file from audio only (stream decode to a low-rate mono envelope; this takes seconds even for hours of video).
3. For each target boundary `k * target_chunk_sec`, search `+/- boundary_window_sec` for silences >= 0.3 s; choose the midpoint of the longest/deepest one. Snap to the frame grid.
4. No silence in the window: choose the lowest-energy 100 ms point; mark the boundary `hard=true` (a word might be cut; log a warning).
5. If the last chunk is shorter than 30% of the target, merge it into the previous one.
6. Write `manifest.json`: `[{index, src_start, src_end, hard_boundary, status, cut_duration, attempts, files}]`.

### 5.2 Stage runners
- Wrap phases 2 and 3 functions as stage functions: `stage_audio(chunk)`, `stage_transcribe(chunk)`, `stage_video(chunk)`. Each reads inputs and writes outputs inside `work/<job>/chunk_<i>/`, using `.partial` + `.done` markers.
- Stage C reads from the **original** file with `-ss/-t`, so there is no intermediate video encode.
- Chunk outputs: `chunk_<i>.mkv` with identical codec params across chunks (same encoder, preset, crf, fps, timebase, pixel format, PCM 48 kHz).

### 5.3 Merge (`services/merger.py`)
- Concat demuxer (`-f concat -safe 0`), `-c copy`. Sort by index, skip chunks with `cut_duration == 0`.
- Verify: the sum of chunk durations equals the merged duration within 1 frame, and ffprobe shows a single consistent stream layout.
- Then call the phase 4 master stage on the merged file.
- Delete chunk intermediates as soon as merge succeeds (unless `keep_intermediates`).

### 5.4 Progress and ETA
- Overall progress = sum over chunks of `chunk_duration * stage_weight * stage_fraction` divided by total. Suggested weights: A 0.15, B 0.35, C 0.40, merge+master 0.10. Calibrate with the benchmark script.
- ETA = moving average of recent throughput (seconds of source per wall second). Show per-chunk states in SSE.

### 5.5 Failure, retry, resume, cancel
- Per-stage retry up to 2 times with backoff. A `BrokenProcessPool` (worker crash) recreates the pool and retries.
- Stage B failure after retries: if `degrade_on_asr_failure`, render that chunk without subtitles, add a job warning listing the chunk; otherwise fail the job with the chunk index in the error.
- Resume (`POST /api/jobs/{id}/resume`, also automatic for `interrupted`): skip any stage whose `.done` marker hash matches the current options-and-input hash.
- **Stage cache keys** (G3, architecture rule 9): changing subtitle style invalidates only stage C and later; changing silence options invalidates A, B, C; changing music/loudness invalidates only master. This enables a fast "re-run with different settings" in the UI.
- Cancel: set the flag, cancel pending futures, kill all child processes (ffmpeg and workers) via psutil, delete `.partial` files, mark `cancelled`.

### 5.6 Resource guards
- Before start: disk estimate (about 3x input size) vs free space; RAM check lowers `workers`.
- Memory watchdog: if free RAM falls below a threshold during the run, pause submitting new chunks until it recovers.
- `scripts/benchmark.py`: runs a synthetic 10-minute file through 1, 2, 4 workers and reports speedup and peak RAM, so defaults can be tuned from real numbers on the user's machine.

## Edge cases to handle

- No silence near a target boundary: hard boundary fallback (5.1.4).
- Speech spanning a hard boundary: a word may be split between two chunks; subtitles for both halves are still produced; log it.
- Tiny last chunk: merged (5.1.5).
- A chunk that is entirely silent: `cut_duration = 0`, skipped in merge. If **all** chunks are empty, fail with `NO_SPEECH`.
- Audio track shorter or longer than video inside a chunk: pad/trim so A/V durations match.
- Chunk boundary frame accuracy: both cut-audio and video pass use the same snapped segment list.
- Join between chunks: each chunk begins and ends with a fade (if enabled), so joins look the same as any other cut.
- Different loudness per chunk: avoided because normalization happens once at master.
- AAC gaps/clicks at joins: avoided because chunk audio is PCM.
- Language detection differing across chunks: language locked after the first chunk's detection. Chunk 0 transcription must finish before other chunks' stage B starts when `language=auto`.
- Worker crash / OOM kill: pool rebuilt, chunk retried.
- Windows `spawn`: all worker entry points importable at module level, guarded by `if __name__ == "__main__"`; no lambdas or closures sent to workers.
- Very long video (3 h+): enforce `MAX_DURATION_MIN`, show a confirmation in the UI if above a warning threshold.
- Server restart mid-merge: merge is idempotent (re-run from chunk outputs).
- User starts a second job: queued, shown as `queued` with position.
- Disk fills up mid-job: map to `DISK_SPACE`, keep completed chunk outputs so a resume is possible after freeing space.

## Tests

- Planner: synthetic envelopes with and without silences near targets, tiny last chunk, hard boundary.
- Equivalence: the same 10-minute synthetic file processed as 1 chunk vs 5 chunks gives durations within a small tolerance and the same set of removed silences (except near hard boundaries).
- Resume: kill the server after N chunk stages; resume completes without redoing finished stages.
- Cache invalidation: change only subtitle color, assert stages A and B were not re-run.
- Crash injection: kill a worker process mid-stage; job still completes.
- Cancel: all child processes gone within 2 s.

## Acceptance criteria

- [ ] A 60-minute synthetic or real video completes with flat memory and shows per-chunk progress with ETA.
- [ ] Speedup versus `workers=1` is measured and recorded in `docs/decisions.md`.
- [ ] Merged output has no audible click, no A/V drift (check start, middle, end).
- [ ] Resume, cancel, retry, and cache invalidation tests pass.
