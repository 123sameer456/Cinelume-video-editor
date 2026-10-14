import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { Banner, Button, formatBytes, Section } from "../components/ui";
import type { ModelSize } from "../api/types";

export function SystemScreen() {
  const client = useQueryClient();
  const system = useQuery({ queryKey: ["system"], queryFn: () => api.system() });

  const download = useMutation({
    mutationFn: (size: ModelSize) => api.downloadModel(size),
    onSuccess: () => client.invalidateQueries({ queryKey: ["system"] }),
  });

  if (system.isLoading) {
    return <p className="muted">Loading…</p>;
  }
  if (!system.data) {
    return <Banner tone="error">Could not reach the server.</Banner>;
  }
  const info = system.data;
  const missingFilters = Object.entries(info.ffmpeg.filters).filter(([, ok]) => !ok);

  return (
    <div className="stack">
      {info.ffmpeg.ffmpeg_version ? null : (
        <Banner tone="error">FFmpeg is missing. Install it and restart the server.</Banner>
      )}
      {missingFilters.length > 0 ? (
        <Banner tone="warn">Missing FFmpeg filters: {missingFilters.join(", ")}</Banner>
      ) : null}

      <Section title="System">
        <dl className="dl">
          <dt>App</dt>
          <dd>
            {info.app} {info.version}
          </dd>
          <dt>FFmpeg</dt>
          <dd>{info.ffmpeg.ffmpeg_version}</dd>
          <dt>Hardware encoders</dt>
          <dd>
            {info.ffmpeg.hardware_encoders.length > 0
              ? info.ffmpeg.hardware_encoders.join(", ")
              : "none (libx264)"}
          </dd>
          <dt>CPU</dt>
          <dd>
            {info.cpu.count} cores · {info.workers} workers
          </dd>
          <dt>RAM</dt>
          <dd>
            {formatBytes(info.ram.available)} free of {formatBytes(info.ram.total)}
          </dd>
          <dt>CUDA</dt>
          <dd>{info.cuda ? "available" : "not available (CPU mode)"}</dd>
        </dl>
      </Section>

      <Section title="Whisper models">
        <ul className="models">
          {info.models.map((model) => (
            <li key={model.size}>
              <b>{model.size}</b>{" "}
              <span className={model.installed ? "ok" : "muted"}>
                {model.installed ? formatBytes(model.disk_bytes) : "not installed"}
              </span>
              {model.status === "downloading" ? ` · ${model.progress.toFixed(0)}%` : ""}
              {model.installed ? null : (
                <Button onClick={() => download.mutate(model.size)}>
                  {model.status === "downloading" ? "Downloading…" : "Download"}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Fonts">
        <p className="muted">{info.fonts.map((font) => font.family).join(", ") || "none bundled"}</p>
      </Section>
    </div>
  );
}
