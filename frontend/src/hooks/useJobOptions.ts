import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { JobOptions } from "../api/types";

/** Options with a per-section reset, persisted to localStorage between runs. */

const STORAGE_KEY = "cinelume.options";

export function useDefaults() {
  return useQuery({
    queryKey: ["defaults"],
    queryFn: () => api.defaults(),
    staleTime: Infinity,
  });
}

function mergeDefaults(defaults: JobOptions, stored: Partial<JobOptions> | null): JobOptions {
  if (!stored) {
    return defaults;
  }
  return {
    denoise: { ...defaults.denoise, ...(stored.denoise ?? {}) },
    silence: { ...defaults.silence, ...(stored.silence ?? {}) },
    subtitles: { ...defaults.subtitles, ...(stored.subtitles ?? {}) },
    music: { ...defaults.music, ...(stored.music ?? {}) },
    cover: { ...defaults.cover, ...(stored.cover ?? {}) },
    output: { ...defaults.output, ...(stored.output ?? {}) },
    performance: { ...defaults.performance, ...(stored.performance ?? {}) },
  };
}

export function useJobOptions() {
  const defaults = useDefaults();
  const client = useQueryClient();
  const [options, setOptions] = useState<JobOptions | null>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) as JobOptions) : null;
    } catch {
      return null;
    }
  });

  const update = (section: keyof JobOptions, patch: Partial<JobOptions[typeof section]>) => {
    setOptions((current) => {
      const base = current ?? defaults.data;
      if (!base) {
        return current;
      }
      const next = { ...base, [section]: { ...base[section], ...patch } };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const resetSection = (section: keyof JobOptions) => {
    if (!defaults.data) {
      return;
    }
    setOptions((current) => {
      const base = current ?? defaults.data;
      const next = { ...base, [section]: defaults.data[section] };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
    void client.invalidateQueries({ queryKey: ["defaults"] });
  };

  const resetAll = () => {
    if (!defaults.data) {
      return;
    }
    localStorage.removeItem(STORAGE_KEY);
    setOptions(defaults.data);
  };

  return { options, defaults: defaults.data, update, resetSection, resetAll, isLoading: defaults.isLoading };
}
