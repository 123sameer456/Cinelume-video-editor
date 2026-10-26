import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { fieldMessages, type ApiError } from "../api/errors";
import type { JobOptions } from "../api/types";
import { Banner, Button, ColorInput, Field, NumberInput, Section, Select, Slider, Toggle } from "../components/ui";

export function ConfigureScreen({
  uploadId,
  options,
  update,
  resetSection,
  onStart,
  busy,
  startError,
}: {
  uploadId: string;
  options: JobOptions | null;
  update: <K extends keyof JobOptions>(section: K, patch: Partial<JobOptions[K]>) => void;
  resetSection: (section: keyof JobOptions) => void;
  onStart: () => void;
  busy: boolean;
  startError: ApiError | null;
}) {
  const [open, setOpen] = useState<string | null>("silence");
  const fonts = useQuery({ queryKey: ["fonts"], queryFn: () => api.fonts() });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api.listModels() });

  if (!options) {
    return <p className="muted">Loading defaults…</p>;
  }

  const toggle = (name: string) => setOpen((current) => (current === name ? null : name));
  const musicInvalid = options.music.enabled && !options.music.track_id;
  const fontOptions = (fonts.data?.fonts ?? []).map((font) => ({
    value: font.family,
    label: font.family,
  }));

  return (
    <div className="stack">
      {startError ? (
        <Banner tone="error">
          <b>The settings were rejected:</b>
          <ul className="warnings">
            {fieldMessages(startError).map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
      <Section
        title="Silence"
        open={open === "silence"}
        onHeaderClick={() => toggle("silence")}
        actions={
          <Toggle
            label="Enabled"
            checked={options.silence.enabled}
            onChange={(enabled) => update("silence", { enabled })}
            tip="Automatically remove the silent parts of your video so it plays with no dead air."
          />
        }
      >
        {open === "silence" && options.silence.enabled ? (
          <>
            <Field
              label="Cut silence longer than"
              hint="seconds"
              tip="Silent gaps longer than this are removed — lower keeps more natural pauses, higher cuts more."
            >
              <Slider
                value={options.silence.min_silence_sec}
                min={0.1}
                max={10}
                step={0.1}
                onChange={(min_silence_sec) => update("silence", { min_silence_sec })}
              />
            </Field>
            <Field
              label="Threshold dB"
              hint={options.silence.auto_threshold ? "auto is on" : undefined}
              tip="How quiet the audio must be to count as silence — lower only trims dead silence, higher also trims background noise."
            >
              <Slider
                value={options.silence.threshold_db}
                min={-60}
                max={-15}
                onChange={(threshold_db) => update("silence", { threshold_db })}
              />
            </Field>
            <Toggle
              label="Auto threshold (from the noise floor)"
              checked={options.silence.auto_threshold}
              onChange={(auto_threshold) => update("silence", { auto_threshold })}
              tip="Measure the quietest level in your audio and pick the silence cut-off for you, instead of a fixed value."
            />
            <Field
              label="Keep padding (s)"
              tip="Silence left around each cut so speech does not start or end abruptly."
            >
              <Slider
                value={options.silence.keep_padding_sec}
                min={0}
                max={1}
                step={0.01}
                onChange={(keep_padding_sec) => update("silence", { keep_padding_sec })}
              />
            </Field>
            <Field
              label="Drop speech shorter than (s)"
              tip="Very short sounds under this length are treated as noise (clicks, breaths) and removed."
            >
              <NumberInput
                value={options.silence.min_speech_sec}
                min={0}
                max={2}
                step={0.05}
                onChange={(min_speech_sec) => update("silence", { min_speech_sec })}
              />
            </Field>
            <Toggle
              label="Fade at every join"
              checked={options.silence.fade_enabled}
              onChange={(fade_enabled) => update("silence", { fade_enabled })}
              tip="Smoothly fade picture and sound at each cut point instead of jumping instantly."
            />
            {options.silence.fade_enabled ? (
              <>
                <Field
                  label="Fade length (s)"
                  tip="How long each fade at the joins lasts — short fades are barely visible, long ones are a soft dissolve."
                >
                  <Slider
                    value={options.silence.fade_sec}
                    min={0.03}
                    max={0.5}
                    step={0.01}
                    onChange={(fade_sec) => update("silence", { fade_sec })}
                  />
                </Field>
                <Toggle
                  label="Fade video"
                  checked={options.silence.fade_video}
                  onChange={(fade_video) => update("silence", { fade_video })}
                  tip="Fade the picture to the fade colour at the joins."
                />
                <Toggle
                  label="Fade audio"
                  checked={options.silence.fade_audio}
                  onChange={(fade_audio) => update("silence", { fade_audio })}
                  tip="Fade the sound in and out at the joins so cuts never pop or click."
                />
                <Field
                  label="Fade colour"
                  tip="Colour the video briefly fades through at each join (black is the usual choice)."
                >
                  <ColorInput
                    value={options.silence.fade_color}
                    onChange={(fade_color) => update("silence", { fade_color })}
                  />
                </Field>
                <Toggle
                  label="Also fade the very start and end"
                  checked={options.silence.fade_first_last}
                  onChange={(fade_first_last) => update("silence", { fade_first_last })}
                  tip="Fade in at the very beginning and fade out at the very end of the video too."
                />
              </>
            ) : null}
          </>
        ) : null}
      </Section>

      <Section
        title="Subtitles"
        open={open === "subtitles"}
        onHeaderClick={() => toggle("subtitles")}
        actions={
          <Toggle
            label="Enabled"
            checked={options.subtitles.enabled}
            onChange={(enabled) => update("subtitles", { enabled })}
            tip="Transcribe the spoken words and burn animated captions into the video."
          />
        }
      >
        {open === "subtitles" && options.subtitles.enabled ? (
          <>
            <Field
              label="Model"
              hint={models.data?.models.find((m) => m.size === options.subtitles.model_size)?.installed ? "downloaded" : "not downloaded"}
              tip="Whisper model used for transcription — bigger understands more accents and noise but runs slower. Download it in the System tab first."
            >
              <Select
                value={options.subtitles.model_size}
                options={[
                  { value: "tiny", label: "tiny (fastest)" },
                  { value: "base", label: "base" },
                  { value: "small", label: "small (default)" },
                  { value: "medium", label: "medium" },
                  { value: "large-v3", label: "large-v3" },
                ]}
                onChange={(model_size) => update("subtitles", { model_size })}
              />
            </Field>
            <Field
              label="Language"
              tip="Language spoken in the video — auto-detect listens to the first 30 seconds to work it out; pick yours for faster, more accurate captions."
            >
              <Select
                value={options.subtitles.language}
                options={[
                  { value: "auto", label: "auto-detect" },
                  { value: "en", label: "English" },
                  { value: "ur", label: "Urdu" },
                  { value: "ar", label: "Arabic" },
                  { value: "hi", label: "Hindi" },
                  { value: "es", label: "Spanish" },
                  { value: "fr", label: "French" },
                  { value: "de", label: "German" },
                ]}
                onChange={(language) => update("subtitles", { language })}
              />
            </Field>
            <Field
              label="Mode"
              tip="Caption style: karaoke fill paints each word as it is spoken, word highlight brightens the current word, static shows plain text."
            >
              <Select
                value={options.subtitles.mode}
                options={[
                  { value: "karaoke_fill", label: "karaoke fill" },
                  { value: "word_highlight", label: "word highlight" },
                  { value: "static", label: "static" },
                ]}
                onChange={(mode) => update("subtitles", { mode })}
              />
            </Field>
            <div className="grid-2">
              <Field label="Text colour" tip="Colour of the caption text.">
                <ColorInput
                  value={options.subtitles.text_color}
                  onChange={(text_color) => update("subtitles", { text_color })}
                />
              </Field>
              <Field
                label="Highlight colour"
                tip="Colour used for the word currently being spoken in karaoke and word-highlight modes."
              >
                <ColorInput
                  value={options.subtitles.highlight_color}
                  onChange={(highlight_color) => update("subtitles", { highlight_color })}
                />
              </Field>
            </div>
            <div className="preview" aria-label="approximate subtitle preview">
              <span style={{ color: options.subtitles.text_color, background: options.subtitles.background_mode === "none" ? "transparent" : options.subtitles.background_color }}>
                word <b style={{ color: options.subtitles.highlight_color }}>highlight</b> sample
              </span>
            </div>
            <div className="grid-2">
              <Field
                label="Font"
                tip="Font family for the captions, chosen from the fonts available on the server."
              >
                <Select
                  value={options.subtitles.font_family}
                  options={fontOptions.length > 0 ? fontOptions : [{ value: options.subtitles.font_family, label: options.subtitles.font_family }]}
                  onChange={(font_family) => update("subtitles", { font_family })}
                />
              </Field>
              <Field
                label="Size (% of height)"
                tip="Caption text size as a percentage of the video height — 4–5% reads well on phones."
              >
                <Slider
                  value={options.subtitles.font_size_pct}
                  min={2}
                  max={10}
                  step={0.1}
                  onChange={(font_size_pct) => update("subtitles", { font_size_pct })}
                />
              </Field>
            </div>
            <Field
              label="Background"
              tip="Add a solid box behind or an outline around the text so it stays readable on busy footage."
            >
              <Select
                value={options.subtitles.background_mode}
                options={[
                  { value: "none", label: "none" },
                  { value: "box", label: "box" },
                  { value: "outline", label: "outline" },
                ]}
                onChange={(background_mode) => update("subtitles", { background_mode })}
              />
            </Field>
            {options.subtitles.background_mode !== "none" ? (
              <div className="grid-2">
                <Field
                  label="Background colour"
                  tip="Colour of the box or outline drawn behind the caption text."
                >
                  <ColorInput
                    value={options.subtitles.background_color}
                    onChange={(background_color) => update("subtitles", { background_color })}
                  />
                </Field>
                <Field
                  label="Opacity"
                  tip="How solid the background is — 0 is invisible, 100 fully blocks the video behind the text."
                >
                  <Slider
                    value={options.subtitles.background_opacity}
                    min={0}
                    max={100}
                    onChange={(background_opacity) => update("subtitles", { background_opacity })}
                  />
                </Field>
              </div>
            ) : null}
            <Field
              label="Position"
              tip="Where the captions sit on the video — bottom is the standard for social media."
            >
              <Select
                value={options.subtitles.position}
                options={[
                  { value: "bottom", label: "bottom" },
                  { value: "center", label: "center" },
                  { value: "top", label: "top" },
                ]}
                onChange={(position) => update("subtitles", { position })}
              />
            </Field>
            <Field
              label="Margin from edge (% of height)"
              hint="keeps text clear of platform UI"
              tip="Gap between the captions and the video edge, so text clears buttons and overlays that apps draw on top of videos."
            >
              <Slider
                value={options.subtitles.margin_v_pct}
                min={0}
                max={40}
                onChange={(margin_v_pct) => update("subtitles", { margin_v_pct })}
              />
            </Field>
            <div className="grid-3">
              <Field
                label="Words per line"
                tip="Maximum words grouped on one caption line before the text wraps to the next."
              >
                <NumberInput
                  value={options.subtitles.words_per_line}
                  min={1}
                  max={8}
                  onChange={(words_per_line) => update("subtitles", { words_per_line })}
                />
              </Field>
              <Field
                label="Max chars/line"
                tip="Maximum characters per caption line — shorter lines are easier to read at a glance."
              >
                <NumberInput
                  value={options.subtitles.max_chars_per_line}
                  min={10}
                  max={60}
                  onChange={(max_chars_per_line) => update("subtitles", { max_chars_per_line })}
                />
              </Field>
              <Field
                label="Max lines"
                tip="Maximum caption lines shown at once — extra words move to the next caption."
              >
                <NumberInput
                  value={options.subtitles.max_lines}
                  min={1}
                  max={3}
                  onChange={(max_lines) => update("subtitles", { max_lines })}
                />
              </Field>
            </div>
            <Toggle
              label="UPPERCASE"
              checked={options.subtitles.uppercase}
              onChange={(uppercase) => update("subtitles", { uppercase })}
              tip="Show all caption text in capitals — a common style for short-form social videos."
            />
            {options.subtitles.mode === "word_highlight" ? (
              <Toggle
                label="Pop animation"
                checked={options.subtitles.pop_animation}
                onChange={(pop_animation) => update("subtitles", { pop_animation })}
                tip="Give each word a small pop/scale effect at the moment it is spoken (word-highlight mode only)."
              />
            ) : null}
          </>
        ) : null}
      </Section>

      <Section
        title="Music"
        open={open === "music"}
        onHeaderClick={() => toggle("music")}
        actions={
          <Toggle
            label="Enabled"
            checked={options.music.enabled}
            onChange={(enabled) => update("music", { enabled })}
            tip="Mix a background music track underneath your video's own audio."
          />
        }
      >
        {open === "music" && options.music.enabled ? (
          <MusicSection options={options} update={update} />
        ) : null}
      </Section>

      <Section
        title="Cover"
        open={open === "cover"}
        onHeaderClick={() => toggle("cover")}
        actions={
          <Toggle
            label="Enabled"
            checked={options.cover.enabled}
            onChange={(enabled) => update("cover", { enabled })}
            tip="Generate a thumbnail/poster image from the video, downloadable as cover.png."
          />
        }
      >
        {open === "cover" && options.cover.enabled ? (
          <CoverSection options={options} update={update} uploadId={uploadId} />
        ) : null}
      </Section>

      <Section
        title="Output"
        open={open === "output"}
        onHeaderClick={() => toggle("output")}
        actions={<Button onClick={() => resetSection("output")}>Reset</Button>}
      >
        {open === "output" ? (
          <>
            <Field
              label="Resolution"
              tip="Final video size — 'original' keeps the input size, or pick a platform format such as vertical 1080×1920 for Reels/TikTok."
            >
              <Select
                value={options.output.resolution}
                options={[
                  { value: "original", label: "original" },
                  { value: "1080x1920", label: "1080×1920 (vertical)" },
                  { value: "1920x1080", label: "1920×1080" },
                  { value: "1080x1080", label: "1080×1080" },
                  { value: "720x1280", label: "720×1280" },
                ]}
                onChange={(resolution) => update("output", { resolution })}
              />
            </Field>
            <Field
              label="Fit"
              tip="When the resolution changes: crop fills the new frame by trimming the edges, pad keeps everything and adds borders."
            >
              <Select
                value={options.output.fit}
                options={[
                  { value: "crop", label: "crop" },
                  { value: "pad", label: "pad" },
                ]}
                onChange={(fit) => update("output", { fit })}
              />
            </Field>
            <div className="grid-2">
              <Field
                label="Quality (CRF, lower = better)"
                tip="Compression quality — lower numbers look sharper but create bigger files, higher numbers shrink files at the cost of detail."
              >
                <Slider
                  value={options.output.crf}
                  min={14}
                  max={35}
                  onChange={(crf) => update("output", { crf })}
                />
              </Field>
              <Field
                label="Preset"
                tip="Encoder speed vs compression — slower presets render longer but produce smaller, cleaner files for the same quality."
              >
                <Select
                  value={options.output.preset}
                  options={[
                    { value: "ultrafast", label: "ultrafast" },
                    { value: "superfast", label: "superfast" },
                    { value: "veryfast", label: "veryfast" },
                    { value: "faster", label: "faster" },
                    { value: "fast", label: "fast" },
                    { value: "medium", label: "medium" },
                    { value: "slow", label: "slow" },
                  ]}
                  onChange={(preset) => update("output", { preset })}
                />
              </Field>
            </div>
            <Field
              label="Encoder"
              tip="Which engine renders the video — 'auto' picks the fastest one your machine supports (a GPU encoder when available)."
            >
              <Select
                value={options.output.encoder}
                options={[
                  { value: "auto", label: "auto" },
                  { value: "libx264", label: "libx264" },
                  { value: "h264_nvenc", label: "NVENC" },
                  { value: "h264_qsv", label: "QuickSync" },
                  { value: "h264_amf", label: "AMF" },
                ]}
                onChange={(encoder) => update("output", { encoder })}
              />
            </Field>
            <Toggle
              label="Loudness normalization"
              checked={options.output.loudnorm}
              onChange={(loudnorm) => update("output", { loudnorm })}
              tip="Even out the final audio to one consistent loudness, so quiet speech and loud peaks end up balanced."
            />
            {options.output.loudnorm ? (
              <Field
                label="Target LUFS"
                tip="The loudness level the final audio is adjusted to — −14 LUFS is the standard for YouTube, Instagram and TikTok."
              >
                <Slider
                  value={options.output.target_lufs}
                  min={-24}
                  max={-9}
                  onChange={(target_lufs) => update("output", { target_lufs })}
                />
              </Field>
            ) : null}
          </>
        ) : null}
      </Section>

      <Section
        title="Advanced"
        open={open === "advanced"}
        onHeaderClick={() => toggle("advanced")}
      >
        {open === "advanced" ? (
          <>
            <Field
              label="Chunking"
              tip="Split long videos into parts that render in parallel — 'auto' chunks anything longer than the threshold, 'off' processes the whole video at once."
            >
              <Select
                value={options.performance.chunking}
                options={[
                  { value: "auto", label: "auto" },
                  { value: "on", label: "always" },
                  { value: "off", label: "off" },
                ]}
                onChange={(chunking) => update("performance", { chunking })}
              />
            </Field>
            <Field
              label="Chunk length (s)"
              tip="Rough length of each part when chunking is used — shorter chunks spread the work across more workers."
            >
              <NumberInput
                value={options.performance.target_chunk_sec}
                min={30}
                max={600}
                onChange={(target_chunk_sec) => update("performance", { target_chunk_sec })}
              />
            </Field>
            <div className="grid-2">
              <Field
                label="Media workers"
                tip="How many video parts render at the same time — 0 picks a value from your CPU. More workers are faster but heavier on CPU and RAM."
              >
                <NumberInput
                  value={options.performance.workers ?? 0}
                  min={0}
                  max={16}
                  onChange={(workers) => update("performance", { workers: workers === 0 ? null : workers })}
                />
              </Field>
              <Field
                label="ASR workers"
                tip="How many transcription processes run at once — more is faster but each loads its own copy of the Whisper model in memory."
              >
                <NumberInput
                  value={options.performance.asr_workers}
                  min={1}
                  max={4}
                  onChange={(asr_workers) => update("performance", { asr_workers })}
                />
              </Field>
            </div>
            <Toggle
              label="Continue without subtitles if transcription fails"
              checked={options.performance.degrade_on_asr_failure}
              onChange={(degrade_on_asr_failure) => update("performance", { degrade_on_asr_failure })}
              tip="If transcription fails, finish the video without captions instead of failing the whole job."
            />
          </>
        ) : null}
      </Section>

      {musicInvalid ? (
        <Banner tone="warn">
          Music is switched on but no track is selected — pick or upload one in the Music
          section, or switch Music off.
        </Banner>
      ) : null}
      {busy ? <Banner tone="info">Starting…</Banner> : null}
      <Button kind="primary" onClick={onStart} disabled={busy || musicInvalid}>
        Start editing →
      </Button>
    </div>
  );
}

function MusicSection({
  options,
  update,
}: {
  options: JobOptions;
  update: <K extends keyof JobOptions>(section: K, patch: Partial<JobOptions[K]>) => void;
}) {
  const tracks = useQuery({ queryKey: ["music"], queryFn: () => api.listTracks() });
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <>
      <Field
        label="Track"
        hint="you are responsible for having the rights to use it"
        tip="Choose the background music from the tracks in your library."
      >
        <Select
          value={options.music.track_id ?? ""}
          options={[
            { value: "", label: tracks.data?.tracks.length ? "choose a track" : "no tracks yet" },
            ...(tracks.data?.tracks ?? []).map((track) => ({
              value: track.id,
              label: `${track.name} (${track.duration.toFixed(1)}s)`,
            })),
          ]}
          onChange={(track_id) => update("music", { track_id: track_id || null })}
        />
      </Field>
      <label className="field">
        <span className="btn">
          {uploading ? "Uploading…" : "Upload mp3 / wav / m4a / flac"}
          <input
            type="file"
            accept=".mp3,.wav,.m4a,.ogg,.flac,audio/*"
            hidden
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              setUploading(true);
              setError(null);
              try {
                const { uploadMusicTrack } = await import("../api/client");
                await uploadMusicTrack(file);
                await tracks.refetch();
              } catch (err) {
                setError((err as Error).message ?? "upload failed");
              } finally {
                setUploading(false);
              }
            }}
          />
        </span>
      </label>
      {error ? <Banner tone="error">{error}</Banner> : null}
      <Field
        label="Volume"
        tip="How loud the music plays under your voice — low values give a subtle bed, high values make the music prominent."
      >
        <Slider
          value={options.music.volume}
          min={0}
          max={100}
          onChange={(volume) => update("music", { volume })}
        />
      </Field>
      <Toggle
        label="Loop"
        checked={options.music.loop}
        onChange={(loop) => update("music", { loop })}
        tip="Restart the track from the beginning whenever it ends, so music covers a video longer than the song."
      />
      <div className="grid-2">
        <Field
          label="Fade in (s)"
          tip="Seconds at the start of the video for the music to rise from silence."
        >
          <NumberInput
            value={options.music.fade_in_sec}
            min={0}
            max={10}
            step={0.1}
            onChange={(fade_in_sec) => update("music", { fade_in_sec })}
          />
        </Field>
        <Field
          label="Fade out (s)"
          tip="Seconds at the end of the video for the music to fade away gently."
        >
          <NumberInput
            value={options.music.fade_out_sec}
            min={0}
            max={10}
            step={0.1}
            onChange={(fade_out_sec) => update("music", { fade_out_sec })}
          />
        </Field>
      </div>
      <Toggle
        label="Duck under speech"
        checked={options.music.ducking_enabled}
        onChange={(ducking_enabled) => update("music", { ducking_enabled })}
        tip="Automatically lower the music while you are talking and bring it back up in the pauses."
      />
      {options.music.ducking_enabled ? (
        <Field
          label="Ducking strength"
          tip="How strongly the music is dipped while you speak — higher means quieter music under your voice."
        >
          <Slider
            value={options.music.ducking_strength}
            min={0}
            max={100}
            onChange={(ducking_strength) => update("music", { ducking_strength })}
          />
        </Field>
      ) : null}
    </>
  );
}

function CoverSection({
  options,
  update,
  uploadId,
}: {
  options: JobOptions;
  update: <K extends keyof JobOptions>(section: K, patch: Partial<JobOptions[K]>) => void;
  uploadId: string;
}) {
  const [cover, setCover] = useState<{ time_sec: number; image_url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const render = async (newSeed: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const seed = newSeed ? Math.floor(Math.random() * 1_000_000) : options.cover.seed;
      const result = await api.renderCover(uploadId, {
        ...options.cover,
        seed,
        lines: options.cover.lines.filter((line) => line.trim().length > 0),
      });
      setCover(result);
      update("cover", { seed });
    } catch (err) {
      setError((err as Error).message ?? "cover failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Field
        label="Frame"
        tip="Which frame becomes the cover: pick the sharpest-looking frame automatically, or choose the exact time yourself."
      >
        <Select
          value={options.cover.time_mode}
          options={[
            { value: "random", label: "pick the sharpest frame" },
            { value: "manual", label: "manual time" },
          ]}
          onChange={(time_mode) => update("cover", { time_mode })}
        />
      </Field>
      {options.cover.time_mode === "manual" ? (
        <Field
          label="Time (s)"
          tip="The exact moment in the video used as the cover image."
        >
          <NumberInput
            value={options.cover.time_sec ?? 1}
            min={0}
            step={0.1}
            onChange={(time_sec) => update("cover", { time_sec })}
          />
        </Field>
      ) : null}
      <Field
        label="Text lines (up to 4)"
        tip="Headline text drawn on top of the cover image, one line per box."
      >
        {[0, 1, 2, 3].map((index) => (
          <input
            key={index}
            className="input"
            value={options.cover.lines[index] ?? ""}
            placeholder={index === 0 ? "First line" : "…"}
            onChange={(event) => {
              const lines = [...options.cover.lines];
              // "" (never undefined/null) so the array is always dense strings
              lines[index] = event.target.value;
              for (let fill = 0; fill < 4; fill += 1) {
                lines[fill] = lines[fill] ?? "";
              }
              update("cover", { lines: lines.slice(0, 4) });
            }}
          />
        ))}
      </Field>
      <div className="grid-2">
        <Field label="Text colour" tip="Colour of the cover headline text.">
          <ColorInput
            value={options.cover.text_color}
            onChange={(text_color) => update("cover", { text_color })}
          />
        </Field>
        <Field
          label="Stroke colour"
          tip="Outline colour around the cover text so it stays readable on any background."
        >
          <ColorInput
            value={options.cover.stroke_color}
            onChange={(stroke_color) => update("cover", { stroke_color })}
          />
        </Field>
      </div>
      <Field
        label="Aspect"
        tip="Cover image shape — same as the video, or a platform preset like 9:16 vertical or 1:1 square."
      >
        <Select
          value={options.cover.aspect}
          options={[
            { value: "same_as_video", label: "same as video" },
            { value: "9:16", label: "9:16" },
            { value: "1:1", label: "1:1" },
            { value: "16:9", label: "16:9" },
            { value: "4:5", label: "4:5" },
          ]}
          onChange={(aspect) => update("cover", { aspect })}
        />
      </Field>
      <div className="row">
        <Button onClick={() => void render(false)} disabled={busy}>
          {busy ? "Rendering…" : "Generate cover"}
        </Button>
        <Button onClick={() => void render(true)} disabled={busy}>
          Re-roll
        </Button>
      </div>
      {error ? <Banner tone="error">{error}</Banner> : null}
      {cover ? (
        <img className="cover-preview" src={cover.image_url} alt={`cover at ${cover.time_sec.toFixed(2)}s`} />
      ) : null}
    </>
  );
}
