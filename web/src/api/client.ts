import { useCallback, useEffect, useRef, useState } from "react";

const BASE = (import.meta.env.VITE_API as string | undefined) ?? "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<{ data: T; source: string }> {
  const r = await fetch(BASE + path, init);
  const text = await r.text();
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${path}: ${text.slice(0, 200)}`);
  return { data: JSON.parse(text) as T, source: r.headers.get("x-data-source") ?? "" };
}

export const get = <T,>(path: string) => request<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

/** Fetch `path` now and then every `ms` milliseconds (0 = once). Keeps the last good data if a refresh fails. */
export function usePoll<T>(path: string | null, ms: number) {
  const [state, setState] = useState<{ data?: T; error?: string; source?: string; loading: boolean }>({ loading: true });
  const alive = useRef(true);
  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const { data, source } = await get<T>(path);
      if (alive.current) setState({ data, source, loading: false });
    } catch (e) {
      if (alive.current) setState((s) => ({ ...s, error: String((e as Error).message ?? e), loading: false }));
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    reload();
    const id = ms > 0 ? setInterval(() => { if (!document.hidden) reload(); }, ms) : undefined;
    return () => { alive.current = false; if (id) clearInterval(id); };
  }, [reload, ms]);
  return { ...state, reload };
}
