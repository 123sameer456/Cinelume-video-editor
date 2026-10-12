"""Final audio master (phase 4.2): music mix, loudness normalization, AAC encode.

The video stream is copied (encoded exactly once upstream); audio is processed once, on
the whole file, so no chunk ever has a different volume.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.errors import ErrorCode, PipelineError
from ..models.media import MediaInfo
from ..models.options import JobOptions
from .ffmpeg import parse_loudnorm, run_ffmpeg, run_ffmpeg_capture
from .music import normalize_track, source_path
from .probe import probe

TARGET_TRUE_PEAK = -1.5
_SILENT_LUFS = -70.0  # below this the loudnorm measurement is treated as -inf


def _ducking_params(strength: int) -> tuple[float, float, float]:
    """Map ducking_strength 0-100 to (threshold as a LINEAR level, ratio, release ms).

    sidechaincompress' `threshold` is a linear amplitude in [0.000976563, 1], not dB.
    """
    s = max(0, min(100, strength)) / 100.0
    threshold_db = -50.0 + s * 40.0  # -50..-10 dB
    threshold = round(10 ** (threshold_db / 20.0), 6)
    ratio = 1.0 + s * 9.0  # 1..10
    release = 500.0 - s * 400.0  # 500..100 ms
    return threshold, round(ratio, 2), round(release, 1)


def _audio_graph(
    options: JobOptions,
    info: MediaInfo,
    duration: float,
    music_input: int | None,
) -> str:
    """Build the filter_complex that produces [aout] from speech and (optionally) music."""
    music = options.music
    speech_index = min(options.output.audio_track_index, max(0, len(info.audio_streams) - 1))
    lines: list[str] = []
    fmt = "aformat=sample_fmts=s16:sample_rates=48000"

    if music_input is None:
        if info.has_audio:
            lines.append(f"[0:a:{speech_index}]{fmt}[aout]")
        return ";\n".join(lines)

    use_ducking = music.ducking_enabled and info.has_audio
    bed_start = music.start_offset_sec
    music_bed = (
        f"[{music_input}:a]atrim=start={bed_start:.3f}:end={bed_start + duration:.3f},"
        f"asetpts=PTS-STARTPTS,volume={music.volume / 100.0:.4f}"
    )
    if music.fade_in_sec > 0:
        music_bed += f",afade=t=in:st=0:d={music.fade_in_sec:.3f}"
    if music.fade_out_sec > 0:
        start = max(0.0, duration - music.fade_out_sec)
        music_bed += f",afade=t=out:st={start:.3f}:d={music.fade_out_sec:.3f}"
    lines.append(music_bed + "[mus]")

    if not info.has_audio:
        # Video without audio: the music alone becomes the track (no sidechain).
        lines.append("[mus]aformat=sample_fmts=s16:sample_rates=48000[aout]")
        return ";\n".join(lines)

    if use_ducking:
        threshold, ratio, release = _ducking_params(music.ducking_strength)
        lines.append(f"[0:a:{speech_index}]{fmt},asplit=2[spk1][spk2]")
        lines.append(
            f"[mus][spk1]sidechaincompress=threshold={threshold}:ratio={ratio}:release={release}[ducked]"
        )
        lines.append("[spk2][ducked]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        lines.append(f"[0:a:{speech_index}]{fmt}[spk]")
        lines.append("[spk][mus]amix=inputs=2:duration=first:normalize=0[aout]")
    return ";\n".join(lines)


def run_master(
    video: Path,
    options: JobOptions,
    out_path: Path,
    *,
    workdir: Path | None = None,
    cancel_event: Any | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Music mix -> loudness normalize -> AAC -> final.mp4 (video stream copied)."""
    info = probe(video)
    workdir = Path(workdir) if workdir else out_path.parent
    workdir.mkdir(parents=True, exist_ok=True)
    if info.duration <= 0:
        raise PipelineError(
            ErrorCode.UNSUPPORTED_MEDIA, "The video has zero duration.", stage="master"
        )
    duration = info.duration
    music = options.music
    output = options.output

    use_music = music.enabled and bool(music.track_id) and music.volume > 0
    if music.enabled and music.volume == 0:
        use_music = False

    music_path: Path | None = None
    music_input: int | None = None
    if use_music:
        try:
            source_path(music.track_id)
        except PipelineError as exc:
            raise PipelineError(
                ErrorCode.NOT_FOUND, "The selected music track no longer exists.", stage="master"
            ) from exc
        music_path = normalize_track(music.track_id)
        music_input = 1

    graph = _audio_graph(options, info, duration, music_input)

    if not info.has_audio and music_path is None:
        # No audio at all: copy the video stream only (nothing to measure or normalize).
        args = [
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(out_path),
        ]
        run_ffmpeg(args, cwd=out_path.parent, timeout=max(300.0, 6 * duration),
                   cancel_event=cancel_event, log_path=out_path.parent / "master.log")
        return out_path

    loudnorm_filter: str | None = None

    if output.loudnorm and info.has_audio:
        measured = _measure_mixed(graph, video, music_path, workdir, cancel_event)
        if measured.get("input_i", _SILENT_LUFS - 1) <= _SILENT_LUFS:
            # Silent audio: skip normalization rather than produce nonsense.
            loudnorm_filter = None
        else:
            loudnorm_filter = (
                f"loudnorm=I={output.target_lufs}:TP={TARGET_TRUE_PEAK}:LRA=7"
                f":measured_I={measured['input_i']}"
                f":measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}"
                f":measured_thresh={measured['input_thresh']}"
                f":offset={measured.get('target_offset', 0.0)}"
                ":linear=true"
            )

    args: list[str] = ["-i", str(video)]
    if music_path is not None:
        args += ["-stream_loop", "-1", "-i", str(music_path)]

    final_graph = graph if loudnorm_filter is None else f"{graph};[aout]{loudnorm_filter}[out]"
    out_label = "aout" if loudnorm_filter is None else "out"
    graph_path = workdir / "master.graph.txt"
    graph_path.write_text(final_graph + "\n", encoding="utf-8")

    args += ["-filter_complex_script", str(graph_path), "-map", "0:v:0", "-map", f"[{out_label}]"]
    args += ["-c:v", "copy", "-c:a", "aac", "-b:a", f"{output.audio_bitrate_kbps}k"]
    args += ["-movflags", "+faststart", "-y", str(out_path)]
    run_ffmpeg(
        args,
        cwd=out_path.parent,
        timeout=max(300.0, 6 * duration),
        cancel_event=cancel_event,
        log_path=out_path.parent / "master.log",
    )
    if not out_path.exists():
        raise PipelineError(
            ErrorCode.FFMPEG_FAILED, "Master stage produced no output.", stage="master"
        )
    return out_path


def _measure_mixed(
    graph: str,
    video: Path,
    music_path: Path | None,
    workdir: Path,
    cancel_event: Any | None,
) -> dict[str, float]:
    """Measure the mixed audio's loudness (loudnorm's first pass)."""
    measure_graph = f"{graph};[aout]loudnorm=print_format=json[out]"
    graph_path = workdir / "master_measure.graph.txt"
    graph_path.write_text(measure_graph + "\n", encoding="utf-8")

    args: list[str] = ["-i", str(video)]
    if music_path is not None:
        args += ["-stream_loop", "-1", "-i", str(music_path)]
    args += ["-filter_complex_script", str(graph_path), "-map", "[out]", "-f", "null", "-"]

    stderr = run_ffmpeg_capture(
        args,
        cwd=workdir,
        timeout=max(300.0, 6 * 60.0),
        cancel_event=cancel_event,
        log_path=workdir / "master.log",
        loglevel="info",
    )
    return parse_loudnorm(stderr)
