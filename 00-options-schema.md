# 00 Options Schema (single source of truth)

Implement as pydantic v2 models in `backend/app/models/options.py`. Frontend types are generated from the OpenAPI schema. The server is the validator; the UI shows server field errors next to the control.

Colors are `#RRGGBB` strings. Percent-of-height values keep styling resolution independent.

## denoise
| Key | Type | Default | Range / values | Notes |
|---|---|---|---|---|
| enabled | bool | true | | |
| method | enum | `afftdn` | `afftdn`, `arnndn`, `noisereduce` | `arnndn` needs an RNNoise model file (download in setup, check license). `noisereduce` runs in-memory per chunk only |
| strength | int | 50 | 0-100 | Mapped to the method's own parameters |
| highpass_hz | int | 80 | 0-300 | 0 = off. Removes rumble |

## silence
| Key | Type | Default | Range | Notes |
|---|---|---|---|---|
| enabled | bool | true | | |
| min_silence_sec | float | 0.5 | 0.1-10 | **X**: only silences at least this long are cut. Shorter pauses are kept untouched |
| threshold_db | float | -35 | -60 to -15 | |
| auto_threshold | bool | false | | Estimate from the noise floor instead of using `threshold_db` |
| keep_padding_sec | float | 0.12 | 0-1 | Silence kept on each side of a cut, so words are not clipped |
| min_speech_sec | float | 0.25 | 0-2 | Speech blips shorter than this (clicks, coughs) are dropped |
| fade_enabled | bool | true | | Fade at every join |
| fade_sec | float | 0.12 | 0.03-0.5 | |
| fade_video | bool | true | | |
| fade_audio | bool | true | | |
| fade_color | color | `#000000` | | Video fade-to color |
| fade_first_last | bool | false | | Also fade in at the very start and out at the very end |

Validators: `min_silence_sec > 2 * keep_padding_sec`; `fade_sec * 2 <= min_speech_sec + 2 * keep_padding_sec`.

## subtitles
| Key | Type | Default | Range / values | Notes |
|---|---|---|---|---|
| enabled | bool | true | | |
| model_size | enum | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` | |
| language | str | `auto` | `auto` or ISO code | Detected once, then locked for all chunks |
| device | enum | `auto` | `auto`, `cpu`, `cuda` | |
| vocabulary_hint | str | "" | max 200 chars | Names/brands, passed as `initial_prompt` |
| mode | enum | `karaoke_fill` | `karaoke_fill`, `word_highlight`, `static` | fill = smooth color sweep; highlight = current word recolored (+ optional pop); static = no animation |
| font_family | str | `Montserrat` | must exist in `/api/fonts` | |
| font_size_pct | float | 4.5 | 2-10 | % of video height |
| bold | bool | true | | |
| text_color | color | `#FFFFFF` | | Foreground, not-yet-spoken words |
| highlight_color | color | `#FFD400` | | Current / spoken word |
| background_mode | enum | `none` | `none`, `box`, `outline` | |
| background_color | color | `#000000` | | Box or outline color |
| background_opacity | int | 60 | 0-100 | |
| outline_width_pct | float | 0.25 | 0-1 | % of video height |
| shadow | bool | true | | |
| position | enum | `bottom` | `top`, `center`, `bottom` | |
| margin_v_pct | float | 15 | 0-40 | Keeps text out of platform UI overlays |
| words_per_line | int | 4 | 1-8 | |
| max_chars_per_line | int | 28 | 10-60 | |
| max_lines | int | 2 | 1-3 | |
| uppercase | bool | false | | |
| pop_animation | bool | false | | Only for `word_highlight` |

## music
| Key | Type | Default | Range | Notes |
|---|---|---|---|---|
| enabled | bool | false | | **On / off** |
| track_id | str? | null | | From the music library; required when enabled |
| volume | int | 8 | 0-100 | Applied to the loudness-normalized track. 5-15 is a typical background level |
| loop | bool | true | | |
| fade_in_sec | float | 1.5 | 0-10 | |
| fade_out_sec | float | 2.0 | 0-10 | |
| start_offset_sec | float | 0 | 0-600 | Skip the start of the track |
| ducking_enabled | bool | false | | Lower music while speech is present |
| ducking_strength | int | 50 | 0-100 | |

## cover
| Key | Type | Default | Range | Notes |
|---|---|---|---|---|
| enabled | bool | true | | |
| time_mode | enum | `random` | `random`, `manual` | |
| time_sec | float? | null | | for `manual` |
| seed | int? | null | | Reproducible random; the chosen time is returned so the UI can show/re-roll |
| candidates | int | 5 | 1-12 | Random mode samples N frames and keeps the sharpest, non-black one |
| lines | list[str] | [] | max 4 lines | |
| font_family | str | `Montserrat` | | |
| font_size_pct | float | 7 | 2-20 | |
| text_color | color | `#FFFFFF` | | |
| stroke_color | color | `#000000` | | |
| stroke_width_pct | float | 0.4 | 0-2 | |
| strip_enabled | bool | false | | Semi-transparent strip behind text |
| strip_color / strip_opacity | color / int | `#000000` / 55 | 0-100 | |
| position | enum | `bottom` | `top`, `center`, `bottom` | |
| aspect | enum | `same_as_video` | `same_as_video`, `9:16`, `1:1`, `16:9`, `4:5` | Center crop |
| format | enum | `png` | `png`, `jpg` | |

## output
| Key | Type | Default | Range / values | Notes |
|---|---|---|---|---|
| resolution | enum | `original` | `original`, `1080x1920`, `1920x1080`, `1080x1080`, `720x1280` | |
| fit | enum | `crop` | `crop`, `pad` | When aspect differs from the source |
| fps | int? | null | 15-60 | null = source average fps, rounded, capped at 60 |
| crf | int | 23 | 14-35 | |
| preset | enum | `veryfast` | `ultrafast` ... `slow` | |
| encoder | enum | `auto` | `auto`, `libx264`, `h264_nvenc`, `h264_qsv`, `h264_amf` | `auto` tests the hardware encoder with a 1-frame trial and falls back to libx264 |
| audio_bitrate_kbps | int | 192 | 96-320 | |
| loudnorm | bool | true | | Final loudness normalization |
| target_lufs | float | -14 | -24 to -9 | Typical social platform level |
| audio_track_index | int | 0 | | If the file has several audio tracks |

## performance (advanced)
| Key | Type | Default | Range | Notes |
|---|---|---|---|---|
| chunking | enum | `auto` | `auto`, `off`, `on` | |
| chunk_threshold_sec | int | 240 | 60-3600 | `auto` chunks videos longer than this |
| target_chunk_sec | int | 120 | 30-600 | |
| boundary_window_sec | int | 20 | 5-60 | Search window around each target boundary for a silence |
| workers | int? | null | 1-16 | null = auto |
| asr_workers | int | 1 | 1-4 | |
| max_segments_per_graph | int | 120 | 20-400 | |
| degrade_on_asr_failure | bool | false | | If a chunk's transcription fails, render that chunk without subtitles and warn |
| keep_intermediates | bool | false | | Debug only |

## Global limits (server settings, not per job)

`MAX_UPLOAD_GB=10`, `MAX_DURATION_MIN=180`, `MAX_CONCURRENT_JOBS=1`, `UPLOAD_TTL_HOURS=24`, `WORK_TTL_HOURS=24`.
