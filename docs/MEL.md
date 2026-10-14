# Simplified fictional deferral register

This register is **NOT an MMEL/MEL**, is not regulatory guidance, and must not be
used for real aircraft dispatch or maintenance. The catalogue and extension rules
are fictional demo examples. All register payloads include `simulated: true`;
each item in a list also carries this flag.

## Categories and time boundaries

The register uses `app.copilot.persist.sim_now()`, the same simulated clock as
work orders. Durations are whole day intervals from the precise deferral timestamp,
without rounding to midnight or excluding the deferral day:

| Category | Original interval | Extension |
| --- | --- | --- |
| A | Explicit positive integer `limit_days`, required | Forbidden |
| B | 3 days | +1 day, once |
| C | 10 days | +5 days, once |
| D | 120 days | Forbidden |

`expires_at = deferred_at + N days`. An item is open one second before this
deadline and expires when `now >= expires_at`. Reads and `refresh_statuses(now)`
persist expiry. Expired items never reopen if the clock moves backward.

An open B or C item may receive one extension with a non-empty justification.
The extension adds days to its existing deadline, never to the time of the request,
and must not exceed `deferred_at + 2 * original_interval`. Category A's limit is
explicit, following the simulator's per-limit semantics; it has no default interval.
Closing an open item sets `status: rectified` and `rectified_at` to simulated time.
Rectified and expired items cannot be closed or extended. Closing an expired item
is rejected.

## Fictional catalogue

Keys are `(component_kind, fault_mode)`, normalized by stripping whitespace and
lowercasing. ATA chapter/section references reuse `app.taxonomy.ata.ata_for`.
Unknown keys and category overrides that disagree with a template are rejected.

| Key | ATA | Category | Title / restriction |
| --- | --- | --- | --- |
| `apu`, `failed-to-start` | 49 / 49-40 | B | APU inoperative: ground power unit required at turn |
| `ecs pack 1`, `fault` | 21 / 21-50 | C | ECS pack 1 inoperative; restriction: ECS pack 1 inoperative: max FL 310 |
| `anti-skid`, `fault` | 32 / 32-42 | C | Anti-skid inoperative; restriction: Anti-skid inoperative: contaminated-runway limits |
| `avionics`, `fault` | 34 / 34-00 | A | Navigation display inoperative; restriction: alternate display required |
| `electrical`, `fault` | 24 / 24-20 | C | Generator inoperative; restriction: reduced electrical load, no ETOPS |
| `tires`, `fault` | 32 / 32-41 | D | Tire pressure monitor inoperative; restriction: manual checks each transit |

`open_restrictions(tail)` refreshes expiry first and returns the full restriction
texts of all currently open items for that tail.

## Routes and access

The router is mounted automatically by `app.insights.router`.

| Method | Route | Input / result |
| --- | --- | --- |
| POST | `/ops/mel` | Create body below; returns an item |
| GET | `/ops/mel?tail=SIM-001&status=open` | Optional tail/status filters; returns `{simulated: true, items: [...]}` |
| GET | `/ops/mel/summary` | Returns open `by_category` counts (including zeroes), `expiring_within_72h`, and sorted unique `aircraft_with_open_restrictions` |
| GET | `/ops/mel/{id}` | Returns one item after expiry refresh |
| POST | `/ops/mel/{id}/close` | No body required; returns the rectified item |
| POST | `/ops/mel/{id}/extend` | `{ "justification": "Awaiting fictional parts" }`; returns the extended item |

Example create body:

```json
{
  "tail": "SIM-001",
  "component_kind": "apu",
  "fault_mode": "failed-to-start",
  "category": "B",
  "reason": "Fictional starter defect",
  "notes": "Demo only"
}
```

`category` is optional but must match the template when supplied. `limit_days` is
required for A. The summary counts open items with `now < expires_at <= now + 72h`.
All items include the fields `id`, `tail`, `ata_chapter`, `ata_section`, `title`,
`category`, `reason`, `deferred_at`, `expires_at`, `limit_days`, `status`,
`restrictions`, `rectified_at`, `deferred_by`, `notes`, `extensions_used`, and
`simulated`.

With the shared sign-in gate enabled (`AUTH_REQUIRED=1`), creates, closes and
extensions require ops, maintenance or admin. Any signed-in role may read.
The authenticated principal's display name supplies `deferred_by` and the audit
actor; callers cannot override it in the body. In the repository's local mode
with authentication disabled, the actor is `local-user`. Unauthenticated requests
receive the existing gate's 401 response and forbidden roles its 403 response.
These shared authentication denials retain the application's standard envelope.

Register errors include `simulated: true`: 404 for a missing item, 409 for a
forbidden transition or extension, and 422 for invalid input.

## Persistence and audit

Items always persist in the application's SQLite database (`settings.DB_PATH`),
including mock mode. The additive `mel_items` table is created with
`CREATE TABLE IF NOT EXISTS` and has `seq` (SQLite AUTOINCREMENT primary key),
`id`, `status`, and `json` columns. IDs are sequential per database, formatted
`MEL-0001`, `MEL-0002`, and so on, with a minimum of four digits. Opening a fresh
app preserves rows and the sequence. Scenario reset clears the rows through
`app.copilot.reset_all()` and retains the sequence; rebuilding the entire database
starts a new sequence. No destructive migration is needed.

Every successful create, close, and extend calls `app.copilot.audit.record`, with
actions `mel_created`, `mel_closed`, and `mel_extended`. Extension justification
is recorded in the audit detail. Audit storage follows the existing copilot
persistence configuration (SQLite in database mode, memory otherwise).
