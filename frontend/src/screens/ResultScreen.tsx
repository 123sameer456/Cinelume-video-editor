import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Banner, Button } from "../components/ui";

export function ResultScreen({ jobId }: { jobId: string }) {
  const job = useQuery({ queryKey: ["job", jobId], queryFn: () => api.getJob(jobId) });

  if (job.isLoading) {
    return <p className="muted">Loading…</p>;
  }
  if (!job.data) {
    return <Banner tone="error">Job not found.</Banner>;
  }
  const record = job.data;

  return (
    <div className="stack">
      {record.status === "failed" ? (
        <Banner tone="error">
          {record.error?.message ?? "The job failed."}
        </Banner>
      ) : null}
      {record.status === "cancelled" ? <Banner tone="warn">This job was cancelled.</Banner> : null}

      <div className="card">
        <h2>Your video</h2>
        <video controls src={api.fileUrl(jobId, "video")} className="player" />
        <div className="row">
          <a className="btn btn-primary" href={api.fileUrl(jobId, "video")} download>
            Download video
          </a>
          <a className="btn" href={api.fileUrl(jobId, "transcript")} download>
            Transcript (JSON)
          </a>
          <a className="btn" href={api.fileUrl(jobId, "log")} download>
            Log
          </a>
        </div>
      </div>

      {record.options.cover.enabled ? (
        <div className="card">
          <h2>Cover</h2>
          <img className="cover-preview" src={api.fileUrl(jobId, "cover")} alt="cover" />
          <div className="row">
            <a className="btn" href={api.fileUrl(jobId, "cover")} download>
              Download cover
            </a>
          </div>
        </div>
      ) : null}

      {record.warnings.length > 0 ? (
        <Banner tone="warn">
          <ul className="warnings">
            {record.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Banner>
      ) : null}

      <div className="row">
        <Button onClick={() => job.refetch()}>Refresh</Button>
      </div>
    </div>
  );
}
