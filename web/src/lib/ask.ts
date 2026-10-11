import { useLocation } from "react-router-dom";
import { post } from "../api/client";
import type { ChatResponse } from "../api/types";
import { useRole } from "./role";
import { collectScreen } from "./screen";

/** Ask the copilot a question with the screen attached, so "this aircraft", "it" and "here" mean what the person is looking at. */
export function useAsk() {
  const loc = useLocation();
  const { role } = useRole();
  return async (message: string, focusTail?: string | null): Promise<ChatResponse> => {
    const screen = collectScreen(loc.pathname, loc.search, role.label);
    // On the Copilot page the person can point the AI at one aircraft; then that aircraft is "on screen" for the question.
    if (focusTail) Object.assign(screen, { tail: focusTail, selected: screen.selected ?? null });
    const { data } = await post<ChatResponse>("/copilot/chat", { session_id: "web", message, screen });
    return data;
  };
}

/** Does this text read like a question for the copilot (rather than a name or a filter)? */
export const looksLikeQuestion = (text: string): boolean => {
  const t = text.trim();
  return t.length > 3 && (/\?$/.test(t) || t.split(/\s+/).length >= 3);
};
