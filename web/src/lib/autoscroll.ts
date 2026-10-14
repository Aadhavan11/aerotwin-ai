import { useCallback, useEffect, useRef } from "react";

/**
 * Keep a scrolling area pinned to its newest content while it grows (a chat thread, an agent timeline).
 * If the person scrolls up to read, it stops following until they come back to the bottom or `pin()` is called.
 */
export function useAutoScroll<T extends HTMLElement>(changes: unknown) {
  const ref = useRef<T>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [changes]);
  const pin = useCallback(() => {
    pinned.current = true;
  }, []);
  return [ref, pin] as const;
}
