"""Cut-audio render (2.5) and the single-pass video render (2.6).

The video stream is lossy-encoded exactly once: trim + fades + concat + scale/crop +
burn-in happen in one ffmpeg pass from the ORIGINAL file. Chunk output is
h264 + PCM audio so later joins have no AAC priming gaps.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import soundfile as sf

from ..core.errors import ErrorCode, PipelineError
from ..models.chunk import ChunkSpec
from ..models.media import MediaInfo
from ..models.options import Encoder, Fit, JobOptions, Resolution
from .ffmpeg import capabilities, has_filter, run_ffmpeg
from .segments import Segment

ProgressCallback = Callable[[float], None]  # percent 0..100

# x264 preset -> nvenc preset (nvenc has no "veryfast").
_NVENC_PRESET = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p3",
    "faster": "p4",
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
}


@dataclass(frozen=True)
class Placed:
    """A kept segment together with its global first/last fade flags."""

    segment: Segment
    fade_in: bool
    fade_out: bool


def playback_fps(options: JobOptions, info: MediaInfo) -> int:
    if options.output.fps:
        return int(options.output.fps)
    fps = info.fps or info.r_fps or 30.0
    if fps <= 0:
        fps = 30.0
    return max(15, min(60, int(round(fps))))


def build_placed(segments: list[Segment], options: JobOptions) -> list[Placed]:
    """Apply the fade_first_last rule: interior joins always fade, ends optionally."""
    silence = options.silence
    count = len(segments)
    placed: list[Placed] = []
    for index, segment in enumerate(segments):
        if not silence.fade_enabled:
            placed.append(Placed(segment, False, False))
            continue
        fade_in = (index > 0) or silence.fade_first_last
        fade_out = (index < count - 1) or silence.fade_first_last
        placed.append(Placed(segment, fade_in, fade_out))
    return placed


def resolve_encoder(options: JobOptions, caps: dict, warnings: list[str]) -> str:
    requested = options.output.encoder
    if requested is Encoder.AUTO:
        hardware = caps.get("hardware_encoders", [])
        return hardware[0] if hardware else "libx264"
    name = requested.value
    if name == "libx264" or caps.get("encoders", {}).get(name):
        return name
    warnings.append(f"{name} is unavailable; falling back to libx264.")
    return "libx264"


def encode_args(encoder: str, options: JobOptions) -> list[str]:
    crf = str(options.output.crf)
    preset = options.output.preset.value
    if encoder == "h264_nvenc":
        return [
            "-c:v",
            encoder,
            "-preset",
            _NVENC_PRESET.get(preset, "p4"),
            "-rc",
            "vbr",
            "-cq",
            crf,
        ]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", preset, "-global_quality", crf]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "speed", "-rc", "cqp", "-qp_i", crf, "-qp_p", crf]
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf]


def _video_prechain(options: JobOptions, fps: int, tonemap: bool) -> str:
    parts: list[str] = []
    if tonemap:
        parts += [
            "zscale=t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
            "format=yuv420p",
        ]
    parts.append(f"fps={fps}")
    resolution = options.output.resolution
    if resolution is Resolution.ORIGINAL:
        parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    else:
        width, height = resolution.value.split("x")
        if options.output.fit is Fit.CROP:
            parts.append(f"scale={width}:{height}:force_original_aspect_ratio=increase")
            parts.append(f"crop={width}:{height}")
        else:
            parts.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
            parts.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    parts.append("setsar=1")
    parts.append("format=yuv420p")
    return ",".join(parts)


def _fade_len(segment: Segment, fade_sec: float) -> float:
    return max(0.0, min(fade_sec, segment.duration / 2.0))


def _build_graph(
    placed: list[Placed],
    options: JobOptions,
    channels: int,
    fps: int,
    tonemap: bool,
    ass_name: str | None,
    with_audio: bool,
) -> str:
    silence = options.silence
    color = "0x" + silence.fade_color.lstrip("#")
    count = len(placed)
    lines: list[str] = []

    video_labels = "".join(f"[pv{i}]" for i in range(count))
    lines.append(f"[0:v]{_video_prechain(options, fps, tonemap)},split={count}{video_labels}")

    if with_audio:
        layout = "mono" if channels == 1 else "stereo"
        audio_labels = "".join(f"[pa{i}]" for i in range(count))
        lines.append(
            f"[1:a]asetpts=PTS-STARTPTS,"
            f"aformat=sample_fmts=s16:sample_rates=48000:channel_layouts={layout},"
            f"apad,asplit={count}{audio_labels}"
        )

    for i, placed_seg in enumerate(placed):
        seg = placed_seg.segment
        fade = _fade_len(seg, silence.fade_sec)
        vf = [f"trim=start={seg.start:.6f}:end={seg.end:.6f}", "setpts=PTS-STARTPTS"]
        if silence.fade_enabled and silence.fade_video and fade > 0:
            if placed_seg.fade_in:
                vf.append(f"fade=t=in:st=0:d={fade:.4f}:color={color}")
            if placed_seg.fade_out:
                start = max(0.0, seg.duration - fade)
                vf.append(f"fade=t=out:st={start:.6f}:d={fade:.4f}:color={color}")
        lines.append(f"[pv{i}]{','.join(vf)}[v{i}]")

        if with_audio:
            af = [f"atrim=start={seg.start:.6f}:end={seg.end:.6f}", "asetpts=PTS-STARTPTS"]
            if silence.fade_enabled and silence.fade_audio and fade > 0:
                if placed_seg.fade_in:
                    af.append(f"afade=t=in:st=0:d={fade:.4f}")
                if placed_seg.fade_out:
                    af.append(f"afade=t=out:st={max(0.0, seg.duration - fade):.6f}:d={fade:.4f}")
            lines.append(f"[pa{i}]{','.join(af)}[a{i}]")

    if with_audio:
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(count))
        if ass_name:
            lines.append(f"{concat_inputs}concat=n={count}:v=1:a=1[cv][outa]")
            lines.append(f"[cv]ass=filename={ass_name}:fontsdir=.[outv]")
        else:
            lines.append(f"{concat_inputs}concat=n={count}:v=1:a=1[outv][outa]")
    else:
        concat_inputs = "".join(f"[v{i}]" for i in range(count))
        if ass_name:
            lines.append(f"{concat_inputs}concat=n={count}:v=1:a=0[cv]")
            lines.append(f"[cv]ass=filename={ass_name}:fontsdir=.[outv]")
        else:
            lines.append(f"{concat_inputs}concat=n={count}:v=1:a=0[outv]")

    return ";\n".join(lines) + "\n"


def _stage_ass(workdir: Path, ass_path: Path | None) -> str | None:
    if ass_path is None:
        return None
    source = Path(ass_path)
    if not source.exists():
        raise PipelineError(ErrorCode.NOT_FOUND, "Subtitle file not found.", stage="render")
    target = workdir / "subs.ass"
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return target.name


def render_cut_audio(
    workdir: Path,
    audio_dn: Path,
    segments: list[Segment],
    channels: int,
    cancel_event: Any | None = None,
) -> Path:
    """Concat trimmed, zero-based audio ranges. Used only for transcription (no fades)."""
    if not segments:
        raise PipelineError(ErrorCode.FFMPEG_FAILED, "No segments to render.", stage="render")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "audio_cut.wav"

    lines = [
        f"[0:a]asetpts=PTS-STARTPTS,apad,asplit={len(segments)}"
        + "".join(f"[pre{i}]" for i in range(len(segments)))
    ]
    for i, seg in enumerate(segments):
        lines.append(
            f"[pre{i}]atrim=start={seg.start:.6f}:end={seg.end:.6f},asetpts=PTS-STARTPTS[a{i}]"
        )
    inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    lines.append(f"{inputs}concat=n={len(segments)}:v=0:a=1[out]")
    graph_path = workdir / "audio_cut.graph.txt"
    graph_path.write_text(";\n".join(lines) + "\n", encoding="utf-8")

    info = sf.info(str(audio_dn))
    duration = float(info.frames) / float(info.samplerate)
    run_ffmpeg(
        [
            "-i",
            str(audio_dn),
            "-filter_complex_script",
            str(graph_path),
            "-map",
            "[out]",
            "-ac",
            str(channels),
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(out),
        ],
        cwd=workdir,
        timeout=max(120.0, 6 * duration),
        cancel_event=cancel_event,
        log_path=workdir / "ffmpeg.log",
    )
    return out


def render_video(
    workdir: Path,
    chunk: ChunkSpec,
    source: Path,
    audio_dn: Path | None,
    placed: list[Placed],
    options: JobOptions,
    info: MediaInfo,
    fps: int,
    channels: int,
    ass_path: Path | None = None,
    warnings: list[str] | None = None,
    cancel_event: Any | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    if not placed:
        raise PipelineError(ErrorCode.FFMPEG_FAILED, "No segments to render.", stage="render")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    warnings = warnings if warnings is not None else []

    caps = capabilities()
    encoder = resolve_encoder(options, caps, warnings)
    tonemap = info.is_hdr and has_filter("zscale") and has_filter("tonemap")
    if info.is_hdr and not tonemap:
        warnings.append(
            "HDR source but tonemap filters are unavailable; colors may look washed out."
        )
    ass_name = _stage_ass(workdir, ass_path)
    with_audio = audio_dn is not None

    expected = sum(item.segment.duration for item in placed) or 1.0
    timeout = max(120.0, 6 * chunk.duration)
    max_segments = max(20, options.performance.max_segments_per_graph)
    batches = [placed[i : i + max_segments] for i in range(0, len(placed), max_segments)]
    done = 0.0

    def _progress(done_before: float, batch_duration: float):
        def _cb(progress: dict[str, Any]) -> None:
            if on_progress is None:
                return
            current = min(float(progress.get("out_time_sec", 0.0)), batch_duration)
            on_progress(min(100.0, (done_before + current) / expected * 100.0))

        return _cb

    def _render_batch(batch: list[Placed], out_path: Path, done_before: float) -> None:
        graph = _build_graph(batch, options, channels, fps, tonemap, ass_name, with_audio)
        graph_path = workdir / f"{out_path.stem}.graph.txt"
        graph_path.write_text(graph, encoding="utf-8")

        args: list[str] = []
        if chunk.src_start > 0:
            args += ["-ss", f"{chunk.src_start:.6f}"]
        args += ["-t", f"{chunk.duration:.6f}", "-i", str(source)]
        if with_audio:
            args += ["-i", str(audio_dn)]
        args += ["-filter_complex_script", str(graph_path), "-map", "[outv]"]
        if with_audio:
            args += ["-map", "[outa]"]
        args += encode_args(encoder, options)
        args += ["-pix_fmt", "yuv420p"]
        if with_audio:
            args += ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", str(channels)]
        # Explicit output frame rate: `-fps_mode cfr` alone defaults to 25 fps.
        args += ["-r", str(fps), "-y", str(out_path)]

        batch_duration = sum(item.segment.duration for item in batch) or 1.0
        run_ffmpeg(
            args,
            cwd=workdir,
            timeout=timeout,
            cancel_event=cancel_event,
            log_path=workdir / "ffmpeg.log",
            on_progress=_progress(done_before, batch_duration) if on_progress else None,
        )

    if len(batches) == 1:
        out = workdir / "chunk_out.mkv"
        _render_batch(batches[0], out, 0.0)
        return out

    parts: list[Path] = []
    for index, batch in enumerate(batches):
        part = workdir / f"part_{index:03d}.mkv"
        _render_batch(batch, part, done)
        parts.append(part)
        done += sum(item.segment.duration for item in batch)

    manifest = workdir / "concat.txt"
    manifest.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    out = workdir / "chunk_out.mkv"
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-fflags",
            "+genpts",
            "-i",
            manifest.name,
            "-c",
            "copy",
            "-y",
            out.name,
        ],
        cwd=workdir,
        timeout=timeout,
        cancel_event=cancel_event,
        log_path=workdir / "ffmpeg.log",
    )
    return out
