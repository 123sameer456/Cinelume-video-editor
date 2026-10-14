import { useState } from "react";

import { api } from "../api/client";
import { friendlyMessage } from "../api/errors";
import { useJobEvents } from "../hooks/useJobEvents";
import { Banner, Button, formatEta } from "../components/ui";
import type { ChunkProgress } from "../api/types";

const STAGE_LABEL: Record<string, string> = {
  audio: "Audio cleanup",
  transcribe: "Transcribing",
  video: "Rendering",
  merge: "Merging",
  master: "Mastering",
  chunker: "Planning",
  done: "Done",
  cancelled: "Cancelled",
};

export function ProgressScreen({ jobId, onDone }: { jobId: string; onDone: () => void }) {
  const snapshot = useJobEvents(jobId);
  const [error, setError] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  const cancel = async () => {
    try {
      await api.cancelJob(jobId);
    } catch (err) {
      setError(friendlyMessage(err as never));
    }
  };

  if (!snapshot) {
    return <p className="muted">Connecting…</p>;
  }

  const terminal = snapshot.status === "completed";
  if (terminal || snapshot.status === "failed" || snapshot.status === "cancelled") {
    if (terminal) {
      // Let the parent switch to the result screen once we know it finished.
      setTimeout(onDone, 400);
    }
  }

  return (
    <div className="stack">
      {error ? <Banner tone="error">{error}</Banner> : null}

      <div className="card">
        <div className="progress-row">
          <div className="bar">
            <div className="bar-fill" style={{ width: `${snapshot.overall_pct}%` }} />
          </div>
          <span>{snapshot.overall_pct.toFixed(0)}%</span>
        </div>
        <div className="row spaced">
          <b>{STAGE_LABEL[snapshot.stage ?? ""] ?? snapshot.stage ?? "Working"}</b>
          <span className="muted">
            {snapshot.message} {formatEta(snapshot.eta_sec)}
          </span>
        </div>
        {snapshot.warnings.length > 0 ? (
          <ul className="warnings">
            {snapshot.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
        <div className="chunk-grid">
          {snapshot.chunks.map((chunk) => (
            <ChunkCell key={chunk.index} chunk={chunk} />
          ))}
        </div>
        <div className="row">
          <Button kind="danger" confirm="Cancel this job?" onClick={() => void cancel()}>
            Cancel
          </Button>
          <Button onClick={() => setShowLog((current) => !current)}>
            {showLog ? "Hide log" : "Show log"}
          </Button>
        </div>
        {showLog ? (
          <pre className="log">
            {`log will stream here\nstage: ${snapshot.stage}\n${snapshot.message ?? ""}`}
          </pre>
        ) : null}
      </div>
    </div>
  );
}

function ChunkCell({ chunk }: { chunk: ChunkProgress }) {
  const state =
    chunk.status === "done"
      ? "done"
      : chunk.status === "failed"
        ? "failed"
        : chunk.status === "skipped"
          ? "skipped"
          : chunk.status === "running"
            ? "running"
            : "queued";
  return (
    <div className={`chunk chunk-${state}`} title={`chunk ${chunk.index}: ${chunk.status}`}>
      <span>#{chunk.index}</span>
      <small>{state}</small>
      {chunk.status === "running" ? (
        <div className="mini-bar">
          <div className="mini-fill" style={{ width: `${chunk.stage_pct * 100}%` }} />
        </div>
      ) : null}
    </div>
  );
}
