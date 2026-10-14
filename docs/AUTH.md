# Sign-in, positions and the master account

Off by default: local runs, tests and the golden path behave exactly as before. The public demo turns it on with `AUTH_REQUIRED=1`
(see `docs/HACKLANTA-HOSTING.md` on the Pi for the exact settings). Code: `app/auth/` (server), `web/src/lib/auth.tsx`, `web/src/pages/Login.tsx`,
`web/src/components/AccountMenu.tsx` (browser). Tests: `tests/test_auth.py`.

## What sign-in does

* Every data endpoint needs a session. `/health` and `/auth/*` stay public.
* **Positions are enforced on the server**, not just hidden in the browser:
  * approving or rejecting a work order: operations control and maintenance planner only (`403` for anyone else);
  * changing the simulation (advance time, inject a fault, reset): operations control and administrator only;
  * the audit log records the signed-in person, whatever name the request body carries.
* Passwords are hashed with scrypt. Sessions are random tokens in an HttpOnly cookie (only the SHA-256 is stored). Login is rate limited per username and per address.
* Accounts live in their own SQLite file (`AUTH_DB`), so resetting the demo database never deletes them.

## The six built-in accounts

| Username | Position | Notes |
|---|---|---|
| `operations` | Operations control | approves work orders, drives the simulation |
| `maintenance` | Maintenance planner | approves work orders |
| `crew` | Pilot / flight crew | readiness brief, trips, duty, weather, defect report |
| `dispatch` | Crew & dispatch | departures, flight release, crew roster, weather, messages |
| `administrator` | Administrator | system health, audit, scenario controls |
| `admin` | **Master** | can view the app as any of the five positions (account menu, top right) |

The five position accounts share a demo password that the sign-in screen shows (`AUTH_DEMO_PASSWORD`; it is a public demo on simulated data).
The master account has **no password until one is set** through the key drop box `aerotwin-master-password` (file `AUTH_MASTER_PASSWORD_FILE`; the server
re-reads it when it changes). Google accounts listed in `AUTH_MASTER_EMAILS` become master automatically.

## Creating accounts

* **Create account**: name, username, password and a **position** (the five cards). The position is fixed after sign-up.
* **Continue with Google**: a first-time Google account is asked for its position, then signed in; a returning account goes straight in.
  Needs a Google OAuth *Web application* client with the authorized JavaScript origin `https://shamitbasu.com`. Its client ID (not secret) goes in through the key drop
  box `aerotwin-google-client-id` (file `AUTH_GOOGLE_CLIENT_ID_FILE`). Until then the Google button is simply not shown. The server verifies the Google token with
  Google's tokeninfo endpoint and checks audience, issuer, expiry and a verified email.

## Settings (environment)

`AUTH_REQUIRED`, `AUTH_DB`, `AUTH_DEMO_PASSWORD`, `AUTH_SHOW_DEMO` (default 1: list the demo accounts and their password on the sign-in screen),
`AUTH_MASTER_EMAILS`, `AUTH_MASTER_PASSWORD_FILE`, `AUTH_GOOGLE_CLIENT_ID_FILE` (or `GOOGLE_CLIENT_ID`), `AUTH_COOKIE_PATH` (`/hacklanta` on the public demo),
`AUTH_COOKIE_SECURE=1` behind HTTPS, `AUTH_SESSION_DAYS` (default 7).

## Public hosting

The public site (Render, see `DEPLOY.md`) sets these:

| Variable | Default | Public site | Meaning |
|---|---|---|---|
| `AUTH_REQUIRED` | off | `1` | Turn sign-in on |
| `AUTH_COOKIE_SECURE` | unset (on for HTTPS requests under `AUTH_REQUIRED`) | `1` | `1` forces secure session cookies (HTTPS only), `0` forces them off |
| `AUTH_SHOW_DEMO` | `1` | `0` | `0` hides the demo password on the sign-in screen |
| `AUTH_DEMO_PASSWORD` | `aerotwin-demo` | secret | Password of the five position accounts |
| `AUTH_ALLOW_SIGNUP` | off | `0` | `1` lets visitors create accounts (never as administrator) |
| `AUTH_MASTER_PASSWORD_FILE` | unset | `/etc/secrets/master_password` | File holding the master (`admin`) password |
| `TRUSTED_PROXY_HOPS` | `0` | `1` | Take the client address for rate limiting from the proxy header |
| `LLM_RATE_PER_MIN` | `6` | `6` | AI requests per minute per visitor |
| `LLM_DAILY_CAP` | `200` | `200` | AI requests per day overall |
| `ANTHROPIC_API_KEY` | unset | secret | Enables AI replies; unset runs scripted mode |
| `CORS_ORIGINS` | unset | empty | Extra allowed browser origins |

`TRUSTED_PROXY_HOPS` must stay `0` unless the service sits behind a proxy that sets the forwarded-address header. Otherwise a client can fake its address.

## Limits, said plainly

Prototype-grade: failed-login counters are in memory (reset on restart), there is no email verification for username sign-ups, no password reset and no
multi-factor sign-in. It is meant to show role-based access on a simulated system, not to protect real data.
