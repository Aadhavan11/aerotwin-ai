import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ROLES, useRole } from "../lib/role";

/** Who is signed in, their position, and sign out. The master account can also view the app as any position. */
export default function AccountMenu() {
  const { user, logout } = useAuth();
  const { role, setRole } = useRole();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", away);
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("mousedown", away);
      window.removeEventListener("keydown", esc);
    };
  }, []);
  if (!user) return null;
  const initial = (user.name || user.username).slice(0, 1).toUpperCase();
  return (
    <div className="account" ref={box}>
      <button className="account-btn" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} title={`${user.name} · ${role.label}`}>
        <span className="avatar">{user.picture ? <img src={user.picture} alt="" referrerPolicy="no-referrer" /> : initial}</span>
        <span className="account-role">{user.master ? `${role.label} · master` : role.label}</span>
      </button>
      {open && (
        <div className="account-menu" role="menu">
          <div className="account-who">
            <b>{user.name}</b>
            <span>
              @{user.username}
              {user.email ? ` · ${user.email}` : ""}
            </span>
          </div>
          {user.master ? (
            <>
              <div className="account-label">Master account: view the app as</div>
              {ROLES.map((r) => (
                <button
                  key={r.id}
                  role="menuitemradio"
                  aria-checked={r.id === role.id}
                  className={r.id === role.id ? "on" : ""}
                  onClick={() => {
                    setRole(r.id);
                    navigate(r.home);
                    setOpen(false);
                  }}
                >
                  <span>{r.label}</span>
                  {r.id === role.id && <i>✓</i>}
                </button>
              ))}
            </>
          ) : (
            <div className="account-pos">
              <div className="account-label">Position</div>
              <b>{role.label}</b>
              <span>{role.blurb}</span>
            </div>
          )}
          <button
            role="menuitem"
            className="account-out"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
