/** App-level types mirroring backend/app/models (server is the source of truth). */

export type DenoiseMethod = "afftdn" | "arnndn" | "noisereduce";
export type ModelSize = "tiny" | "base" | "small" | "medium" | "large-v3";
export type Device = "auto" | "cpu" | "cuda";
export type SubtitleMode = "karaoke_fill" | "word_highlight" | "static";
export type BackgroundMode = "none" | "box" | "outline";
export type Position = "top" | "center" | "bottom";
export type CoverTimeMode = "random" | "manual";
export type CoverAspect = "same_as_video" | "9:16" | "1:1" | "16:9" | "4:5";
export type CoverFormat = "png" | "jpg";
export type Resolution = "original" | "1080x1920" | "1920x1080" | "1080x1080" | "720x1280";
export type Fit = "crop" | "pad";
export type Preset = "ultrafast" | "superfast" | "veryfast" | "faster" | "fast" | "medium" | "slow";
export type Encoder = "auto" | "libx264" | "h264_nvenc" | "h264_qsv" | "h264_amf";
export type ChunkingMode = "auto" | "off" | "on";

export interface DenoiseOptions {
  enabled: boolean;
  method: DenoiseMethod;
  strength: number;
  highpass_hz: number;
}

export interface SilenceOptions {
  enabled: boolean;
  min_silence_sec: number;
  threshold_db: number;
  auto_threshold: boolean;
  keep_padding_sec: number;
  min_speech_sec: number;
  fade_enabled: boolean;
  fade_sec: number;
  fade_video: boolean;
  fade_audio: boolean;
  fade_color: string;
  fade_first_last: boolean;
}

export interface SubtitleOptions {
  enabled: boolean;
  model_size: ModelSize;
  language: string;
  device: Device;
  vocabulary_hint: string;
  mode: SubtitleMode;
  font_family: string;
  font_size_pct: number;
  bold: boolean;
  text_color: string;
  highlight_color: string;
  background_mode: BackgroundMode;
  background_color: string;
  background_opacity: number;
  outline_width_pct: number;
  shadow: boolean;
  position: Position;
  margin_v_pct: number;
  words_per_line: number;
  max_chars_per_line: number;
  max_lines: number;
  uppercase: boolean;
  pop_animation: boolean;
}

export interface MusicOptions {
  enabled: boolean;
  track_id: string | null;
  volume: number;
  loop: boolean;
  fade_in_sec: number;
  fade_out_sec: number;
  start_offset_sec: number;
  ducking_enabled: boolean;
  ducking_strength: number;
}

export interface CoverOptions {
  enabled: boolean;
  time_mode: CoverTimeMode;
  time_sec: number | null;
  seed: number | null;
  candidates: number;
  lines: string[];
  font_family: string;
  font_size_pct: number;
  text_color: string;
  stroke_color: string;
  stroke_width_pct: number;
  strip_enabled: boolean;
  strip_color: string;
  strip_opacity: number;
  position: Position;
  aspect: CoverAspect;
  format: CoverFormat;
}

export interface OutputOptions {
  resolution: Resolution;
  fit: Fit;
  fps: number | null;
  crf: number;
  preset: Preset;
  encoder: Encoder;
  audio_bitrate_kbps: number;
  loudnorm: boolean;
  target_lufs: number;
  audio_track_index: number;
}

export interface PerformanceOptions {
  chunking: ChunkingMode;
  chunk_threshold_sec: number;
  target_chunk_sec: number;
  boundary_window_sec: number;
  workers: number | null;
  asr_workers: number;
  max_segments_per_graph: number;
  degrade_on_asr_failure: boolean;
  keep_intermediates: boolean;
}

export interface JobOptions {
  denoise: DenoiseOptions;
  silence: SilenceOptions;
  subtitles: SubtitleOptions;
  music: MusicOptions;
  cover: CoverOptions;
  output: OutputOptions;
  performance: PerformanceOptions;
}

export interface AudioStreamInfo {
  index: number;
  codec: string;
  channels: number;
  sample_rate: number;
  start_time: number;
  language?: string | null;
}

export interface MediaInfo {
  duration: number;
  size_bytes: number;
  has_video: boolean;
  has_audio: boolean;
  width: number;
  height: number;
  display_width: number;
  display_height: number;
  rotation: number;
  fps: number;
  is_vfr: boolean;
  pix_fmt: string;
  bit_depth: number;
  is_hdr: boolean;
  audio_streams: AudioStreamInfo[];
  warnings: string[];
}

export interface UploadInfo {
  upload_id: string;
  filename: string;
  size_bytes: number;
  status: "initiated" | "complete" | "failed";
  received_parts: number[];
  media: MediaInfo | null;
  thumbnail_url: string | null;
  warnings: string[];
}

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface ChunkProgress {
  index: number;
  status: "pending" | "running" | "done" | "failed" | "skipped";
  stage: string | null;
  stage_pct: number;
  src_start: number;
  src_end: number;
  hard_boundary: boolean;
  cut_duration: number;
  attempts: number;
}

export interface JobRecord {
  id: string;
  upload_id: string;
  status: JobStatus;
  options: JobOptions;
  created_at: number;
  updated_at: number;
  stage: string | null;
  overall_pct: number;
  message: string | null;
  error: ApiErrorShape | null;
  warnings: string[];
  chunks: ChunkProgress[];
}

export interface ApiErrorShape {
  code: string;
  message: string;
  stage?: string | null;
}

export interface ProgressSnapshot {
  job_id: string;
  status: JobStatus;
  stage: string | null;
  overall_pct: number;
  stage_pct: number;
  eta_sec: number | null;
  message: string | null;
  warnings: string[];
  chunks: ChunkProgress[];
}

export interface SystemInfo {
  app: string;
  version: string;
  ffmpeg: {
    ffmpeg_version: string;
    filters: Record<string, boolean>;
    encoders: Record<string, boolean>;
    hardware_encoders: string[];
  };
  cpu: { count: number };
  ram: { total: number; available: number };
  cuda: boolean;
  workers: number;
  models: ModelInfo[];
  fonts: { family: string; file: string }[];
  limits: {
    max_upload_gb: number;
    max_duration_min: number;
    max_concurrent_jobs: number;
  };
}

export interface ModelInfo {
  size: ModelSize;
  installed: boolean;
  disk_bytes: number;
  approx_download_bytes: number;
  status: string;
  progress: number;
}

export interface MusicTrack {
  id: string;
  name: string;
  duration: number;
  integrated_lufs: number | null;
  size_bytes: number;
  normalized: boolean;
  file_url: string;
}

export interface CoverResult {
  time_sec: number;
  image_url: string;
  warnings: string[];
}
