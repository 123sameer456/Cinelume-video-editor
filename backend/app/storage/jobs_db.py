"""Storage layer: SQLite job database."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    upload_id TEXT NOT NULL,
    status TEXT NOT NULL,
    options_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    stage TEXT,
    overall_pct REAL NOT NULL DEFAULT 0,
    message TEXT,
    error_json TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS chunks (
    job_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT,
    stage_pct REAL NOT NULL DEFAULT 0,
    src_start REAL NOT NULL DEFAULT 0,
    src_end REAL NOT NULL DEFAULT 0,
    hard_boundary INTEGER NOT NULL DEFAULT 0,
    cut_duration REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, idx),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
"""


class JobsDB:
    """Thread-safe SQLite wrapper (WAL). Only ever used from the main process."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ jobs
    def create_job(self, job: dict[str, Any]) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (id, upload_id, status, options_json, created_at,"
                " updated_at, stage, overall_pct, message, warnings_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job["id"],
                    job["upload_id"],
                    job["status"],
                    json.dumps(job["options"], ensure_ascii=False),
                    job.get("created_at", now),
                    now,
                    job.get("stage"),
                    job.get("overall_pct", 0.0),
                    job.get("message"),
                    json.dumps(job.get("warnings", []), ensure_ascii=False),
                ),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = []
        values: list[Any] = []
        for key, value in fields.items():
            if key in ("error", "warnings"):
                key = f"{key}_json"
                value = json.dumps(value, ensure_ascii=False)
            columns.append(f"{key} = ?")
            values.append(value)
        columns.append("updated_at = ?")
        values.append(time.time())
        values.append(job_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {', '.join(columns)} WHERE id = ?", values)

    def delete_job(self, job_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def mark_running_interrupted(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'interrupted', updated_at = ? WHERE status = 'running'",
                (time.time(),),
            )
            return cur.rowcount

    # ---------------------------------------------------------------- chunks
    def set_chunks(self, job_id: str, chunks: list[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE job_id = ?", (job_id,))
            self._conn.executemany(
                "INSERT INTO chunks (job_id, idx, status, stage, stage_pct, src_start,"
                " src_end, hard_boundary, cut_duration, attempts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        c["index"],
                        c.get("status", "pending"),
                        c.get("stage"),
                        c.get("stage_pct", 0.0),
                        c.get("src_start", 0.0),
                        c.get("src_end", 0.0),
                        1 if c.get("hard_boundary") else 0,
                        c.get("cut_duration", 0.0),
                        c.get("attempts", 0),
                    )
                    for c in chunks
                ],
            )

    def update_chunk(self, job_id: str, idx: int, **fields: Any) -> None:
        if not fields:
            return
        if "hard_boundary" in fields:
            fields["hard_boundary"] = 1 if fields["hard_boundary"] else 0
        columns = [f"{k} = ?" for k in fields]
        values = list(fields.values()) + [job_id, idx]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE chunks SET {', '.join(columns)} WHERE job_id = ? AND idx = ?", values
            )

    def get_chunks(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE job_id = ? ORDER BY idx", (job_id,)
            ).fetchall()
        return [
            {
                "index": r["idx"],
                "status": r["status"],
                "stage": r["stage"],
                "stage_pct": r["stage_pct"],
                "src_start": r["src_start"],
                "src_end": r["src_end"],
                "hard_boundary": bool(r["hard_boundary"]),
                "cut_duration": r["cut_duration"],
                "attempts": r["attempts"],
            }
            for r in rows
        ]

    # ---------------------------------------------------------------- events
    def append_event(self, job_id: str, type_: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (job_id, ts, type, payload_json) VALUES (?, ?, ?, ?)",
                (job_id, time.time(), type_, json.dumps(payload or {}, ensure_ascii=False)),
            )

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "upload_id": row["upload_id"],
            "status": row["status"],
            "options": json.loads(row["options_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "stage": row["stage"],
            "overall_pct": row["overall_pct"],
            "message": row["message"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "warnings": json.loads(row["warnings_json"] or "[]"),
        }
