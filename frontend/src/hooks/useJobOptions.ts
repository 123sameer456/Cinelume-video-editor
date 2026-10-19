/** Options with a per-section reset, persisted to localStorage between runs. */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { JobOptions } from "../api/types";

const STORAGE_KEY = "cinelume.options";

function mergeSection<T>(fallback: T, stored: Partial<T> | undefined): T {
  return { ...fallback, ...(stored ?? {}) };
}

function mergeDefaults(defaults: JobOptions, stored: Partial<JobOptions> | null): JobOptions {
  if (!stored) {
    return defaults;
  }
  return {
    denoise: mergeSection(defaults.denoise, stored.denoise),
    silence: mergeSection(defaults.silence, stored.silence),
    subtitles: mergeSection(defaults.subtitles, stored.subtitles),
    music: mergeSection(defaults.music, stored.music),
    cover: mergeSection(defaults.cover, stored.cover),
    output: mergeSection(defaults.output, stored.output),
    performance: mergeSection(defaults.performance, stored.performance),
  };
}

export function useJobOptions() {
  const [serverDefaults, setServerDefaults] = useState<JobOptions | null>(null);
  const [options, setOptions] = useState<JobOptions | null>(null);

  useQuery({
    queryKey: ["defaults"],
    queryFn: async () => {
      const data = await api.defaults();
      setServerDefaults(data);
      setOptions((current) => {
        if (current) {
          return mergeDefaults(data, current);
        }
        let stored: Partial<JobOptions> | null = null;
        try {
          const raw = localStorage.getItem(STORAGE_KEY);
          stored = raw ? (JSON.parse(raw) as Partial<JobOptions>) : null;
        } catch {
          stored = null;
        }
        return mergeDefaults(data, stored);
      });
      return data;
    },
    staleTime: Infinity,
  });

  const update = <K extends keyof JobOptions>(section: K, patch: Partial<JobOptions[K]>) => {
    setOptions((current) => {
      if (!current) {
        return current;
      }
      const next = {
        ...current,
        [section]: { ...current[section], ...patch },
      } as JobOptions;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const resetSection = (section: keyof JobOptions) => {
    if (!serverDefaults) {
      return;
    }
    setOptions((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current, [section]: serverDefaults[section] } as JobOptions;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const resetAll = () => {
    if (!serverDefaults) {
      return;
    }
    localStorage.removeItem(STORAGE_KEY);
    setOptions(serverDefaults);
  };

  return {
    options,
    defaults: serverDefaults,
    update,
    resetSection,
    resetAll,
    isLoading: serverDefaults === null,
  };
}
