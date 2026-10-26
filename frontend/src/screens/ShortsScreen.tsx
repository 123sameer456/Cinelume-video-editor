import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { ClipExport, ClipSpec } from "../api/types";
import { Banner, Button, NumberInput } from "../components/ui";

const MIN_CLIP_SEC = 0.5;
const DEFAULT_CLIP_SEC = 15;

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

function fmt(t: number): string {
  if (!Number.isFinite(t) || t < 0) return "0:00.0";
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function ShortsScreen({ jobId }: { jobId: string }) {
  const job = useQuery({ queryKey: ["job", jobId], queryFn: () => api.getJob(jobId) });
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ id: string; edge: "start" | "end" } | null>(null);

  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [clips, setClips] = useState<ClipSpec[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [exported, setExported] = useState<ClipExport[] | null>(null);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = job.data?.status === "completed";
  const storageKey = `cinelume:shorts:${jobId}`;

  // Markers persist per job so a refresh does not lose them.
  useEffect(() => {
    const raw = window.localStorage.getItem(storageKey);
    setClips([]);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as ClipSpec[];
        if (Array.isArray(parsed)) setClips(parsed);
      } catch {
        /* corrupt cache: start empty */
      }
    }
  }, [storageKey]);

  useEffect(() => {
    window.localStorage.setItem(storageKey, JSON.stringify(clips));
  }, [storageKey, clips]);

  // Any change to the markers makes the previous export stale.
  useEffect(() => {
    setExported(null);
  }, [clips]);

  // Playhead follows the video while it plays.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let raf = 0;
    const tick = () => {
      setTime(video.currentTime);
      raf = window.requestAnimationFrame(tick);
    };
    const onPlay = () => {
      window.cancelAnimationFrame(raf);
      raf = window.requestAnimationFrame(tick);
    };
    const onStop = () => {
      window.cancelAnimationFrame(raf);
      setTime(video.currentTime);
    };
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onStop);
    video.addEventListener("ended", onStop);
    video.addEventListener("seeked", onStop);
    return () => {
      window.cancelAnimationFrame(raf);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onStop);
      video.removeEventListener("ended", onStop);
      video.removeEventListener("seeked", onStop);
    };
  }, [ready]);

  // Preview stops at the clip's OUT point.
  useEffect(() => {
    const video = videoRef.current;
    const clip = clips.find((c) => c.id === previewId);
    if (!video || !clip) return;
    const onTime = () => {
      if (video.currentTime >= clip.end) {
        video.pause();
        setPreviewId(null);
      }
    };
    video.addEventListener("timeupdate", onTime);
    return () => video.removeEventListener("timeupdate", onTime);
  }, [clips, previewId]);

  const timeAt = useCallback(
    (clientX: number) => {
      const bar = barRef.current;
      if (!bar || duration <= 0) return 0;
      const rect = bar.getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return fraction * duration;
    },
    [duration],
  );

  // Dragging a handle updates that edge of the clip.
  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const t = timeAt(event.clientX);
      setClips((current) =>
        current.map((clip) => {
          if (clip.id !== drag.id) return clip;
          return drag.edge === "start"
            ? { ...clip, start: Math.max(0, Math.min(t, clip.end - MIN_CLIP_SEC)) }
            : { ...clip, end: Math.min(duration, Math.max(t, clip.start + MIN_CLIP_SEC)) };
        }),
      );
    };
    const onUp = () => {
      dragRef.current = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [timeAt, duration]);

  const selected = clips.find((c) => c.id === selectedId) ?? null;
  const percent = (t: number) => `${duration > 0 ? (t / duration) * 100 : 0}%`;

  const seek = (t: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.min(Math.max(0, t), duration);
    setTime(video.currentTime);
  };

  const patchClip = (id: string, patch: (clip: ClipSpec) => Partial<ClipSpec>) => {
    setClips((current) =>
      current.map((clip) => (clip.id === id ? { ...clip, ...patch(clip) } : clip)),
    );
  };

  const addClip = () => {
    if (duration <= 0) return;
    const start = Math.min(time, Math.max(0, duration - MIN_CLIP_SEC));
    const end = Math.min(duration, start + DEFAULT_CLIP_SEC);
    const clip: ClipSpec = {
      id: uid(),
      name: `Short ${clips.length + 1}`,
      start,
      end: Math.max(end, start + MIN_CLIP_SEC),
    };
    setClips((current) => [...current, clip]);
    setSelectedId(clip.id);
  };

  const deleteClip = (id: string) => {
    setClips((current) => current.filter((clip) => clip.id !== id));
    if (selectedId === id) setSelectedId(null);
    if (previewId === id) setPreviewId(null);
  };

  const previewClip = (clip: ClipSpec) => {
    const video = videoRef.current;
    if (!video) return;
    setPreviewId(clip.id);
    setSelectedId(clip.id);
    video.currentTime = clip.start;
    void video.play();
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play();
    else video.pause();
  };

  const exportAll = async () => {
    if (clips.length === 0) return;
    setExporting(true);
    setError(null);
    try {
      const result = await api.exportClips(jobId, clips);
      setExported(result.clips);
    } catch (err) {
      setError((err as Error).message ?? "Export failed.");
    } finally {
      setExporting(false);
    }
  };

  if (!ready) {
    return (
      <Banner tone="warn">
        This tab works on a finished video. Run a job first, or open a completed one from
        History.
      </Banner>
    );
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>Shorts &amp; Reels</h2>
        <p className="muted">
          Mark start and end points on the timeline, then export each range as its own clip.
        </p>
        <video
          ref={videoRef}
          controls
          src={api.fileUrl(jobId, "video")}
          className="player"
          onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
        />

        <div
          className="shorts-timeline"
          ref={barRef}
          onPointerDown={(event) => {
            if ((event.target as HTMLElement).dataset.handle) return;
            seek(timeAt(event.clientX));
          }}
        >
          {clips.map((clip, i) => (
            <div
              key={clip.id}
              className={`shorts-range tone-${i % 3} ${clip.id === selectedId ? "selected" : ""}`}
              style={{
                left: percent(clip.start),
                width: percent(Math.max(0, clip.end - clip.start)),
              }}
              title={clip.name}
              onPointerDown={() => setSelectedId(clip.id)}
            />
          ))}
          {selected ? (
            <>
              <button
                type="button"
                className="shorts-handle"
                data-handle="start"
                style={{ left: percent(selected.start) }}
                aria-label="Drag clip start"
                onPointerDown={(event) => {
                  event.stopPropagation();
                  dragRef.current = { id: selected.id, edge: "start" };
                }}
              />
              <button
                type="button"
                className="shorts-handle"
                data-handle="end"
                style={{ left: percent(selected.end) }}
                aria-label="Drag clip end"
                onPointerDown={(event) => {
                  event.stopPropagation();
                  dragRef.current = { id: selected.id, edge: "end" };
                }}
              />
            </>
          ) : null}
          <div className="shorts-playhead" style={{ left: percent(time) }} />
        </div>
        <div className="row spaced muted">
          <span>{fmt(time)}</span>
          <span>{fmt(duration)}</span>
        </div>

        <div className="row">
          <Button onClick={togglePlay}>Play / Pause</Button>
          <Button
            disabled={!selected}
            onClick={() => {
              if (selected) patchClip(selected.id, () => ({ start: Math.min(time, selected.end - MIN_CLIP_SEC) }));
            }}
          >
            Set IN at playhead
          </Button>
          <Button
            disabled={!selected}
            onClick={() => {
              if (selected) patchClip(selected.id, () => ({ end: Math.max(time, selected.start + MIN_CLIP_SEC) }));
            }}
          >
            Set OUT at playhead
          </Button>
          <Button kind="primary" onClick={addClip} disabled={duration <= 0}>
            + Add short here
          </Button>
        </div>
      </div>

      {clips.length === 0 ? (
        <Banner tone="info">
          No shorts yet. Play the video, park the playhead where a short should begin, then press
          “+ Add short here”. Drag the handles on the timeline to fine-tune.
        </Banner>
      ) : (
        <div className="card">
          <div className="row spaced">
            <h2>Clips ({clips.length})</h2>
            <Button kind="primary" onClick={() => void exportAll()} disabled={exporting}>
              {exporting ? "Exporting…" : "Export all shorts"}
            </Button>
          </div>
          {error ? <Banner tone="error">{error}</Banner> : null}
          <div className="stack">
            {clips.map((clip, i) => {
              const item = exported?.[i];
              return (
                <div key={clip.id} className={`clip-row ${clip.id === selectedId ? "selected" : ""}`}>
                  <input
                    className="input clip-name"
                    value={clip.name}
                    onFocus={() => setSelectedId(clip.id)}
                    onChange={(event) => patchClip(clip.id, () => ({ name: event.target.value }))}
                  />
                  <label className="clip-time">
                    IN
                    <NumberInput
                      value={Number(clip.start.toFixed(2))}
                      min={0}
                      max={duration}
                      step={0.1}
                      onChange={(start) =>
                        patchClip(clip.id, (c) => ({ start: Math.min(start, c.end - MIN_CLIP_SEC) }))
                      }
                    />
                  </label>
                  <label className="clip-time">
                    OUT
                    <NumberInput
                      value={Number(clip.end.toFixed(2))}
                      min={0}
                      max={duration}
                      step={0.1}
                      onChange={(end) =>
                        patchClip(clip.id, (c) => ({ end: Math.max(end, c.start + MIN_CLIP_SEC) }))
                      }
                    />
                  </label>
                  <span className="muted">{fmt(Math.max(0, clip.end - clip.start))}</span>
                  <div className="row">
                    <Button onClick={() => previewClip(clip)}>Preview</Button>
                    {item ? (
                      <a
                        className="btn btn-primary"
                        href={api.clipUrl(jobId, item.index)}
                        download={item.download_name}
                      >
                        Download
                      </a>
                    ) : null}
                    <Button onClick={() => deleteClip(clip.id)}>Delete</Button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
