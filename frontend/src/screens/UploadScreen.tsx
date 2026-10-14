import { useCallback, useRef, useState } from "react";

import { api } from "../api/client";
import { friendlyMessage, type ApiError } from "../api/errors";
import { uploadFileChunked, type UploadProgress } from "../hooks/useChunkedUpload";
import { Banner, Button, formatBytes } from "../components/ui";
import type { UploadInfo } from "../api/types";

const PART_SIZE = 8 * 1024 * 1024;

export function UploadScreen({
  onUploaded,
}: {
  onUploaded: (upload: UploadInfo) => void;
}) {
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [upload, setUpload] = useState<UploadInfo | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    async (file: File) => {
      setError(null);
      setUpload(null);
      setProgress({ uploadedBytes: 0, totalBytes: file.size, speedBytesPerSec: 0, done: false });
      try {
        const info = await uploadFileChunked(file, PART_SIZE, setProgress);
        setUpload(info);
        onUploaded(info);
      } catch (err) {
        setProgress(null);
        setError(err as ApiError);
      }
    },
    [onUploaded],
  );

  return (
    <div className="stack">
      {error ? <Banner tone="error">{friendlyMessage(error)} {error.hint}</Banner> : null}

      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files[0];
          if (file) void handleFile(file);
        }}
      >
        <p>
          <b>Drop a video here</b> or
        </p>
        <Button kind="primary" onClick={() => inputRef.current?.click()}>
          Choose a file
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept="video/*,.mp4,.mov,.mkv,.webm"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
      </div>

      {progress ? (
        <div className="card">
          <div className="progress-row">
            <div className="bar">
              <div
                className="bar-fill"
                style={{ width: `${Math.min(100, (progress.uploadedBytes / progress.totalBytes) * 100)}%` }}
              />
            </div>
            <span>
              {formatBytes(progress.uploadedBytes)} / {formatBytes(progress.totalBytes)}
              {progress.speedBytesPerSec > 0
                ? ` · ${formatBytes(progress.speedBytesPerSec)}/s`
                : ""}
            </span>
          </div>
        </div>
      ) : null}

      {upload ? (
        <div className="card file-card">
          <div>
            <b>{upload.filename}</b>
            <div className="muted">
              {upload.media
                ? `${upload.media.display_width}×${upload.media.display_height} · ${upload.media.duration.toFixed(1)}s · ${upload.media.fps.toFixed(2)} fps · ${upload.media.has_audio ? "audio" : "no audio"}`
                : "uploaded"}
            </div>
          </div>
          {upload.thumbnail_url ? (
            <img className="thumb" src={upload.thumbnail_url} alt="thumbnail" />
          ) : null}
          {upload.warnings.length > 0 ? (
            <ul className="warnings">
              {upload.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
          <Button
            kind="primary"
            onClick={() => {
              void fetch("/api/uploads");
              onUploaded(upload);
            }}
          >
            Configure this video →
          </Button>
        </div>
      ) : null}
    </div>
  );
}
