/** SSE hook with auto-reconnect and an initial snapshot so the UI recovers cleanly. */

import { useEffect, useRef, useState } from "react";

import type { ProgressSnapshot } from "../api/types";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

/** Subscribes to a job's event stream; refreshes the snapshot first on reconnect. */
export function useJobEvents(jobId: string | null): ProgressSnapshot | null {
  const [snapshot, setSnapshot] = useState<ProgressSnapshot | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    if (!jobId) {
      setSnapshot(null);
      return;
    }
    let disposed = false;
    let source: EventSource | null = null;
    const myGeneration = generation.current + 1;
    generation.current = myGeneration;

    const connect = () => {
      if (disposed || generation.current !== myGeneration) {
        return;
      }
      source = new EventSource(`/api/jobs/${jobId}/events`);
      source.onmessage = (event) => {
        try {
          const snapshot = JSON.parse(event.data) as ProgressSnapshot;
          setSnapshot(snapshot);
          if (TERMINAL.has(snapshot.status)) {
            source?.close();
          }
        } catch {
          // ignore malformed frames
        }
      };
      source.onerror = () => {
        source?.close();
        if (!disposed && generation.current === myGeneration) {
          // EventSource reconnects by itself and we re-send a snapshot on connect.
          window.setTimeout(connect, 1000);
        }
      };
    };

    // Fetch a snapshot immediately so a reconnect never shows a stuck 99%.
    fetch(`/api/jobs/${jobId}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((job) => {
        if (job && !disposed) {
          setSnapshot({
            job_id: job.id,
            status: job.status,
            stage: job.stage,
            overall_pct: job.overall_pct,
            stage_pct: 0,
            eta_sec: null,
            message: job.message,
            warnings: job.warnings,
            chunks: job.chunks,
          });
          if (!TERMINAL.has(job.status)) {
            connect();
          }
        }
      })
      .catch(() => connect());

    return () => {
      disposed = true;
      source?.close();
    };
  }, [jobId]);

  return snapshot;
}
