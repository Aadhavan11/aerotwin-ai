import { useEffect, useState } from "react";
import { ApiError } from "../api/client";

export function unavailableMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return "Not available for your role.";
    if (error.status === 404) return "Not available: not found.";
    if (error.status >= 500) return "Not available: server error.";
  }
  return "Not available. Please try again.";
}

/** Ignore late responses and poll only active data; never overlap slow requests. */
export function useAvailable<T>(load: (() => Promise<T>) | null, revision = 0, pollWhile?: (data: T) => boolean): { data?: T; loading: boolean; error?: string } {
  const [state, setState] = useState<{ load: typeof load; data?: T; loading: boolean; error?: string }>({ load, loading: true });
  useEffect(() => {
    let cancelled = false;
    let busy = false;
    let interval: ReturnType<typeof setInterval> | undefined;
    setState({ load, loading: !!load });
    const refresh = async () => {
      if (!load || busy) return;
      busy = true;
      try {
        const data = await load();
        if (cancelled) return;
        setState({ load, data, loading: false });
        if (data && pollWhile?.(data)) {
          interval ??= setInterval(() => { void refresh(); }, 2000);
        } else {
          clearInterval(interval);
          interval = undefined;
        }
      } catch (error) {
        if (!cancelled) {
          setState({ load, loading: false, error: unavailableMessage(error) });
          clearInterval(interval);
          interval = undefined;
        }
      } finally { busy = false; }
    };
    void refresh();
    return () => { cancelled = true; clearInterval(interval); };
  }, [load, revision, pollWhile]);
  return state.load === load ? state : { loading: !!load };
}
