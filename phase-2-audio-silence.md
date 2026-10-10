# Phase 2: Audio Cleanup, Silence Detection, Cut + Fades

**Goal:** given one input video and options, produce a denoised, silence-trimmed video with fades at every join (no subtitles or music yet). Build every function so it takes `(workdir, chunk)` and works on a time range. For now there is exactly one chunk covering the whole file (rule G10).

## Tasks

### 2.1 Audio extraction
- Extract the selected audio track to `audio.wav`: 48 kHz, PCM s16le, keep stereo if source is stereo, downmix 5.1+ to stereo. Use `aresample=async=1:first_pts=0` so an audio start offset does not shift sync.

### 2.2 Denoise (`services/denoise.py`)
- `afftdn` (default): map `strength` 0-100 to `nr` (about 6-30 dB) and a noise floor `nf`. Always add `highpass=f=<highpass_hz>` when > 0. Optionally add a light `acompressor`/`alimiter` guard to avoid clipping.
- `arnndn`: requires a `.rnnn` model path from `data/models/rnnoise/`; if missing, return `MODEL_MISSING` with a hint (or fall back to `afftdn` and warn).
- `noisereduce`: process in blocks (about 30 s, with overlap + crossfade) so memory stays bounded; estimate the noise profile from the quietest 0.5 s.
- Output `audio_dn.wav`. Do **not** loudness-normalize here (done once at master stage).

### 2.3 Envelope (`services/envelope.py`)
- Read `audio_dn.wav` with `soundfile` in blocks and compute RMS in dBFS per 20 ms hop into a numpy array. Memory is proportional to the hop count, not to the sample count.
- `auto_threshold`: noise floor = 10th percentile of the envelope, speech level = 90th percentile; `threshold = floor + 0.35 * (speech - floor)`, clamped to [-55, -25] dB. Log the chosen value and return it to the UI.

### 2.4 Keep-segments algorithm (`services/segments.py`), pure function, heavily unit-tested
```
compute_keep_segments(envelope, hop, duration, fps, params) -> list[Segment(start, end)]
```
1. Mark frames below threshold as silent, with hysteresis (needs +3 dB to leave silence) so a brief dip inside a word does not split it.
2. Merge silent runs; **only silent runs with length >= `min_silence_sec` are cut.** Shorter pauses stay as part of speech (this is the user's X option).
3. For each cut silence, keep `keep_padding_sec` on both sides, so the removed part is `length - 2*padding`.
4. Drop speech segments shorter than `min_speech_sec` (but never drop everything).
5. Snap every boundary to the video frame grid (`round(t * fps) / fps`).
6. Clamp to `[0, duration]`, remove zero-length segments, sort, no overlaps.
7. If nothing would be cut (or less than about 1% of duration), return a single full segment.

### 2.5 Cut-audio render
- Render `audio_cut.wav` from `audio_dn.wav` with `atrim`+`asetpts`+`concat` (no fades; this file is only used for transcription in phase 3). Must have exactly the same total duration as the video pass will have (see tests).

### 2.6 Video pass (`services/render.py`)
- Inputs: the **original** video (`-ss chunk_start -t chunk_dur`) and `audio_dn.wav`.
- For each segment: `trim`, `setpts=PTS-STARTPTS`, `fade=in/out` (video, color from `fade_color`) and `afade=in/out`, then `concat`.
- Fade length per segment: `min(fade_sec, seg_len / 2)`.
- `fade_first_last=false`: no fade-in on the first segment and no fade-out on the last.
- Before the trim chain: `fps=<cfr>`, `scale/crop/pad` per `output.resolution` + `fit`, `format=yuv420p`, even-dimension guard (`trunc(iw/2)*2`), `setsar=1`.
- If SDR conversion is needed for HDR input, apply a `zscale`/`tonemap` chain only when probe flagged HDR and the filter exists; else warn.
- Write the graph to `graph.txt`, call with `-filter_complex_script`.
- Batching: if segments > `max_segments_per_graph`, render batches to `part_NNN.mkv` and concat them (stream copy).
- Output: `chunk_out.mkv` (h264 per `output` options + `pcm_s16le`). A hook parameter `ass_path` is accepted now and used in phase 3 (append `ass=` after concat when provided).
- Encoder selection: use the trial-tested encoder from `/api/system`. `h264_nvenc` uses `-cq` instead of `-crf`; map the options properly.

### 2.7 CLI for development
`python -m app.cli cut input.mp4 --options options.json --out out.mp4`

## Edge cases to handle

- **No silence found:** pass-through (single segment) but still apply denoise + normalization of format.
- **Entire file silent / below threshold:** fail with `NO_SPEECH` and a hint to lower the threshold or enable `auto_threshold`.
- **Constant background noise louder than the threshold:** nothing is cut; the log and UI hint suggest `auto_threshold`.
- **Very many segments (hundreds):** batching (2.6), script file for the graph.
- **Segment shorter than 2x fade:** clamp the fade length.
- **Fades on every join look like flicker:** default 0.12 s; validator blocks fade lengths that exceed the kept padding logic.
- **VFR source:** `fps=` filter before trim; cut times snapped to the grid.
- **Audio shorter than video (or the reverse):** pad audio with `apad` or trim to video duration so A/V lengths match.
- **Start offsets between streams:** zero-base both with `setpts`/`asetpts`.
- **Mono source:** stay mono, do not upmix.
- **Clipping/distortion after denoise:** alimiter guard.
- **Odd dimensions, rotation, anamorphic SAR:** handled in the video filter chain (autorotate is on by default when re-encoding; keep it).
- **Cancel during render:** partial output removed.
- **Dropped speech:** log every dropped blip with time so debugging is possible.

## Tests

- `compute_keep_segments`: table-driven with synthetic envelopes: short pause kept, long pause cut, padding respected, hysteresis, blip dropped, all silent, none silent, boundary snap, X exactly equal to a silence length.
- Synthetic media: build a clip with `lavfi` (sine tone bursts separated by digital silence plus low noise). Assert after processing: expected duration within 1 frame, `|audio_dur - video_dur| < 1 frame`, silence >= X is gone, shorter pauses remain.
- Fade check: sample the first/last frames of each segment and assert luminance rises/falls (fade present).
- 300-segment test proves batching works.

## Acceptance criteria

- [ ] CLI produces a trimmed video from a real 5-minute talking clip with audible noise reduction and visible/audible fade at each join.
- [ ] Changing `min_silence_sec` visibly changes which pauses are removed.
- [ ] A/V sync verified on VFR phone footage.
- [ ] No test or run loads the full audio into memory (check peak RSS on a 1-hour synthetic file).
- [ ] All edge cases above have a test or a documented decision in `docs/decisions.md`.
