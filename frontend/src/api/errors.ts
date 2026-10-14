/**
 * Friendly messages for the backend's structured errors (phase 6, error mapping).
 * The backend always sends {code, message, stage, chunk?, hint}.
 */

export interface ApiError {
  code: string;
  message: string;
  stage?: string | null;
  chunk?: number;
  hint?: string | null;
}

const FRIENDLY: Record<string, string> = {
  FFMPEG_MISSING: "FFmpeg is not installed. Run scripts/setup.ps1 or install Gyan.FFmpeg.",
  UNSUPPORTED_MEDIA: "That file is not a video we can read.",
  NO_VIDEO_STREAM: "No usable video stream found in that file.",
  NO_AUDIO_STREAM: "This file has no audio track.",
  DISK_SPACE: "Not enough free disk space.",
  LIMIT_EXCEEDED: "The file exceeds the server limits.",
  FFMPEG_FAILED: "FFmpeg failed while processing.",
  TIMEOUT: "The operation took too long and was stopped.",
  CANCELLED: "Cancelled.",
  MODEL_MISSING: "The Whisper model is not downloaded yet.",
  ASR_FAILED: "Transcription failed.",
  NO_SPEECH: "No speech was found above the threshold.",
  NO_SPEECH_DETECTED: "No speech was detected in this chunk.",
  NOT_FOUND: "Not found.",
  BAD_REQUEST: "The request was not valid.",
  CONFLICT: "That is not allowed right now.",
  INTERNAL: "Something went wrong on the server.",
};

export function friendlyMessage(error: ApiError): string {
  return FRIENDLY[error.code] ?? error.message ?? "Something went wrong.";
}

export async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiError>;
    return {
      code: body.code ?? "INTERNAL",
      message: body.message ?? response.statusText,
      stage: body.stage ?? null,
      hint: body.hint ?? null,
    };
  } catch {
    return { code: "INTERNAL", message: response.statusText };
  }
}
