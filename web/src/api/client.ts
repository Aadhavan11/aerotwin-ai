import { useCallback, useEffect, useRef, useState } from "react";

const BASE = (import.meta.env.VITE_API as string | undefined) ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; source: string }> {
  const r = await fetch(BASE + path, init);
  const text = await r.text();
  if (!r.ok)
    throw new ApiError(r.status, `${r.status} ${path}: ${text.slice(0, 200)}`);
  return {
    data: (text ? JSON.parse(text) : null) as T,
    source: r.headers.get("x-data-source") ?? "",
  };
}

export const get = <T>(path: string) => request<T>(path);
export const del = <T>(path: string) => request<T>(path, { method: "DELETE" });
export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

/** Fetch `path` now and then every `ms` milliseconds (0 = once). Keeps the last good data if a refresh fails. */
export function usePoll<T>(path: string | null, ms: number) {
  const [state, setState] = useState<{
    path: string | null;
    data?: T;
    error?: string;
    source?: string;
    loading: boolean;
  }>({ path, loading: true });
  const active = useRef({ path, generation: 0, alive: true });
  if (active.current.path !== path) {
    active.current.path = path;
    active.current.generation++;
  }
  const reload = useCallback(async () => {
    if (!path) return;
    const generation = ++active.current.generation;
    try {
      const { data, source } = await get<T>(path);
      if (
        active.current.alive &&
        active.current.path === path &&
        active.current.generation === generation
      )
        setState({ path, data, source, loading: false });
    } catch (e) {
      if (
        active.current.alive &&
        active.current.path === path &&
        active.current.generation === generation
      )
        setState((s) => ({
          ...(s.path === path ? s : { path }),
          error: String((e as Error).message ?? e),
          loading: false,
        }));
    }
  }, [path]);
  useEffect(() => {
    active.current.alive = true;
    reload();
    // Slow down (not stop) when the page reports itself hidden: embedded views and some screen
    // recorders report hidden while visible, and a frozen dashboard is worse than a few extra polls.
    let tick = 0;
    const id =
      ms > 0
        ? setInterval(() => {
            tick++;
            if (!document.hidden || tick % 4 === 0) reload();
          }, ms)
        : undefined;
    return () => {
      active.current.alive = false;
      active.current.generation++;
      if (id) clearInterval(id);
    };
  }, [reload, ms]);
  // A route change must never render the previous aircraft's data while loading.
  const visible = state.path === path ? state : { path, loading: true };
  return { ...visible, reload };
}
