# Phase 3: Transcription (free Whisper) and Animated Subtitles

**Goal:** generate word-level timestamps locally with faster-whisper (open source, runs offline after a one-time model download, no API cost) and burn animated subtitles with the user's colors/style.

## Tasks

### 3.1 Model manager (`api/models.py`, `services/transcribe.py`)
- Models come from Hugging Face repos `Systran/faster-whisper-<size>` via `huggingface_hub.snapshot_download` into `data/models/whisper/`.
- Endpoints: list sizes with installed flag and approximate disk size, start download with progress over SSE, cancel/resume. Lock so two downloads of the same model cannot run at once. Clean partial downloads on failure.
- Check free disk space before download. After download, loading must work fully offline (`local_files_only=True`).

### 3.2 Device selection
- `auto`: use CUDA if `ctranslate2.get_cuda_device_count() > 0` and a 1-second test transcription succeeds; compute_type `float16`. Otherwise CPU with `int8`.
- On Windows GPU needs CUDA runtime libs (cuBLAS/cuDNN). If CUDA init fails at runtime, log it, fall back to CPU automatically, and surface a warning (not a failure).
- Out-of-memory: retry once with a smaller compute type (`int8_float16`), then with CPU, and tell the user.

### 3.3 Transcribe (`transcribe(audio_wav, opts) -> list[Word]`)
- Defaults: `word_timestamps=True`, `vad_filter=True` (`min_silence_duration_ms=500`), `condition_on_previous_text=False` (prevents repetition loops), `beam_size=5` on GPU / 1-3 on CPU, `initial_prompt=vocabulary_hint` if set.
- Language: if `auto`, detect once on the first speech of the job (first chunk), then pass the same explicit language to all chunks (prevents per-chunk misdetection).
- Returns `Word{text, start, end, prob}`. Persist `words.json` per chunk (also feeds a future transcript editor).
- Interface it as a `Transcriber` protocol so tests can use a fake.

### 3.4 Word repair (`services/subtitles.py`)
- Merge standalone punctuation tokens into the previous word; strip whitespace.
- Fix zero/negative durations, overlaps, words past the audio end; minimum word duration 80 ms.
- Remove hallucinations: repeated identical words/phrases more than 3 times in a row, text emitted in regions with no speech, very low probability with near-zero duration, and common boilerplate emitted over silence/music (e.g., "thanks for watching") when the surrounding region is below the threshold.
- No words at all: skip subtitles, add a warning (`NO_SPEECH_DETECTED`), continue the job.

### 3.5 Line grouping
- Break lines by `words_per_line`, `max_chars_per_line`, sentence punctuation, and any pause greater than 0.6 s. Max `max_lines` lines per caption.
- **Measure real text width** with Pillow `ImageFont` for the chosen font and size, and wrap so width <= 85% of video width. Emit explicit `\N` line breaks and set `WrapStyle: 2`, since automatic ASS wrapping is unreliable.
- A single word wider than the screen: reduce font size for that caption.

### 3.6 ASS generation (`build_ass(words, style, play_res) -> str`)
- `PlayResX/Y` = the **final output size** (after scale/crop), `ScaledBorderAndShadow: yes`. Font size = `font_size_pct * PlayResY / 100`.
- Color conversion: `#RRGGBB` + opacity -> ASS `&HAABBGGRR` where alpha is inverted (`00` = opaque).
- Modes:
  - **karaoke_fill:** one event per line using `\kf` tags. In ASS the text starts in `SecondaryColour` and sweeps to `PrimaryColour`, so set Secondary = `text_color` and Primary = `highlight_color`. **Each `\kf` duration must run from this word's start to the next word's start (include the gap)**, and the last word to its own end. Accumulate in float and round the cumulative centiseconds to avoid drift. (Using only `end - start` makes highlighting drift ahead of the voice.)
  - **word_highlight:** one event per word, each showing the whole line with only the current word recolored; if `pop_animation`, add `\t` scale (about 110-120%) that settles back.
  - **static:** whole line for its duration.
- Background: `box` uses `BorderStyle=3` (opaque box; verify in libass which color the box uses and render a test frame to confirm). If it does not give the desired result or padding, fall back to a separate drawing event (`\p1` rounded rectangle) sized from the Pillow-measured text width, behind the text. `outline` uses `BorderStyle=1` with `background_color` as outline.
- Escape `\`, `{`, `}` in text, and handle emoji/non-Latin gracefully.
- Position -> `Alignment` (2 bottom, 5 center, 8 top) and `MarginV = margin_v_pct * PlayResY / 100`.

### 3.7 Fonts
- Bundle a few open-license fonts in `assets/fonts` (e.g., Montserrat, Poppins, Inter, Noto Sans, and one Arabic-script font such as Noto Naskh/Nastaliq for Urdu). Record licenses.
- `/api/fonts` lists the **family names read from the font files** (ASS uses family name, not file name). Copy needed fonts into the chunk workdir and use `ass=subs.ass:fontsdir=.`, running FFmpeg with `cwd=workdir`, so Windows path escaping (`C:\` colon problem) never matters.

### 3.8 Integrate into the video pass
- Orchestrator order per chunk: audio stage -> transcribe `audio_cut.wav` -> build ASS -> phase-2 video pass with `ass_path`. Timestamps already match the output timeline.

## Edge cases to handle

- Language: Urdu/Hindi/Arabic Whisper accuracy is lower than English (suggest `medium` or larger). Right-to-left text needs a font with shaping support; line grouping and karaoke direction must be tested with RTL text. Roman-Urdu is not a Whisper output language; document this limitation.
- Code-switching (Urdu + English in one video): a locked language can transcribe the other language poorly. Document it; consider an option later.
- Music/noise-only or very quiet sections: VAD prevents most hallucinations; keep the filter in 3.4.
- Fast speech: words shorter than 80 ms get merged into the line display without breaking the animation.
- Overlapping speakers: no diarization in v1. Document.
- First run: model download interrupted -> resumable, never leaves a half-model that loads.
- Model files corrupt: detect on load, offer redownload.
- Subtitle overlaps with platform UI (Reels/Shorts/TikTok): default `margin_v_pct=15`; UI hint.
- Colors with low contrast (text_color near background): UI warning only, never block.
- Very long job: model stays loaded in a persistent ASR worker (phase 5); do not reload per chunk.
- Memory: `large-v3` on CPU is slow and heavy. Show model size/speed hints, and recommend `small` default.

## Tests

- ASS: color/alpha conversion, centisecond cumulative rounding (sum of `\kf` equals line duration within 1 cs), escaping, RTL sample, box vs outline style.
- Word repair: overlaps, zero durations, hallucination loop removal.
- Line grouping with measured widths (long word case).
- Fake `Transcriber` end-to-end test; one optional slow test with the `tiny` model on a short recorded fixture.
- Visual check: render one frame per mode with FFmpeg and save to `tests/artifacts/` for manual review.

## Acceptance criteria

- [ ] Real 2-minute talking clip: subtitles are in sync, the current word changes color correctly in both animated modes.
- [ ] Changing text color, highlight color, background mode/color/opacity, font, size, position changes the output as expected.
- [ ] Works fully offline after the model download.
- [ ] GPU absent or broken: automatic CPU fallback with a warning.
- [ ] Urdu/Arabic sample renders correct glyph shapes (even if the transcription itself is imperfect).
