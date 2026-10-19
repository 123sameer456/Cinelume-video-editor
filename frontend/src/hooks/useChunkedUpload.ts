/** Chunked uploader: 8 MB parts, 3 parallel, retry with backoff, resumable. */

import { api } from "../api/client";
import type { UploadInfo } from "../api/types";

const PARALLEL = 3;
const MAX_RETRIES = 4;

export interface UploadProgress {
  uploadedBytes: number;
  totalBytes: number;
  speedBytesPerSec: number;
  done: boolean;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Uploads `file` in parts; resumes using the parts the server already has. */
export async function uploadFileChunked(
  file: File,
  partSize: number,
  onProgress: (progress: UploadProgress) => void,
  shouldAbort?: () => boolean,
): Promise<UploadInfo> {
  const started = api.initUpload(file.name, file.size);
  const init = await started;
  const uploadId = init.upload_id;
  const totalParts = Math.max(1, Math.ceil(file.size / partSize));

  const uploaded = new Set<number>(init.received_parts);
  const startedAt = Date.now();
  let uploadedBytes = init.received_parts.length * partSize;
  let completed = uploaded.size;

  const report = () => {
    const elapsed = Math.max(0.25, (Date.now() - startedAt) / 1000);
    onProgress({
      uploadedBytes,
      totalBytes: file.size,
      speedBytesPerSec: uploadedBytes / elapsed,
      done: completed >= totalParts,
    });
  };

  const queue: number[] = [];
  for (let index = 0; index < totalParts; index += 1) {
    if (!uploaded.has(index)) {
      queue.push(index);
    }
  }

  async function worker() {
    while (queue.length > 0) {
      if (shouldAbort?.()) {
        throw new Error("aborted");
      }
      const index = queue.shift();
      if (index === undefined) {
        return;
      }
      const start = index * partSize;
      const end = Math.min(file.size, start + partSize);
      const body = await file.slice(start, end).arrayBuffer();

      let attempt = 0;
      for (;;) {
        try {
          await api.putPart(uploadId, index, body);
          uploaded.add(index);
          completed += 1;
          uploadedBytes += body.byteLength;
          report();
          break;
        } catch (error) {
          attempt += 1;
          if (attempt >= MAX_RETRIES) {
            throw error;
          }
          await sleep(400 * 2 ** attempt);
        }
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(PARALLEL, totalParts) }, () => worker()));
  report();

  const info = await api.completeUpload(uploadId, totalParts);
  onProgress({
    uploadedBytes: file.size,
    totalBytes: file.size,
    speedBytesPerSec: file.size / Math.max(0.25, (Date.now() - startedAt) / 1000),
    done: true,
  });
  return info;
}
