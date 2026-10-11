import { useEffect, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import type { RoleId } from "../lib/role";

declare global {
  interface Window {
    google?: any;
  }
}

/** Position cards: used when creating an account and after a first Google sign-in. */
function Positions({ value, onChange }: { value: RoleId | ""; onChange: (r: RoleId) => void }) {
  const { config } = useAuth();
  return (
    <div className="position-grid" role="radiogroup" aria-label="Your position">
      {(config?.roles ?? []).map((r) => (
        <button type="button" key={r.id} role="radio" aria-checked={value === r.id} className={value === r.id ? "on" : ""} onClick={() => onChange(r.id)}>
          <b>{r.label}</b>
          <span>{r.blurb}</span>
        </button>
      ))}
    </div>
  );
}

/** Google's own button, drawn by Google's script. It only appears when the server has a client id and Google accepts this site. */
function GoogleButton({ clientId, onCredential }: { clientId: string; onCredential: (c: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState<"loading" | "ok" | "unavailable">("loading");
  useEffect(() => {
    let dead = false;
    const draw = () => {
      const g = window.google?.accounts?.id;
      if (dead || !g || !host.current) return;
      g.initialize({ client_id: clientId, callback: (r: { credential: string }) => onCredential(r.credential), use_fedcm_for_prompt: true });
      g.renderButton(host.current, { theme: "filled_black", size: "large", shape: "pill", text: "continue_with", width: 340 });
      // Google draws nothing when it refuses this site (the origin is not registered for the client id): say so instead of showing a hole.
      setTimeout(() => {
        const f = host.current?.querySelector("iframe");
        if (!dead) setReady(f && f.getBoundingClientRect().width > 20 ? "ok" : "unavailable");
      }, 3000);
    };
    if (window.google?.accounts?.id) draw();
    else {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true;
      s.onload = draw;
      s.onerror = () => !dead && setReady("unavailable");
      document.head.appendChild(s);
    }
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);
  return (
    <div className="google-slot">
      <div ref={host} style={{ display: ready === "unavailable" ? "none" : "flex" }} />
      {ready === "unavailable" && <p className="login-note">Google sign-in is not available on this site right now. Use a username below.</p>}
    </div>
  );
}

export default function Login() {
  const { config, login, signup, google } = useAuth();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<RoleId | "">("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ credential: string; email: string; name: string } | null>(null);

  const run = async (fn: () => Promise<{ ok: boolean; error?: string; needsRole?: { email: string; name: string } }>, credential?: string) => {
    setBusy(true);
    setError("");
    const r = await fn();
    setBusy(false);
    if (!r.ok) return setError(r.error ?? "Sign-in failed.");
    if (r.needsRole && credential) setPending({ credential, ...r.needsRole });
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "up" && !role) return setError("Choose your position first.");
    void run(() => (mode === "in" ? login(username, password) : signup({ username, password, name, role: role as RoleId })));
  };

  const demo = (u: string) => config?.demo_password && void run(() => login(u, config.demo_password!));

  return (
    <div className="login">
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-mark">AT</span>
          <span className="brand-name">AeroTwin</span>
        </div>

        {pending ? (
          <>
            <h1>Welcome, {pending.name.split(" ")[0]}</h1>
            <p className="login-sub">Choose your position. It decides your home screen and what you can approve.</p>
            <Positions value={role} onChange={setRole} />
            {error && <p className="login-error">{error}</p>}
            <button className="btn-white wide" disabled={!role || busy} onClick={() => void run(() => google(pending.credential, role as RoleId))}>
              {busy ? "Signing in…" : "Continue"}
            </button>
            <button className="login-link" onClick={() => setPending(null)}>
              Back
            </button>
          </>
        ) : (
          <>
            <h1>{mode === "in" ? "Sign in to AeroTwin" : "Create your account"}</h1>
            <p className="login-sub">Airline maintenance planning on a digital twin of the fleet. Everything inside is simulated.</p>

            <div className="seg login-seg" role="tablist">
              <button role="tab" aria-selected={mode === "in"} className={mode === "in" ? "on" : ""} onClick={() => (setMode("in"), setError(""))}>
                Sign in
              </button>
              <button role="tab" aria-selected={mode === "up"} className={mode === "up" ? "on" : ""} onClick={() => (setMode("up"), setError(""))}>
                Create account
              </button>
            </div>

            {config?.google_client_id && (
              <>
                <GoogleButton clientId={config.google_client_id} onCredential={(c) => void run(() => google(c), c)} />
                <div className="login-or">
                  <span>or</span>
                </div>
              </>
            )}

            <form onSubmit={submit} className="login-form">
              {mode === "up" && (
                <label>
                  Name
                  <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Your name" />
                </label>
              )}
              <label>
                Username
                <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder={mode === "in" ? "e.g. operations" : "3 to 24 letters or numbers"} required />
              </label>
              <label>
                Password
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "in" ? "current-password" : "new-password"} placeholder={mode === "up" ? "At least 8 characters" : ""} required />
              </label>
              {mode === "up" && (
                <div>
                  <div className="login-label">Your position</div>
                  <Positions value={role} onChange={setRole} />
                </div>
              )}
              {error && (
                <p className="login-error" role="alert">
                  {error}
                </p>
              )}
              <button className="btn-white wide" type="submit" disabled={busy}>
                {busy ? "One moment…" : mode === "in" ? "Sign in" : "Create account and sign in"}
              </button>
            </form>

            {mode === "in" && (config?.demo_accounts.length ?? 0) > 0 && (
              <div className="demo-accounts">
                <div className="login-label">Try a position with a demo account</div>
                <div className="demo-list">
                  {config!.demo_accounts.map((a) => (
                    <button key={a.username} onClick={() => demo(a.username)} disabled={busy} title={`Sign in as ${a.username}`}>
                      <b>{a.label}</b>
                      <span>{a.username}</span>
                    </button>
                  ))}
                </div>
                <p className="login-note">
                  One click signs you in. These accounts share the password <code>{config!.demo_password}</code>, and the master account <code>admin</code> can switch between all positions.
                </p>
              </div>
            )}
          </>
        )}
      </div>
      <p className="login-foot">Advisory prototype on simulated data. Not certified for operational use.</p>
    </div>
  );
}
