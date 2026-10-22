/**
 * Friendly messages for the backend's structured errors (phase 6, error mapping).
 * The backend always sends {code, message, stage, chunk?, hint}; a 422 additionally
 * carries `errors` — the pydantic field list, which we turn into readable text.
 */

export interface FieldError {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
}

export interface ApiError {
  code: string;
  message: string;
  stage?: string | null;
  chunk?: number;
  hint?: string | null;
  errors?: FieldError[];
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

/** Human-readable list of the fields the server rejected, e.g. "options.music: …". */
export function fieldMessages(error: ApiError): string[] {
  return (error.errors ?? []).map((field) => {
    const path = (field.loc ?? [])
      .filter((part) => part !== "body" && typeof part === "string")
      .join(".");
    return `${path}: ${field.msg ?? "invalid value"}`;
  });
}

export async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiError> & {
      errors?: FieldError[];
      detail?: unknown;
    };
    // A plain FastAPI/Starlette 422 (outside our handler) puts the list in `detail`.
    const detail = Array.isArray(body.detail)
      ? (body.detail as FieldError[])
      : body.errors;
    return {
      code: body.code ?? "BAD_REQUEST",
      message: body.message ?? response.statusText,
      stage: body.stage ?? null,
      hint: body.hint ?? null,
      errors: detail ?? undefined,
    };
  } catch {
    return { code: "INTERNAL", message: response.statusText };
  }
}
