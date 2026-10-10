"""Job orchestrator: queue, worker processes, progress, SSE fan-out, cancel/resume.

Phase 1 runs the dummy pipeline. The shape here (per-chunk state machine, two-stage
progress, cancel event shared with workers, duration-weighted progress) is what the
real stages plug into in phases 2-5.
"""

from __future__ import annotations

import asyncio
import functools
import json
import multiprocessing
import queue as queue_mod
import shutil
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import ErrorCode, PipelineError
from ..core.logging import get_logger, job_logger
from ..core.paths import ensure_dir, output_dir, work_dir
from ..models.job import ChunkProgress, ChunkStatus, JobStatus, ProgressSnapshot
from ..models.options import JobOptions
from ..services.ffmpeg import run_ffmpeg
from ..services.uploads import generate_thumbnail, get_upload, source_path
from ..storage.jobs_db import JobsDB
from ..workers.media_pool import compute_worker_count, create_media_pool
from .dummy import plan_dummy_chunks
from .progress import ProgressTracker

log = get_logger("cinelume.jobs")

TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


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
        self._futures: dict[str, list[Future]] = {}
        self._last_publish: dict[str, float] = {}
        self._manager: Any = None
        self._pool: ProcessPoolExecutor | None = None
        self._mp_ctx = multiprocessing.get_context("spawn")
        self._shutdown = False
        self.workers = compute_worker_count()

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._shutdown = False
        self._manager = self._mp_ctx.Manager()
        self._pool = create_media_pool(self.workers)
        log.info("job manager started", extra={"stage": f"workers={self.workers}"})

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
        for _job_id, event in list(self._cancel_events.items()):
            try:
                event.set()
            except Exception:
                pass
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if self._manager is not None:
            self._manager.shutdown()
            self._manager = None

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
            # Workers poll this event (chunk sleeps and run_ffmpeg), so cancel is fast
            # without cancelling the asyncio task and losing error bookkeeping.
            event.set()
        self.db.update_job(job_id, message="Cancelling...")
        return self.get(job_id)

    async def resume(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if JobStatus(job["status"]) in (JobStatus.QUEUED, JobStatus.RUNNING):
            return job
        self._cleanup_job_dirs(job_id)
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
                ErrorCode.CONFLICT,
                "Cancel the job before deleting it.",
                stage="job",
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
            except Exception:  # pragma: no cover - defensive
                log.exception(f"worker crashed on job {job_id}")
            finally:
                self._queue.task_done()

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

        try:
            src = source_path(upload_id)
        except PipelineError as exc:
            self._fail(job_id, exc)
            return

        duration = upload["media"].duration
        assert self._manager is not None
        cancel_event = self._manager.Event()
        progress_queue = self._manager.Queue()
        self._cancel_events[job_id] = cancel_event
        self._progress_queues[job_id] = progress_queue

        workdir = ensure_dir(work_dir(job_id))
        outdir = ensure_dir(output_dir(job_id))
        log_path = workdir / "job.log"
        jlog = job_logger(log_path, "cinelume.job")

        plan = plan_dummy_chunks(duration, options)
        self.db.set_chunks(
            job_id,
            [
                {
                    "index": c["index"],
                    "status": ChunkStatus.PENDING.value,
                    "src_start": c["src_start"],
                    "src_end": c["src_end"],
                    "hard_boundary": c["hard_boundary"],
                    "cut_duration": c["src_end"] - c["src_start"],
                }
                for c in plan
            ],
        )
        tracker = ProgressTracker(
            [
                {"index": c["index"], "duration": c["src_end"] - c["src_start"]}
                for c in plan
            ]
        )

        self.db.update_job(
            job_id,
            status=JobStatus.RUNNING.value,
            stage="starting",
            overall_pct=0.0,
            message=f"Processing {len(plan)} chunk(s)",
            started_at=time.time(),
            error=None,
        )
        jlog.info(f"job started: {len(plan)} chunk(s), duration={duration:.1f}s")
        await self._publish(job_id, force=True)

        drain = asyncio.create_task(self._drain(job_id, progress_queue, tracker))
        futures: list[Future] = []
        try:
            assert self._pool is not None
            for chunk in plan:
                future = self._pool.submit(
                    _dummy_chunk_entry,
                    job_id,
                    chunk["index"],
                    chunk["src_start"],
                    chunk["src_end"],
                    str(src),
                    str(workdir),
                    cancel_event,
                    progress_queue,
                    str(log_path),
                )
                futures.append(future)
            self._futures[job_id] = futures

            for future in futures:
                await asyncio.wrap_future(future)

            await self._run_master(job_id, src, outdir, duration, cancel_event, log_path, tracker)

            self.db.update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                stage="done",
                overall_pct=100.0,
                message="Dummy pipeline finished",
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
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(
                job_id,
                PipelineError(ErrorCode.INTERNAL, f"Unexpected error: {exc}", stage="pipeline"),
                jlog,
            )
        finally:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
            self._cancel_events.pop(job_id, None)
            self._progress_queues.pop(job_id, None)
            self._futures.pop(job_id, None)
            await self._publish(job_id, force=True)

    async def _drain(
        self, job_id: str, progress_queue: Any, tracker: ProgressTracker
    ) -> None:
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
                    updated = self._apply_progress(job_id, tracker, msg)
                if updated:
                    self.db.update_job(job_id, overall_pct=tracker.overall_pct())
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

    async def _run_master(
        self,
        job_id: str,
        src: Path,
        outdir: Path,
        duration: float,
        cancel_event: Any,
        log_path: Path,
        tracker: ProgressTracker,
    ) -> None:
        self.db.update_job(job_id, stage="master")
        for i in range(4):
            if cancel_event.is_set():
                raise PipelineError(ErrorCode.CANCELLED, "Job cancelled.", stage="master")
            await asyncio.sleep(0.1)
            tracker.set_tail((i + 1) / 4.0)
            self.db.update_job(job_id, overall_pct=tracker.overall_pct())
            await self._publish(job_id)

        loop = asyncio.get_running_loop()
        clip = min(3.0, duration) if duration > 0 else 1.0
        output = outdir / "output.mp4"
        args = [
            "-i",
            str(src),
            "-t",
            f"{clip:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ]
        await loop.run_in_executor(
            None,
            functools.partial(
                run_ffmpeg,
                args,
                cwd=outdir,
                timeout=max(120.0, 6 * clip),
                cancel_event=cancel_event,
                log_path=log_path,
            ),
        )
        await loop.run_in_executor(
            None,
            functools.partial(
                generate_thumbnail, src, outdir / "thumb.jpg", duration
            ),
        )
        # re-expose the thumbnail as the phase-1 "cover" so file serving is exercised
        thumb = outdir / "thumb.jpg"
        if thumb.exists():
            shutil.copyfile(thumb, outdir / "cover.png")
        (outdir / "transcript.json").write_text(
            json.dumps(
                {"job_id": job_id, "duration": duration, "chunks": len(tracker.entries)},
                indent=2,
            ),
            encoding="utf-8",
        )

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
        self._cleanup_partials(job_id)
        self.db.update_job(
            job_id,
            status=JobStatus.CANCELLED.value,
            stage="cancelled",
            message="Cancelled",
            finished_at=time.time(),
        )
        if jlog is not None:
            jlog.info("job cancelled")

    def _cleanup_partials(self, job_id: str) -> None:
        outdir = output_dir(job_id)
        if outdir.exists():
            shutil.rmtree(outdir, ignore_errors=True)

    def _cleanup_job_dirs(self, job_id: str) -> None:
        for path in (work_dir(job_id), output_dir(job_id)):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)


def _dummy_chunk_entry(*args: Any) -> int:
    """Spawn entry point: import inside so the child always resolves app.*."""
    from .dummy import run_dummy_chunk

    return run_dummy_chunk(*args)
