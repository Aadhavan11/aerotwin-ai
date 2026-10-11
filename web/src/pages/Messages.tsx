import { useEffect, useState } from "react";
import { get, post, usePoll } from "../api/client";
import { Btn, Card, ErrorBox } from "../components/ui";
import { fmtZ } from "../lib/format";
import { notifyMessages } from "../lib/messages";

type Msg = {
  id: number; from: string; from_username: string; from_role: string; to: string; to_kind: string; to_value: string; subject: string; body: string; created: string;
  read?: boolean; recipients?: number; read_by?: number;
};
type Recipients = { roles: { id: string; label: string }[]; users: { username: string; name: string; role: string }[]; can_broadcast: boolean };

/** Write a message to a position, a person or (administrators) everyone. It lands in their inbox, unread, and their bell lights up. */
export function Compose({ onSent, presetRole }: { onSent?: (m: Msg) => void; presetRole?: string }) {
  const [rec, setRec] = useState<Recipients | null>(null);
  const [to, setTo] = useState(presetRole ? `role:${presetRole}` : "role:ops");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  useEffect(() => {
    get<Recipients>("/messages/recipients").then(({ data }) => setRec(data)).catch(() => undefined);
  }, []);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    setDone("");
    const [kind, ...rest] = to.split(":");
    try {
      const { data } = await post<Msg>("/messages", { to_kind: kind, to_value: rest.join(":"), subject, body });
      setSubject("");
      setBody("");
      setDone(`Sent to ${data.to}. It is unread in ${data.recipients === 1 ? "their" : `each of the ${data.recipients} inboxes`} until opened.`);
      notifyMessages();
      onSent?.(data);
    } catch (x) {
      setErr(String((x as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"error":"[a-z_]+","detail":"|"\}$/g, ""));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="compose" onSubmit={send}>
      <label className="mreq-field">
        To
        <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Who is this message for">
          {rec?.can_broadcast && <option value="all:">Everyone</option>}
          <optgroup label="A position">
            {(rec?.roles ?? []).map((r) => (
              <option key={r.id} value={`role:${r.id}`}>
                Everyone in {r.label}
              </option>
            ))}
          </optgroup>
          <optgroup label="One person">
            {(rec?.users ?? []).map((u) => (
              <option key={u.username} value={`user:${u.username}`}>
                {u.name} (@{u.username})
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      <label className="mreq-field">
        Subject
        <input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={120} required placeholder="What it is about" />
      </label>
      <label className="mreq-field">
        Message
        <textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} maxLength={2000} required placeholder="Write the message" />
      </label>
      {err && <p className="mreq-error" role="alert">{err}</p>}
      {done && <p className="compose-done" role="status">{done}</p>}
      <div>
        <Btn tone="primary" disabled={busy}>{busy ? "Sending…" : "Send message"}</Btn>
      </div>
    </form>
  );
}

/** Recent messages the signed-in person sent, and how many recipients have read each. */
export function SentList({ limit = 5 }: { limit?: number }) {
  const sent = usePoll<Msg[]>("/messages?box=sent", 8000);
  useEffect(() => {
    window.addEventListener("aerotwin:messages", sent.reload);
    return () => window.removeEventListener("aerotwin:messages", sent.reload);
  }, [sent.reload]);
  const rows = (sent.data ?? []).slice(0, limit);
  if (rows.length === 0) return <p className="text-sm text-slate-500">Nothing sent yet.</p>;
  return (
    <ul className="sent-list">
      {rows.map((m) => (
        <li key={m.id}>
          <b>{m.subject}</b>
          <span>
            to {m.to} · {fmtZ(m.created)} · read by {m.read_by} of {m.recipients}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function Messages() {
  const [tab, setTab] = useState<"inbox" | "sent" | "new">("inbox");
  const inbox = usePoll<Msg[]>("/messages?box=inbox", 8000);
  const sent = usePoll<Msg[]>(tab === "sent" ? "/messages?box=sent" : null, 8000);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    window.addEventListener("aerotwin:messages", inbox.reload);
    return () => window.removeEventListener("aerotwin:messages", inbox.reload);
  }, [inbox.reload]);
  const rows = (tab === "sent" ? sent.data : inbox.data) ?? [];
  const unread = (inbox.data ?? []).filter((m) => !m.read && m.from_username !== undefined).length;
  const m = rows.find((x) => x.id === open) ?? null;
  const select = async (x: Msg) => {
    setOpen(x.id);
    if (tab === "inbox" && !x.read) {
      try {
        await post(`/messages/${x.id}/read`, {});
      } catch {
        /* the badge corrects itself at the next poll */
      }
      inbox.reload();
      notifyMessages();
    }
  };
  const readAll = async () => {
    await post("/messages/read-all", {});
    inbox.reload();
    notifyMessages();
  };
  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Messages</div>
          <h1>Messages</h1>
          <p>Notes between positions. A message is unread for each person until they open it; the bell in the top bar shows how many are waiting.</p>
        </div>
      </div>
      <ErrorBox error={inbox.error} />
      <div className="seg" role="tablist" aria-label="Messages">
        <button role="tab" aria-selected={tab === "inbox"} className={tab === "inbox" ? "on" : ""} onClick={() => (setTab("inbox"), setOpen(null))}>
          Inbox{unread > 0 && <i>{unread}</i>}
        </button>
        <button role="tab" aria-selected={tab === "sent"} className={tab === "sent" ? "on" : ""} onClick={() => (setTab("sent"), setOpen(null))}>
          Sent
        </button>
        <button role="tab" aria-selected={tab === "new"} className={tab === "new" ? "on" : ""} onClick={() => setTab("new")}>
          Write a message
        </button>
      </div>
      {tab === "new" ? (
        <Card title="New message">
          <Compose onSent={() => undefined} />
        </Card>
      ) : (
        <div className="msgs">
          <Card title={tab === "inbox" ? "Inbox" : "Sent"} right={tab === "inbox" && unread > 0 ? <Btn tone="ghost" onClick={readAll}>Mark all as read</Btn> : undefined}>
            {rows.length === 0 ? (
              <p className="text-sm text-slate-500">{tab === "inbox" ? "No messages." : "You have not sent anything."}</p>
            ) : (
              <ul className="inbox">
                {rows.map((x) => (
                  <li key={x.id}>
                    <button className={`inbox-row ${x.id === open ? "on" : ""} ${tab === "inbox" && !x.read ? "unread-row" : ""}`} onClick={() => select(x)}>
                      <span className={`inbox-dot ${tab === "inbox" && !x.read ? "unread" : ""}`} aria-label={tab === "inbox" && !x.read ? "unread" : "read"} />
                      <span className="min-w-0">
                        <b>{tab === "sent" ? `To ${x.to}` : x.from}</b>
                        <span className="block truncate text-slate-700">{x.subject}</span>
                      </span>
                      <small>{fmtZ(x.created)}</small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={m ? m.subject : "Message"} right={m ? <span className="text-xs text-slate-500">{tab === "sent" ? `to ${m.to}` : `from ${m.from} · ${m.from_role}`} · {fmtZ(m.created)}</span> : undefined}>
            {m ? (
              <>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{m.body}</p>
                {tab === "sent" && <p className="mt-4 text-xs text-slate-500">Read by {m.read_by} of {m.recipients}.</p>}
              </>
            ) : (
              <p className="text-sm text-slate-500">Choose a message to read it. Opening it marks it as read.</p>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
