import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { Banner, Button, formatDuration } from "../components/ui";
import type { JobRecord } from "../api/types";

export function HistoryScreen({
  onOpen,
  onResume,
}: {
  onOpen: (job: JobRecord) => void;
  onResume: (job: JobRecord) => void;
}) {
  const client = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.listJobs() });

  const remove = useMutation({
    mutationFn: (jobId: string) => api.deleteJob(jobId),
    onSuccess: () => client.invalidateQueries({ queryKey: ["jobs"] }),
  });

  if (jobs.isLoading) {
    return <p className="muted">Loading…</p>;
  }

  const list = jobs.data ?? [];
  if (list.length === 0) {
    return <p className="muted">No jobs yet.</p>;
  }

  return (
    <div className="stack">
      {jobs.isError ? <Banner tone="error">Could not load the job list.</Banner> : null}
      {list.map((job) => (
        <div className="card row spaced" key={job.id}>
          <div>
            <b className={`status status-${job.status}`}>{job.status}</b>{" "}
            <span className="muted">{new Date(job.created_at * 1000).toLocaleString()}</span>
            <div className="muted">
              {job.overall_pct.toFixed(0)}% · {job.stage ?? "–"}
              {job.message ? ` · ${job.message}` : ""}
            </div>
          </div>
          <div className="row">
            {job.status === "interrupted" || job.status === "cancelled" || job.status === "failed" ? (
              <Button onClick={() => onResume(job)}>Resume</Button>
            ) : null}
            {job.status === "completed" ? (
              <Button kind="primary" onClick={() => onOpen(job)}>
                Open
              </Button>
            ) : null}
            <Button
              kind="danger"
              confirm="Delete this job and its files?"
              onClick={() => remove.mutate(job.id)}
            >
              Delete
            </Button>
          </div>
        </div>
      ))}
      <p className="muted">
        {list.length} job{list.length === 1 ? "" : "s"} · total{" "}
        {formatDuration(list.reduce((sum, job) => sum + (job.chunks[0]?.src_end ?? 0), 0))} of source
      </p>
    </div>
  );
}
