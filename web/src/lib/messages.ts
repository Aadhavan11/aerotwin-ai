import { useEffect } from "react";
import { usePoll } from "../api/client";

/** Anything that reads, sends or clears a message calls this so the bell and the lists update at once instead of at the next poll. */
export const notifyMessages = () => window.dispatchEvent(new Event("aerotwin:messages"));

/** How many messages are unread for the signed-in person (the blue badge on the bell). */
export function useUnread(enabled: boolean): number {
  const p = usePoll<{ count: number }>(enabled ? "/messages/unread" : null, 8000);
  const reload = p.reload;
  useEffect(() => {
    window.addEventListener("aerotwin:messages", reload);
    return () => window.removeEventListener("aerotwin:messages", reload);
  }, [reload]);
  return p.data?.count ?? 0;
}
