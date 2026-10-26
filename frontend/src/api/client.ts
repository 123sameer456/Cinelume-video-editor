/** Typed fetch client for the Cinelume API. */

import { toApiError, type ApiError } from "./errors";
import type {
  ClipExport,
  ClipSpec,
  CoverOptions,
  CoverResult,
  JobOptions,
  JobRecord,
  ModelSize,
  MusicTrack,
  SystemInfo,
  UploadInfo,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    if (response.status === 204) {
      return undefined as T;
    }
    const error: ApiError = await toApiError(response);
    throw error;
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  system: () => request<SystemInfo>("/system"),

  defaults: () => request<JobOptions>("/options/defaults"),

  fonts: () => request<{ fonts: { family: string; file: string }[] }>("/fonts"),

  // uploads
  initUpload: (filename: string, totalSize: number) =>
    request<UploadInitResponse>("/uploads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, total_size: totalSize }),
    }),

  putPart: (uploadId: string, part: number, body: ArrayBuffer) =>
    request<null>(`/uploads/${uploadId}/parts/${part}`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body,
    }),

  completeUpload: (uploadId: string, totalParts: number) =>
    request<UploadInfo>(`/uploads/${uploadId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total_parts: totalParts }),
    }),

  getUpload: (uploadId: string) => request<UploadInfo>(`/uploads/${uploadId}`),

  deleteUpload: (uploadId: string) =>
    request<null>(`/uploads/${uploadId}`, { method: "DELETE" }),

  // jobs
  createJob: (uploadId: string, options: JobOptions) =>
    request<JobRecord>("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload_id: uploadId, options }),
    }),

  listJobs: () => request<JobRecord[]>("/jobs"),

  getJob: (jobId: string) => request<JobRecord>(`/jobs/${jobId}`),

  cancelJob: (jobId: string) =>
    request<JobRecord>(`/jobs/${jobId}/cancel`, { method: "POST" }),

  resumeJob: (jobId: string) =>
    request<JobRecord>(`/jobs/${jobId}/resume`, { method: "POST" }),

  deleteJob: (jobId: string) => request<null>(`/jobs/${jobId}`, { method: "DELETE" }),

  fileUrl: (jobId: string, kind: "video" | "cover" | "transcript" | "log") =>
    `${BASE}/jobs/${jobId}/files/${kind}`,

  thumbnailUrl: (uploadId: string) => `${BASE}/uploads/${uploadId}/thumbnail`,

  // models
  listModels: () => request<{ models: SystemInfo["models"] }>("/models"),

  downloadModel: (size: ModelSize) =>
    request<{ size: string; status: string }>(`/models/${size}/download`, { method: "POST" }),

  cancelModelDownload: (size: ModelSize) =>
    request<{ size: string; status: string }>(`/models/${size}/cancel`, { method: "POST" }),

  // music
  listTracks: () => request<{ tracks: MusicTrack[] }>("/music"),

  deleteTrack: (trackId: string) => request<null>(`/music/${trackId}`, { method: "DELETE" }),

  trackUrl: (trackId: string) => `${BASE}/music/${trackId}/file`,

  // cover
  renderCover: (uploadId: string, options: CoverOptions) =>
    request<CoverResult>("/cover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload_id: uploadId, options }),
    }),

  // shorts/clips
  exportClips: (jobId: string, clips: ClipSpec[]) =>
    request<{ clips: ClipExport[] }>(`/jobs/${jobId}/clips`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clips: clips.map(({ name, start, end }) => ({ name, start, end })) }),
    }),

  clipUrl: (jobId: string, index: number) => `${BASE}/jobs/${jobId}/files/clip/${index}`,
};

export interface UploadInitResponse {
  upload_id: string;
  part_size: number;
  received_parts: number[];
}

export async function uploadMusicTrack(file: File): Promise<MusicTrack> {
  const response = await fetch(`${BASE}/music`, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "x-filename": file.name,
    },
    body: file,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as MusicTrack;
}
