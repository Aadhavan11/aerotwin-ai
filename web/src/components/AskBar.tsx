import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { usePoll } from "../api/client";
import type { ChatResponse, FleetOverview, WorkOrder } from "../api/types";
import { looksLikeQuestion, useAsk } from "../lib/ask";
import { ROLES, useRole } from "../lib/role";
import { niceComponent } from "../lib/derive";
import { collectScreen } from "../lib/screen";
import { AnswerView } from "./AskPanel";
import Icon from "./Icon";

type Item = { key: string; label: string; hint?: string; run: () => void; ask?: boolean };

/** Questions that make sense on the page you are looking at. */
function suggestionsFor(page: string, tail: string | null): string[] {
  if (tail) return [`What is wrong with this aircraft?`, `When does ${tail} have to be fixed?`, `What happens if we delay the maintenance?`];
  if (page === "Control room") return ["Which work orders need a decision first?", "Which aircraft are most likely to need maintenance in the next 72 hours?", "Why is the plan scheduled this way?"];
  if (page === "Maintenance plan") return ["Why was this slot chosen?", "What changes if the DFW hangar closes?", "Compare the current plan with the baseline."];
  if (page === "Work orders and resources") return ["Which parts and technicians do these jobs need?", "What is out of stock?", "Which station has the most free bays?"];
  return ["Which aircraft need attention?", "Which aircraft are most likely to need maintenance in the next 72 hours?", "Find the best maintenance windows for tomorrow."];
}

/**
 * The one place to search and ask. Type a tail number, a page or a work order and jump there; type a question and get the answer in place.
 * Opens with Ctrl/Cmd+K or "/". The copilot always sees the screen, so "this aircraft" means the one you are looking at.
 */
export default function AskBar() {
  const navigate = useNavigate();
  const loc = useLocation();
  const { role } = useRole();
  const ask = useAsk();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [turn, setTurn] = useState<{ q: string; a?: ChatResponse; err?: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const fleet = usePoll<FleetOverview>("/fleet", 20000); // kept warm, so results are there the moment the bar opens
  const orders = usePoll<WorkOrder[]>("/workorders", 20000);
  const screen = collectScreen(loc.pathname, loc.search, role.label);

  const close = () => {
    setOpen(false);
    setQ("");
    setTurn(null);
    setActive(0);
  };
  const send = (text: string) => {
    const t = text.trim();
    if (!t) return;
    setTurn({ q: t });
    setQ("");
    ask(t)
      .then((a) => setTurn((cur) => (cur?.q === t ? { q: t, a } : cur)))
      .catch((e) => setTurn((cur) => (cur?.q === t ? { q: t, err: String((e as Error).message) } : cur)));
  };

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName);
      if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") || (e.key === "/" && !typing)) {
        e.preventDefault();
        setOpen(true);
        setTimeout(() => input.current?.focus(), 0);
      } else if (e.key === "Escape") close();
    };
    const click = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) close();
    };
    const external = (e: Event) => {
      const d = (e as CustomEvent<{ text?: string; send?: boolean }>).detail ?? {};
      setOpen(true);
      if (d.text && d.send) send(d.text);
      else if (d.text) setQ(d.text);
      setTimeout(() => input.current?.focus(), 0);
    };
    window.addEventListener("keydown", key);
    window.addEventListener("mousedown", click);
    window.addEventListener("aerotwin:open-ask", external);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("mousedown", click);
      window.removeEventListener("aerotwin:open-ask", external);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const items: Item[] = useMemo(() => {
    const t = q.trim().toLowerCase();
    const go = (to: string) => () => {
      navigate(to);
      close();
    };
    const out: Item[] = [];
    if (!t) {
      for (const s of suggestionsFor(screen.page, screen.tail)) out.push({ key: "s:" + s, label: s, hint: "Ask", run: () => send(s), ask: true });
      out.push({
        key: "agents",
        label: "Run the agents on the current situation",
        hint: "Action",
        run: () => {
          navigate("/copilot?tab=agents&run=1");
          close();
        },
      });
      return out;
    }
    const aircraft = (fleet.data?.aircraft ?? [])
      .filter((a) => `${a.tail} ${a.type} ${a.location} ${a.status}`.toLowerCase().includes(t))
      .slice(0, 5)
      .map<Item>((a) => ({ key: "a:" + a.tail, label: `${a.tail}`, hint: `${a.type} · ${a.status.replace("_", " ")}${a.worst_component ? " · " + niceComponent(a.worst_component) : ""}`, run: go(`/aircraft/${a.tail}`) }));
    const pages = [...new Map(ROLES.flatMap((r) => r.nav).map((n) => [n.to === "/aircraft/SIM-017" ? "/aircraft/SIM-017" : n.to, n])).values()]
      .filter((n) => n.label.toLowerCase().includes(t))
      .slice(0, 3)
      .map<Item>((n) => ({ key: "p:" + n.to, label: n.label, hint: "Go to page", run: go(n.to) }));
    const wos = (orders.data ?? [])
      .filter((w) => `${w.id} ${w.tail} ${w.station} ${w.description}`.toLowerCase().includes(t))
      .slice(0, 3)
      .map<Item>((w) => ({ key: "w:" + w.id, label: `${w.id} · ${w.tail} at ${w.station}`, hint: w.status.replace("_", " "), run: go("/workorders") }));
    const askItem: Item = { key: "ask", label: `Ask: ${q.trim()}`, hint: "Ask", run: () => send(q), ask: true };
    const nav = [...aircraft, ...pages, ...wos];
    return looksLikeQuestion(q) || nav.length === 0 ? [askItem, ...nav] : [...nav, askItem];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, fleet.data, orders.data, screen.page, screen.tail]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(items.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      (items[active] ?? items[0])?.run();
    }
  };

  return (
    <div className="askbar" ref={box}>
      <button
        className="askbar-trigger"
        onClick={() => {
          setOpen(true);
          setTimeout(() => input.current?.focus(), 0);
        }}
        aria-label="Ask or search"
      >
        <Icon name="spark" size={15} />
        <span>Ask or search</span>
        <kbd>Ctrl K</kbd>
      </button>
      {open && (
        <div className="askbar-panel" role="dialog" aria-label="Ask or search">
          <div className="askbar-input">
            <Icon name="spark" size={16} />
            <input
              ref={input}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setActive(0);
                if (turn) setTurn(null);
              }}
              onKeyDown={onKey}
              placeholder={screen.tail ? `Ask about ${screen.tail}, or search aircraft, pages, work orders` : "Ask a question, or search aircraft, pages, work orders"}
              aria-label="Ask or search"
            />
            <button className="askbar-close" onClick={close} aria-label="Close">
              Esc
            </button>
          </div>
          {turn ? (
            <div className="askbar-answer">
              <div className="ask-q">
                <Icon name="spark" size={14} />
                <span>{turn.q}</span>
              </div>
              {turn.a ? <AnswerView resp={turn.a} /> : turn.err ? <p className="ask-missing">{turn.err}</p> : <p className="ask-thinking">Looking at the data…</p>}
            </div>
          ) : (
            <ul className="askbar-list">
              {!q.trim() && <li className="askbar-label">{screen.tail ? `On ${screen.tail}` : `On ${screen.page}`}</li>}
              {items.map((it, i) => (
                <li key={it.key}>
                  <button className={i === active ? "on" : ""} onMouseEnter={() => setActive(i)} onClick={it.run}>
                    {it.ask && <Icon name="spark" size={14} />}
                    <span>{it.label}</span>
                    {it.hint && <small>{it.hint}</small>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="askbar-foot">
            <span>The AI sees this screen: {screen.page}{screen.tail ? `, ${screen.tail}` : ""}</span>
            <span>Enter to select · Esc to close</span>
          </div>
        </div>
      )}
    </div>
  );
}
