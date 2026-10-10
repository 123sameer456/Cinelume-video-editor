# Phase 4: Background Music, Cover Image, Final Audio Master

**Goal:** add optional low-volume background music, produce the final MP4 with one loudness-normalized AAC track, and generate a cover image with text from a frame of the original video.

## Tasks

### 4.1 Music library (`api/music.py`, `services/music.py`)
- Upload mp3/wav/m4a/ogg/flac. Validate by probing. Store under `data/music/<id>/` with metadata (name, duration, measured integrated loudness via `ebur128`).
- Pre-normalize each track once to a reference loudness (about -20 LUFS) and cache `normalized.wav`. This makes the `volume` option behave consistently across tracks.
- List / upload / delete endpoints. UI shows a notice that the user is responsible for having the rights to use the track (Content ID claims on social platforms).
- Do not bundle copyrighted music.

### 4.2 Master stage (`services/master.py`)
Input: merged video from phase 5 (for now, the phase 2/3 output). Video stream is copied, audio is processed once:
1. Speech track = video's PCM audio.
2. If `music.enabled`: build the music bed: `atrim` from `start_offset_sec`, loop (`aloop` or `-stream_loop -1`) to cover the video length, trim to video duration, `volume = music.volume / 100`, `afade` in and out.
3. Optional ducking: `sidechaincompress` with the speech as the sidechain; map `ducking_strength` 0-100 to threshold/ratio/release. Speech must be mixed in **after** being used as sidechain.
4. Mix with `amix=inputs=2:duration=first:normalize=0` (check the FFmpeg version supports `normalize`; otherwise use `weights` + gain compensation).
5. If `output.loudnorm`: two-pass `loudnorm` (measure, then apply with measured values, `target_lufs`, true peak -1.5 dB). Guard: if the measured loudness is `-inf` (silent audio), skip.
6. Encode AAC at `audio_bitrate_kbps`, mux with `-c:v copy -movflags +faststart` into `final.mp4`.
- If music is off and loudnorm is off: still do the final remux to MP4/AAC (cheap).

### 4.3 Cover (`services/cover.py`, `POST /api/cover`)
- Frame source is the **original video** (so no burned subtitles), autorotated.
- `random` mode: seeded RNG, choose N candidate times in `[1s, duration-1s]` (clamped for very short videos), extract frames, score each:
  - reject near-black frames (mean luma < 16) and near-white,
  - sharpness = variance of an edge-filtered (Pillow `FIND_EDGES`) grayscale image,
  - pick the best. Return `{time_sec, image_url}` so the UI can show the time and offer "re-roll" (new seed).
- `manual` mode: exact `time_sec`.
- Crop to `aspect` (center crop).
- Text via Pillow: up to 4 lines, wrapped by **measured width** (max 85% of image width), auto-shrink if still too tall, stroke, optional semi-transparent strip, safe margins. Uses the same `/api/fonts` families.
- RTL/Arabic-script text: check that Pillow has RAQM layout (`features.check("raqm")`); if not, use `arabic-reshaper` + `python-bidi`.
- Output PNG or JPG into the job outputs folder and as a standalone result of `POST /api/cover`, so the user can re-roll without re-running the video job.

## Edge cases to handle

- Music shorter than the video: loop; add a short crossfade at the loop point (about 0.5 s) to avoid a click.
- Music longer than the video: trim and fade out at the end of the video, not at the end of the track.
- Music sample rate or channel layout differs from the speech: `aresample` + `aformat` before mixing.
- Music track with silence at its head/tail: option `start_offset_sec`; optionally `silenceremove` at the track edges when caching the normalized version.
- Corrupt music file: validate at upload; at job time return a clear error (`UNSUPPORTED_MEDIA`) instead of an FFmpeg dump.
- `music.enabled` with no `track_id`: validation error before the job starts.
- Video without audio but music enabled: create the audio track from music alone (no sidechain, no ducking).
- Loudnorm on silent or extremely short audio: guarded (see 4.2).
- Volume 0 with music enabled: treat as disabled (skip the mix) and warn.
- Very short video (< 2 s): cover candidate range falls back to the middle frame.
- HDR source: the cover frame must be tone-mapped or it looks washed out.
- Rotated phone video: extracted frame must be upright.
- Text too long for any font size: truncate with an ellipsis and warn in the response.
- Missing glyphs for the chosen font: fall back to a Noto font and warn.
- Cover requested for a job whose upload was already deleted: return 404 with a clear message.

## Tests

- Mix level test: render speech-only vs speech+music; measure that music RMS is the expected ratio below speech for volume 8 and 20.
- Loop test: 5 s music, 60 s video, no discontinuity (energy check around loop point).
- Ducking test: music level lower during speech than during pauses.
- Loudness test: output integrated loudness within +/- 1 LU of target.
- Cover: seeded randomness reproducible, black-frame rejection, text wrapping and auto-shrink, RTL sample.

## Acceptance criteria

- [ ] Toggle music on/off and set volume 0-100; levels audibly and measurably change.
- [ ] Final MP4 plays everywhere (H.264 + AAC, faststart), loudness near the target.
- [ ] Cover generates in under 3 seconds on a normal laptop, re-roll works without re-running the job.
- [ ] No-audio, short-music, and corrupt-music cases behave as specified.
