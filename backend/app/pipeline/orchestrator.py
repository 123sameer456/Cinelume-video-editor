"""Job orchestrator: chunk planner, two worker pools, pipelined stages, merge, resume.

Phase 5. Stage A (audio) -> B (transcribe) -> C (video pass) run in parallel across
chunks; the Whisper model is loaded once per ASR worker; progress is duration-weighted;
resume skips stages whose `.done` marker hash still matches.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import queue as queue_mod
import shutil
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

import psutil

from ..core.config import Settings, get_settings
from ..core.errors import ErrorCode, PipelineError
from ..core.logging import get_logger, job_logger
from ..core.paths import chunk_dir, ensure_dir, output_dir, work_dir
from ..models.chunk import ChunkSpec
from ..models.job import ChunkProgress, ChunkStatus, JobStatus, ProgressSnapshot
from ..models.media import MediaInfo
from ..models.options import JobOptions
from ..models.subtitle import Word
from ..services.chunker import plan_chunks
from ..services.cover import render_cover
from ..services.master import run_master
from ..services.merger import cleanup_chunks, finalize_outputs, merge_chunks, verify_merge
from ..services.render import playback_fps
from ..services.uploads import get_upload, source_path
from ..storage.jobs_db import JobsDB
from ..workers.media_pool import compute_worker_count, create_media_pool
from .progress import ProgressTracker
from .stage_entries import stage_audio_entry, stage_transcribe_entry, stage_video_entry

log = get_logger("cinelume.jobs")

TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
_STAGE_ATTEMPTS = 3


class JobManager:
    def __init__(self, db: JobsDB, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._sem = asyncio.Semaphore(max(1, self.settings.max_concurrent_jobs))
        self._workers: list[asyncio.Task] = []
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._cancel_events: dict[str, Any] = {}
        self._progress_queues: dict[str, Any] = {}
        self._last_publish: dict[str, float] = {}
        self._eta_samples: dict[str, deque] = {}
        self._manager: Any = None
        self._pool: ProcessPoolExecutor | None = None
        self._asr_pool: ProcessPoolExecutor | None = None
        self._mp_ctx = multiprocessing.get_context("spawn")
        self._shutdown = False
        self.workers = compute_worker_count()

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._shutdown = False
        self._manager = self._mp_ctx.Manager()
        self._pool = create_media_pool(self.workers)
        log.info(f"job manager started (media workers={self.workers})")

        interrupted = self.db.mark_running_interrupted()
        if interrupted:
            log.info(f"marked {interrupted} running job(s) as interrupted")
        for job in self.db.list_jobs():
            if job["status"] == JobStatus.QUEUED.value:
                self._queue.put_nowait(job["id"])

        for _ in range(max(1, self.settings.max_concurrent_jobs)):
            self._workers.append(asyncio.create_task(self._worker_loop(), name="cinelume-worker"))

    async def shutdown(self) -> None:
        self._shutdown = True
        for event in list(self._cancel_events.values()):
            try:
                event.set()
            except Exception:
                pass
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._shutdown_pools()
        if self._manager is not None:
            self._manager.shutdown()
            self._manager = None

    def _shutdown_pools(self) -> None:
        for pool in (self._pool, self._asr_pool):
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
        self._pool = None
        self._asr_pool = None

    def _recreate_pools(self, options: JobOptions | None = None) -> None:
        self._shutdown_pools()
        self.workers = compute_worker_count()
        self._pool = create_media_pool(self.workers)
        try:
            self._ensure_asr_pool(options)
        except PipelineError:
            pass
        log.info(f"pools recreated (media workers={self.workers})")

    def _ensure_asr_pool(self, options: JobOptions | None = None) -> ProcessPoolExecutor | None:
        """Persistent ASR workers: the model is loaded once per worker by the initializer."""
        if self._asr_pool is not None:
            return self._asr_pool
        if options is None or not options.subtitles.enabled:
            return None
        from ..services.transcribe import is_installed

        size = options.subtitles.model_size.value
        device = options.subtitles.device.value
        if not is_installed(size):
            raise PipelineError(
                ErrorCode.MODEL_MISSING,
                f"Whisper model '{size}' is not downloaded.",
                stage="transcribe",
                hint="Download it from the UI (POST /api/models/{size}/download).",
            )
        from ..workers.asr_pool import init_asr_worker

        self._asr_pool = ProcessPoolExecutor(
            max_workers=max(1, options.performance.asr_workers),
            mp_context=self._mp_ctx,
            initializer=init_asr_worker,
            initargs=(size, device),
        )
        return self._asr_pool

    # ------------------------------------------------------------- job control
    def submit(self, upload_id: str, options: JobOptions) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        self.db.create_job(
            {
                "id": job_id,
                "upload_id": upload_id,
                "status": JobStatus.QUEUED.value,
                "options": options.model_dump(mode="json"),
                "created_at": time.time(),
            }
        )
        self._queue.put_nowait(job_id)
        return self.db.get_job(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise PipelineError(ErrorCode.NOT_FOUND, "Job not found.", stage="job")
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        return self.db.list_jobs()

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        status = JobStatus(job["status"])
        if status in TERMINAL:
            return job
        if status == JobStatus.QUEUED:
            self.db.update_job(job_id, status=JobStatus.CANCELLED.value, message="Cancelled")
            await self._publish(job_id, force=True)
            return self.get(job_id)

        event = self._cancel_events.get(job_id)
        if event is not None:
            event.set()
        self._kill_children()
        self.db.update_job(job_id, message="Cancelling...")
        return self.get(job_id)

    def _kill_children(self) -> None:
        """Kill executor workers and any ffmpeg they spawned (psutil, whole tree)."""
        for pool in (self._pool, self._asr_pool):
            if pool is None:
                continue
            for process in list(getattr(pool, "_processes", {}).values()):
                pid = getattr(process, "pid", None)
                if pid is None:
                    continue
                try:
                    parent = psutil.Process(pid)
                except psutil.Error:
                    continue
                try:
                    children = parent.children(recursive=True)
                except psutil.Error:
                    children = []
                for target in [parent, *children]:
                    try:
                        target.terminate()
                    except psutil.Error:
                        pass
                _, alive = psutil.wait_procs([parent, *children], timeout=2)
                for target in alive:
                    try:
                        target.kill()
                    except psutil.Error:
                        pass

    async def resume(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if JobStatus(job["status"]) in (JobStatus.QUEUED, JobStatus.RUNNING):
            return job
        self._cleanup_outputs(job_id)
        self.db.update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            stage=None,
            overall_pct=0.0,
            message="Queued",
            error=None,
            started_at=None,
            finished_at=None,
        )
        self.db.set_chunks(job_id, [])
        self._queue.put_nowait(job_id)
        await self._publish(job_id, force=True)
        return self.get(job_id)

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if JobStatus(job["status"]) not in TERMINAL:
            raise PipelineError(
                ErrorCode.CONFLICT, "Cancel the job before deleting it.", stage="job"
            )
        self._cleanup_job_dirs(job_id)
        self.db.delete_job(job_id)
        self._subscribers.pop(job_id, None)

    # -------------------------------------------------------------- snapshots
    def snapshot(self, job_id: str) -> ProgressSnapshot:
        job = self.get(job_id)
        chunks = [ChunkProgress(**c) for c in self.db.get_chunks(job_id)]
        status = JobStatus(job["status"])
        running = [c for c in chunks if c.status == ChunkStatus.RUNNING]
        if running:
            stage_pct = sum(c.stage_pct for c in running) / len(running) * 100.0
        elif status == JobStatus.COMPLETED:
            stage_pct = 100.0
        else:
            stage_pct = 0.0
        return ProgressSnapshot(
            job_id=job_id,
            status=status,
            stage=job["stage"],
            overall_pct=round(job["overall_pct"], 2),
            stage_pct=round(stage_pct, 2),
            eta_sec=self._eta(job),
            message=job["message"],
            warnings=job["warnings"],
            chunks=chunks,
        )

    def _eta(self, job: dict[str, Any]) -> float | None:
        if JobStatus(job["status"]) != JobStatus.RUNNING or not job.get("started_at"):
            return None
        samples = self._eta_samples.get(job["id"])
        if samples and len(samples) >= 2:
            (t0, p0), (t1, p1) = samples[0], samples[-1]
            if p1 > p0 and t1 > t0:
                rate = (p1 - p0) / (t1 - t0)  # percent per second
                if rate > 0:
                    return round(max(0.0, (100.0 - p1) / rate), 1)
        pct = job["overall_pct"] / 100.0
        if pct <= 0.02:
            return None
        elapsed = time.time() - job["started_at"]
        return round(max(0.0, elapsed / pct - elapsed), 1)

    # ------------------------------------------------------------- SSE fan-out
    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(job_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(job_id, None)

    async def _publish(self, job_id: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish.get(job_id, 0.0) < 0.15:
            return
        self._last_publish[job_id] = now
        subs = self._subscribers.get(job_id)
        if not subs:
            return
        snap = self.snapshot(job_id)
        for q in list(subs):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass

    # ---------------------------------------------------------------- workers
    async def _worker_loop(self) -> None:
        while not self._shutdown:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                async with self._sem:
                    job = self.db.get_job(job_id)
                    if job is None or job["status"] != JobStatus.QUEUED.value:
                        continue
                    await self._run_job(job_id)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception(f"worker crashed on job {job_id}")
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------- job
    async def _run_job(self, job_id: str) -> None:
        job = self.get(job_id)
        options = JobOptions(**job["options"])
        upload_id = job["upload_id"]

        try:
            upload = get_upload(upload_id)
        except PipelineError as exc:
            self._fail(job_id, exc)
            return
        if upload["media"] is None or not upload["media"].has_video:
            self._fail(
                job_id,
                PipelineError(
                    ErrorCode.NO_VIDEO_STREAM, "The upload has no usable video.", stage="probe"
                ),
            )
            return
        info: MediaInfo = upload["media"]

        try:
            src = source_path(upload_id)
        except PipelineError as exc:
            self._fail(job_id, exc)
            return

        assert self._manager is not None
        cancel_event = self._manager.Event()
        progress_queue = self._manager.Queue()
        self._cancel_events[job_id] = cancel_event
        self._progress_queues[job_id] = progress_queue
        self._eta_samples[job_id] = deque(maxlen=12)

        workdir = ensure_dir(work_dir(job_id))
        outdir = ensure_dir(output_dir(job_id))
        log_path = workdir / "job.log"
        jlog = job_logger(log_path, "cinelume.job")

        self.db.update_job(
            job_id,
            status=JobStatus.RUNNING.value,
            stage="chunker",
            overall_pct=0.0,
            message="Planning chunks",
            started_at=time.time(),
            error=None,
        )
        await self._publish(job_id, force=True)

        drain: asyncio.Task | None = None
        try:
            fps = playback_fps(options, info)
            chunks = plan_chunks(src, options, info, workdir, fps)
            self.db.set_chunks(
                job_id,
                [
                    {
                        "index": c.index,
                        "status": ChunkStatus.PENDING.value,
                        "src_start": c.src_start,
                        "src_end": c.src_end,
                        "hard_boundary": c.hard_boundary,
                        "cut_duration": c.duration,
                    }
                    for c in chunks
                ],
            )
            tracker = ProgressTracker([{"index": c.index, "duration": c.duration} for c in chunks])
            jlog.info(f"plan: {len(chunks)} chunk(s) over {info.duration:.1f}s")

            self.db.update_job(job_id, stage="audio", message=f"{len(chunks)} chunk(s)")
            await self._publish(job_id, force=True)

            drain = asyncio.create_task(self._drain(job_id, progress_queue, tracker))
            options_json = json.dumps(options.model_dump(mode="json"))
            info_json = json.dumps(info.model_dump())

            audio_futures = self._submit_audio(
                chunks,
                src,
                workdir,
                options_json,
                info_json,
                cancel_event,
                progress_queue,
                log_path,
            )
            language = await self._lock_language(
                chunks, audio_futures, workdir, options, options_json,
                cancel_event, progress_queue, log_path, jlog,
            )

            chunk_outputs = await asyncio.gather(
                *(
                    self._pipeline(
                        chunk,
                        audio_futures[chunk.index],
                        language,
                        src,
                        workdir,
                        options,
                        options_json,
                        info_json,
                        cancel_event,
                        progress_queue,
                        log_path,
                        jlog,
                    )
                    for chunk in chunks
                )
            )

            await self._master_stage(
                job_id, chunks, chunk_outputs, src, workdir, outdir, options, fps,
                cancel_event, tracker, jlog,
            )

            self.db.update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                stage="done",
                overall_pct=100.0,
                message="Finished",
                finished_at=time.time(),
            )
            jlog.info("job completed")
        except PipelineError as exc:
            if exc.code == ErrorCode.CANCELLED:
                self._mark_cancelled(job_id, jlog)
            else:
                self._fail(job_id, exc, jlog)
        except asyncio.CancelledError:
            self._mark_cancelled(job_id, jlog)
            raise
        except Exception as exc:
            self._fail(
                job_id,
                PipelineError(ErrorCode.INTERNAL, f"Unexpected error: {exc}", stage="pipeline"),
                jlog,
            )
        finally:
            if drain is not None:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
            self._cancel_events.pop(job_id, None)
            self._progress_queues.pop(job_id, None)
            self._eta_samples.pop(job_id, None)
            await self._publish(job_id, force=True)

    # ------------------------------------------------------------------ stages
    def _submit_audio(
        self, chunks: list[ChunkSpec], src: Path, workdir: Path, options_json: str,
        info_json: str, cancel_event, progress_queue, log_path: Path,
    ) -> dict[int, Callable[[], Future]]:
        """Submitters, not futures: a retry must be able to submit a fresh one."""
        assert self._pool is not None

        def make(chunk: ChunkSpec) -> Callable[[], Future]:
            def submit() -> Future:
                assert self._pool is not None
                return self._pool.submit(
                    stage_audio_entry,
                    chunk.index,
                    chunk.src_start,
                    chunk.src_end,
                    chunk.hard_boundary,
                    str(src),
                    str(workdir),
                    options_json,
                    info_json,
                    cancel_event,
                    progress_queue,
                    str(log_path),
                )

            return submit

        return {chunk.index: make(chunk) for chunk in chunks}

    async def _lock_language(
        self, chunks: list[ChunkSpec], audio_submitters: dict[int, Callable[[], Future]],
        workdir: Path, options: JobOptions, options_json: str, cancel_event, progress_queue,
        log_path: Path, jlog: Any,
    ) -> str | None:
        """`auto` detects once on the first chunk, then the language is locked."""
        if not options.subtitles.enabled:
            return None
        if options.subtitles.language != "auto":
            return options.subtitles.language

        first = chunks[0]
        await self._retry(audio_submitters[first.index], cancel_event)
        audio_cut = chunk_dir(workdir, first.index) / "audio_cut.wav"
        if not audio_cut.exists():
            return None
        from ..workers.asr_pool import detect_language_chunk

        pool = self._ensure_asr_pool(options)
        if pool is None:
            return None
        language = await self._retry(
            lambda: pool.submit(detect_language_chunk, str(audio_cut)), cancel_event
        )
        jlog.info(f"language locked: {language}")
        return language

    async def _pipeline(
        self, chunk: ChunkSpec, audio_submit: Callable[[], Future], language: str | None,
        src: Path, workdir: Path, options: JobOptions, options_json: str, info_json: str,
        cancel_event, progress_queue, log_path: Path, jlog: Any,
    ) -> dict[str, Any]:
        """A -> B -> C for one chunk; runs concurrently with the other chunks."""
        await self._retry(audio_submit, cancel_event)
        directory = chunk_dir(workdir, chunk.index)
        segments = self._load_segments(directory)
        audio_end = sum(end - start for start, end in segments)
        audio_cut = directory / "audio_cut.wav"
        audio_dn = directory / "audio_dn.wav"

        if options.subtitles.enabled and audio_cut.exists():
            pool = self._ensure_asr_pool(options)
            if pool is not None:
                try:
                    await self._retry(
                        lambda: pool.submit(
                            stage_transcribe_entry,
                            chunk.index,
                            str(audio_cut),
                            language,
                            str(workdir),
                            options_json,
                            cancel_event,
                            progress_queue,
                            str(log_path),
                        ),
                        cancel_event,
                    )
                    self._build_subtitles(
                        chunk, workdir, options, directory, segments, audio_end, audio_cut
                    )
                except PipelineError as exc:
                    if exc.code == ErrorCode.CANCELLED:
                        raise
                    if not options.performance.degrade_on_asr_failure:
                        raise
                    jlog.warning(
                        f"chunk {chunk.index}: transcription failed ({exc.message}); no subtitles"
                    )

        assert self._pool is not None
        return await self._retry(
            lambda: self._pool.submit(
                stage_video_entry,
                chunk.index,
                chunk.src_start,
                chunk.src_end,
                str(src),
                str(audio_dn) if audio_dn.exists() else None,
                str(workdir),
                options_json,
                info_json,
                json.dumps(segments),
                cancel_event,
                progress_queue,
                str(log_path),
            ),
            cancel_event,
        )

    def _build_subtitles(
        self, chunk: ChunkSpec, workdir: Path, options: JobOptions, directory: Path,
        segments: list[list[float]], audio_end: float, audio_cut: Path,
    ) -> None:
        """Build the ASS in the main process from the words the ASR worker wrote."""
        from .stages import stage_subtitles

        words_file = directory / "words.json"
        if not words_file.exists():
            return
        raw = [Word(**entry) for entry in json.loads(words_file.read_text(encoding="utf-8"))]
        spec = ChunkSpec(index=chunk.index, src_start=chunk.src_start, src_end=chunk.src_end)
        stage_subtitles(
            workdir, spec, raw, options, MediaInfo(), audio_end, envelope_audio=audio_cut
        )

    def _load_segments(self, directory: Path) -> list[list[float]]:
        stage_file = directory / "audio_stage.json"
        if stage_file.exists():
            return json.loads(stage_file.read_text(encoding="utf-8"))["segments"]
        return []

    async def _master_stage(
        self, job_id: str, chunks: list[ChunkSpec], chunk_outputs: list[dict[str, Any]],
        src: Path, workdir: Path, outdir: Path, options: JobOptions, fps: int,
        cancel_event, tracker: ProgressTracker, jlog: Any,
    ) -> None:
        self.db.update_job(job_id, stage="merge", message="Merging chunks")
        await self._publish(job_id, force=True)

        loop = asyncio.get_running_loop()
        outputs = [Path(entry["output"]) for entry in chunk_outputs if entry.get("output")]
        merged = workdir / "merged.mkv"
        await loop.run_in_executor(
            None, lambda: merge_chunks(outputs, merged, workdir, cancel_event)
        )
        await loop.run_in_executor(
            None, lambda: verify_merge(merged, sum(c.duration for c in chunks), fps)
        )

        tracker.set_tail(0.5)
        self.db.update_job(job_id, overall_pct=tracker.overall_pct(), stage="master")
        await self._publish(job_id, force=True)

        await loop.run_in_executor(
            None,
            lambda: run_master(
                merged, options, workdir / "final.mp4", workdir=workdir, cancel_event=cancel_event
            ),
        )
        self._write_transcript(workdir)

        if options.cover.enabled:
            try:
                cover = await loop.run_in_executor(
                    None, lambda: render_cover(src, options, workdir / "cover.png")
                )
                jlog.info(f"cover: t={cover.time_sec:.2f}s")
            except PipelineError as exc:
                jlog.warning(f"cover failed: {exc.message}")
                self.db.append_event(
                    job_id, "warning", {"message": f"Cover generation failed: {exc.message}"}
                )

        moved = finalize_outputs(workdir, outdir)
        if not options.performance.keep_intermediates:
            cleanup_chunks(workdir)
        jlog.info(f"outputs: {[p.name for p in moved]}")

    def _write_transcript(self, workdir: Path) -> None:
        """Concatenate every chunk's words into one transcript."""
        parts: list[dict[str, Any]] = []
        for directory in sorted(Path(workdir).glob("chunk_*")):
            words_file = directory / "words.json"
            if not words_file.exists():
                continue
            try:
                words = json.loads(words_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            parts.append({"chunk": directory.name, "words": words})
        (Path(workdir) / "transcript.json").write_text(
            json.dumps({"chunks": parts}, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ------------------------------------------------------------------ retry
    async def _retry(self, submit: Callable[[], Future], cancel_event=None) -> Any:
        """Retry a stage up to 2 extra times; a worker crash rebuilds the pool."""
        last: Exception | None = None
        for attempt in range(_STAGE_ATTEMPTS):
            if cancel_event is not None and cancel_event.is_set():
                raise PipelineError(ErrorCode.CANCELLED, "Job cancelled.")
            try:
                return await asyncio.wrap_future(submit())
            except PipelineError as exc:
                if exc.code == ErrorCode.CANCELLED:
                    raise
                last = exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # BrokenProcessPool and friends
                last = exc
                self._recreate_pools()
            await asyncio.sleep(0.3 * (attempt + 1))
        if isinstance(last, PipelineError):
            raise last
        raise PipelineError(ErrorCode.FFMPEG_FAILED, str(last or "Stage failed."))

    # ------------------------------------------------------------------ drain
    async def _drain(self, job_id: str, progress_queue: Any, tracker: ProgressTracker) -> None:
        try:
            while True:
                updated = False
                while True:
                    try:
                        msg = progress_queue.get_nowait()
                    except queue_mod.Empty:
                        break
                    if msg is None:
                        break
                    updated = self._apply_progress(job_id, tracker, msg) or updated
                if updated:
                    self.db.update_job(job_id, overall_pct=tracker.overall_pct())
                    samples = self._eta_samples.get(job_id)
                    if samples is not None:
                        samples.append((time.monotonic(), tracker.overall_pct()))
                    await self._publish(job_id)
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return

    def _apply_progress(
        self, job_id: str, tracker: ProgressTracker, msg: dict[str, Any]
    ) -> bool:
        index = msg.get("index")
        if index is None:
            return False
        stage = msg.get("stage")
        status = msg.get("status")
        if status == "done":
            tracker.set_status(index, "done")
            self.db.update_chunk(
                job_id, index, status=ChunkStatus.DONE.value, stage_pct=1.0, stage=stage
            )
        elif status == "failed":
            tracker.set_status(index, "failed")
            self.db.update_chunk(job_id, index, status=ChunkStatus.FAILED.value)
        else:
            tracker.set_stage(index, stage, float(msg.get("stage_pct", 0.0)))
            self.db.update_chunk(
                job_id,
                index,
                status=ChunkStatus.RUNNING.value,
                stage=stage,
                stage_pct=float(msg.get("stage_pct", 0.0)),
            )
        self.db.update_job(job_id, stage=stage or "processing")
        return True

    # ---------------------------------------------------------------- helpers
    def _fail(self, job_id: str, exc: PipelineError, jlog: Any = None) -> None:
        self.db.update_job(
            job_id,
            status=JobStatus.FAILED.value,
            stage=exc.stage or "error",
            message=exc.message,
            error=exc.to_dict(),
            finished_at=time.time(),
        )
        if jlog is not None:
            jlog.error(f"job failed: {exc}", extra={"stage": exc.stage})

    def _mark_cancelled(self, job_id: str, jlog: Any = None) -> None:
        self._kill_children()
        self._cleanup_outputs(job_id)
        self.db.update_job(
            job_id,
            status=JobStatus.CANCELLED.value,
            stage="cancelled",
            message="Cancelled",
            finished_at=time.time(),
        )
        if jlog is not None:
            jlog.info("job cancelled")

    def _cleanup_outputs(self, job_id: str) -> None:
        outdir = output_dir(job_id)
        if outdir.exists():
            shutil.rmtree(outdir, ignore_errors=True)
        for path in Path(work_dir(job_id)).glob("*.partial"):
            path.unlink(missing_ok=True)

    def _cleanup_job_dirs(self, job_id: str) -> None:
        for path in (work_dir(job_id), output_dir(job_id)):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
