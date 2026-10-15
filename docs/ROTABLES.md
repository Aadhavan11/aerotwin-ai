# Serialised rotables (opt-in layer)

`World(seed, tails, rotables=True)` (or a `RotableConfig`) adds per-serial lineage to the simulator. Default is off: the
lumped world, the 5-seed removal bands and all state hashes are unchanged. `build_world({"rotables": True, ...})` and the
run spec pass the flag through the runner.

**Synthetic is not field validity.** Every number below is an assumed prior (registered with source type `assumed` in
`docs/PARAMETERS.md`, module `v2.rotables`). The layer exists so lineage analytics (bad actors, per-serial MTBUR, pooling,
ping-pong) can be exercised end to end; it does not describe any operator, part or shop.

## Design choice

The `Fleet` keeps one vectorised model state per installed position (slot). That state remains the physical-wear proxy of
whichever serial sits in the slot. Per-serial information lives in a lightweight record (`RotableUnit` plus the existing
`PartInstance` TSN/CSN/TSO/CSO). Why not extra Fleet slots for spares: it would change `Fleet.state_hash`, the config hash
and every vectorised array length (and the bands), and a shelf unit does not age. The cost of this choice: wear is not
carried inside a serial when it moves (a swapped-in unit shows the post-action slot state, as the lumped model did).

## Mechanism

1. Pools: per kind, `spares_per_station` zero-hour spares at each of 8 stations (default
   `ceil(0.15 * units / 8)`, at least 1). Initial installed units and spares get hidden attributes: quality factor
   (lognormal, mean 1), rogue flag, part-number variant (`PN-<KIND>-01` pre-mod, `-02` post-mod for modded kinds; others
   `none`).
2. Per-slot multiplier on `stress_ext` = quality x mod factor (pre 1.15, post 0.85, none 1.0), applied beside the infant term.
3. Physical removals are the `replace`, `overhaul` and `nff_return` actions. The serial goes to the shop: lognormal
   turnaround (kind median 7 to 60 days; NFF check x0.4), finding `confirmed` (failure) | `partial` (worn / degraded) |
   `nff` (false-call model output, or a rogue unit the shop cannot reproduce). Repaired serials get Kijima q on
   TSO/CSO (overhaul: the action's q, else 0.5); NFF serials return unchanged; 4% of confirmed failures are scrapped and a
   new unit is bought (30 day lead time). The serial returns to stock at the station where it was removed.
4. Replacement: nearest ready stock (ring distance over the 8 stations; ties by serial); AOG/ferry exposure
   0.25 day per hop. No ready stock: rush the earliest shop return (exposure capped at 5 days); no unit of the kind at
   all: emergency purchase. Exposure is booked in `pool.exposure`; it does not feed back into utilisation.
5. Rogue units (`ROGUE_PROB`, default 1% of serials of non-consumable kinds): after every install an early failure
   hazard `floor + h0 * exp(-age / tau)` per cycle sets the slot `failed`; the shop then reports NFF with probability
   0.7 and the unit returns still defective. The same serial keeps getting pulled from different tails (ping-pong).
6. `repair` and `inspect` stay on-wing actions: the serial does not move.

## Records and exports

* `Removal` gains `part_no`, `shop_finding`, `installed_at` (defaults keep lumped records identical).
  `PartInstance` gains `mod_status`, `shop_visits`.
* `World.rotables.install_log` (initial population + every swap), `shop_visits`, `exposure`, and helpers
  `removal_rows(world)` / `install_rows(world)` that match `app.ingest` `PartRemovalEvent` / `PartInstallEvent`
  (`partial` maps to `confirmed`; `nff` to `nff_suspected`). Runner exports add `lineage.json`.
* HIDDEN truth (quality, rogue, mod status) is only in `World.rotable_truth()` and `rotables_truth.json`; it is never in
  telemetry rows, removal or install records, or the reliability pack.

Invariants (tests/test_rotables.py): a serial is in at most one position; installs equal initial population plus physical
removals; removal precedes the next install; shop time is non-negative; determinism and snapshot/restore bit-identity;
the ingest continuity checks (double install, removal before install, overlapping position installs) pass.

## Enables

Bad-actor / repeat-removal detection per serial, per-serial and per-part-number MTBUR, NFF-by-serial analysis, mod-status
comparison, spares sizing against station stock and turnaround, AOG exposure studies.

## Limits

No per-serial physics, no cannibalisation, no pooling agreements, no warranty or core-exchange logic, no routing
(station distance is a ring proxy), no feedback of AOG time onto flying, rogue units are a single latent-defect class,
shop findings come from hidden state plus assumed probabilities. Rates are meant to be preserved, not recalibrated: see the
rotables band test (medians per kind within +/-25% or 2 removals of the lumped model).
