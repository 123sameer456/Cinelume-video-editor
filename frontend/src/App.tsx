import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "./api/client";
import type { ApiError } from "./api/errors";
import type { JobRecord, UploadInfo } from "./api/types";
import { useJobOptions } from "./hooks/useJobOptions";
import { ConfigureScreen } from "./screens/ConfigureScreen";
import { HistoryScreen } from "./screens/HistoryScreen";
import { ProgressScreen } from "./screens/ProgressScreen";
import { ShortsScreen } from "./screens/ShortsScreen";
import { ResultScreen } from "./screens/ResultScreen";
import { SystemScreen } from "./screens/SystemScreen";
import { UploadScreen } from "./screens/UploadScreen";

const client = new QueryClient();

type View = "upload" | "configure" | "progress" | "result" | "shorts" | "history" | "system";

function Shell() {
  const [view, setView] = useState<View>("upload");
  const [upload, setUpload] = useState<UploadInfo | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<ApiError | null>(null);
  const { options, update, resetSection, isLoading } = useJobOptions();

  const start = async () => {
    if (!upload || !options) return;
    setStarting(true);
    setStartError(null);
    try {
      const job = await api.createJob(upload.upload_id, options);
      setJobId(job.id);
      setView("progress");
    } catch (error) {
      setStartError(error as ApiError);
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="app">
      <nav className="nav">
        <b className="brand">Cinelume</b>
        {(
          [
            ["upload", "Upload"],
            ["configure", "Configure"],
            ["progress", "Progress"],
            ["result", "Result"],
            ["shorts", "Shorts"],
            ["history", "History"],
            ["system", "System"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            className={`nav-link ${view === key ? "active" : ""}`}
            onClick={() => setView(key)}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="main">
        {view === "upload" ? (
          <UploadScreen
            onUploaded={(info) => {
              setUpload(info);
              setView("configure");
            }}
          />
        ) : null}

        {view === "configure" ? (
          upload ? (
            <ConfigureScreen
              uploadId={upload.upload_id}
              options={options}
              update={update}
              resetSection={resetSection}
              onStart={() => void start()}
              busy={starting || isLoading}
              startError={startError}
            />
          ) : (
            <p className="muted">Upload a video first.</p>
          )
        ) : null}

        {view === "progress" && jobId ? (
          <ProgressScreen jobId={jobId} onDone={() => setView("result")} />
        ) : null}

        {view === "result" && jobId ? (
          <ResultScreen jobId={jobId} onMakeShorts={() => setView("shorts")} />
        ) : null}

        {view === "shorts" ? (
          jobId ? (
            <ShortsScreen jobId={jobId} />
          ) : (
            <p className="muted">Run a job first, then mark shorts here.</p>
          )
        ) : null}

        {view === "history" ? (
          <HistoryScreen
            onOpen={(job: JobRecord) => {
              setJobId(job.id);
              setView("result");
            }}
            onResume={(job: JobRecord) => {
              void api.resumeJob(job.id);
              setJobId(job.id);
              setView("progress");
            }}
          />
        ) : null}

        {view === "system" ? <SystemScreen /> : null}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <Shell />
    </QueryClientProvider>
  );
}
