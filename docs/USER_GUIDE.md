# Cinelume user guide

Cinelume turns a talking-head social video into a ready-to-post clip: it removes audio
noise, cuts long silences with a fade at every join, burns animated word-by-word
subtitles, mixes low background music and renders a cover image.

Start the app with `scripts\start.bat` and open <http://127.0.0.1:8000>.

## Upload

Drag a video onto the page (mp4, mov, mkv, webm). Uploads are chunked, so a 2 GB file
works and a dropped connection resumes where it stopped.

The file card shows the probe result: resolution, duration, fps, audio presence and any
warnings (VFR source, HDR, no audio, odd dimensions, multiple audio tracks).

## Configure

Every section is collapsible; only Silence starts open.

| Section | What it does |
|---|---|
| **Silence** | Cuts pauses longer than X seconds. The threshold is what counts as silence — if nothing is cut, raise the threshold or switch on **auto threshold**. *Keep padding* is how much silence stays around each cut so words are not clipped. Fades hide the jump. |
| **Subtitles** | The model (small is a good default), language, animation mode, colours, font, size, position and margins. *Margin from edge* keeps text clear of TikTok/Reels UI. |
| **Music** | Pick or upload a track. Volume 5–15 is a typical background level; 0 disables the mix. Ducking lowers the music while someone is talking. You are responsible for having the rights to the track. |
| **Cover** | A still frame with text. Random mode samples frames and keeps the sharpest, non-black one; **Re-roll** tries again with a new seed without re-running the job. |
| **Output** | Resolution (vertical 1080×1920 for Reels/Shorts), quality (CRF, lower = better), encoder and loudness normalization. |

"Start editing" queues the job.

## Progress

The overall bar is weighted by duration, not by step count, and shows an ETA. Each
chunk is a tile: queued, running (with its own mini progress), done, failed or skipped
(a chunk that is entirely silent). Cancel kills everything within a couple of seconds.

## Result

Play the video, download it, the transcript JSON, the log, and the cover. Re-run with
different options from History — unchanged stages are reused, so tweaking a subtitle
colour is fast.

## History / System

History lists every job with resume and delete. System shows the FFmpeg build, hardware
encoders, RAM, CUDA availability, downloaded Whisper models and the bundled fonts.

## Tips

- **Nothing was cut?** Raise the threshold towards -25 dB or enable auto threshold.
- **Cut too much?** Lower the threshold towards -50 dB, or raise X.
- **Clicks at the joins?** Keep the fade enabled (0.12 s is a good default).
- **Subtitles out of sync on phone footage?** That is fixed automatically; make sure you
  are not re-encoding with another tool first.
- **Wrong language detected?** Lock the language in the Subtitles section.
- **Urdu/Hindi/Arabic accuracy is lower** than English — use `medium` or `large-v3`.
