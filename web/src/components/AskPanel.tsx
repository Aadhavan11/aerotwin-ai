import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { ChatResponse, EvidenceRef } from "../api/types";
import { useAsk } from "../lib/ask";
import Icon from "./Icon";
import WorkOrderCard from "./WorkOrderCard";

/** Where an evidence reference leads, if anywhere. */
function evidenceLink(e: EvidenceRef): string | null {
  if (e.kind === "health") {
    const tail = e.ref.split("/")[0];
    return /^SIM-\d+$/i.test(tail) ? `/aircraft/${tail.toUpperCase()}` : null;
  }
  return e.kind === "plan" ? "/planning" : null;
}
const evidenceText = (e: EvidenceRef): string => {
  if (e.kind === "event" && e.ref === "screen") return "your screen";
  if (e.kind === "health") return e.ref.split("/").pop()?.replace(/_/g, " ") ?? e.ref;
  if (e.kind === "plan") return `plan ${e.ref}`;
  if (e.kind === "tool_call") return "planner data";
  return `${e.kind} ${e.ref}`;
};

/** An answer shown as plain text with what it is based on (links where there is somewhere to go). Not a chat bubble. */
export function AnswerView({ resp }: { resp: ChatResponse }) {
  const based = resp.evidence.filter((e, i, all) => all.findIndex((x) => evidenceText(x) === evidenceText(e)) === i);
  return (
    <div className="ask-answer">
      {resp.answer.split("\n").filter((l) => l.trim()).map((l, i) => (
        <p key={i}>{l}</p>
      ))}
      {based.length > 0 && (
        <div className="ask-based">
          <span>Based on</span>
          {based.map((e, i) => {
            const to = evidenceLink(e);
            return to ? (
              <Link key={i} to={to} title={e.note}>
                {evidenceText(e)}
              </Link>
            ) : (
              <em key={i} title={e.note}>
                {evidenceText(e)}
              </em>
            );
          })}
        </div>
      )}
      {resp.missing_info.length > 0 && <p className="ask-missing">Missing: {resp.missing_info.join("; ")}</p>}
      {resp.proposed_work_orders.length > 0 && (
        <div className="mt-3 space-y-2">
          {resp.proposed_work_orders.map((w) => (
            <WorkOrderCard key={w.id} w={w} onDone={() => undefined} />
          ))}
        </div>
      )}
    </div>
  );
}

type Turn = { q: string; a?: ChatResponse; err?: string };

/** The answer to one question, fetched when it appears. Used by the registry search and anywhere a question is already known. */
export function Answer({ question, onClear }: { question: string; onClear?: () => void }) {
  const ask = useAsk();
  const [turn, setTurn] = useState<Turn>({ q: question });
  useEffect(() => {
    let alive = true;
    setTurn({ q: question });
    ask(question)
      .then((a) => alive && setTurn({ q: question, a }))
      .catch((e) => alive && setTurn({ q: question, err: String((e as Error).message) }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question]);
  return (
    <div className="ask-result">
      <div className="ask-q">
        <Icon name="spark" size={14} />
        <span>{question}</span>
        {onClear && (
          <button onClick={onClear} aria-label="Clear the answer">
            Clear
          </button>
        )}
      </div>
      {turn.a ? <AnswerView resp={turn.a} /> : turn.err ? <p className="ask-missing">{turn.err}</p> : <p className="ask-thinking">Looking at the data…</p>}
    </div>
  );
}

/**
 * An ask field placed where the question comes up. The screen travels with every question, so "this aircraft" and "why here" just work.
 * It shows one answer at a time, in place, as text.
 */
export default function AskPanel({ placeholder, suggestions = [], className = "" }: { placeholder: string; suggestions?: string[]; className?: string }) {
  const [text, setText] = useState("");
  const [question, setQuestion] = useState<string | null>(null);
  const go = (q: string) => {
    const t = q.trim();
    if (!t) return;
    setQuestion(null);
    setTimeout(() => setQuestion(t), 0); // re-asking the same words asks again
    setText("");
  };
  return (
    <div className={`ask-panel ${className}`}>
      <form
        className="ask-field"
        onSubmit={(e) => {
          e.preventDefault();
          go(text);
        }}
      >
        <Icon name="spark" size={16} />
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
        {text.trim() && <button type="submit">Ask</button>}
      </form>
      {question === null && suggestions.length > 0 && (
        <div className="ask-suggest">
          {suggestions.map((s) => (
            <button key={s} onClick={() => go(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      {question !== null && <Answer question={question} onClear={() => setQuestion(null)} />}
    </div>
  );
}
