# Sensitivity and uncertainty analysis (v2 simulator)

Harness: `app/sim/v2/sensitivity.py`, CLI `python -m scripts.run_sensitivity [--quick] [--out out/sensitivity] [--max-params N] [--include-anchors]`.
Output (git-ignored `out/`): `sensitivity.json`, `SUMMARY.md`, one `tornado_<output>.svg` per headline output.

## What is perturbed

`perturbed(factors)` is a context manager that multiplies entries of the live module-level tables and restores them in a
`finally` block (exact restoration is tested by hash). It does not touch model code. It mutates module globals, so it is
NOT thread-safe; parallel runs use forked processes (each with its own copy).

Candidates are discovered from the live tables: every positive float in `app/sim/v2/models/*.PARAMS` (id `<module>.<key>`),
`INFANT` (`infant.<kind>.M|tau`), `FALSE_CALL` (`false_call.<kind>.rate|p_call`, probabilities clipped at 1) and
`POLICY` (`policy.<kind>.wear_below|check_days`). Integers are treated as structural and skipped. Default factor range is
[0.7, 1.3]; if `app/sim/v2/params_registry.py` exists and exposes `bounds(id)` returning `(low_factor, high_factor)` it is used
(imported lazily), and a `bounds` callable can be passed explicitly.

### Anchors are excluded by default

Each registry entry (`app/sim/v2/params_registry.py`, `docs/PARAMETERS.md`) carries a `role` (`rate`, `shape`, `threshold`,
`anchor`, `structural`) and `perturb`. `anchor` entries define the healthy/limit/nominal/reference scale of a signal, a unit
or time conversion or a physical coordinate; `structural` entries are counts or mixing structure. They have
`perturb = false` and are dropped from tornado, Morris and envelope by default, because scaling them re-labels outputs
instead of testing a belief. Pass `--include-anchors` (or `run_all(include_anchors=True)`) to perturb them anyway. The
harness ids map to registry ids (`engine.hot_per_c` -> `v2.models.engine.PARAMS[hot_per_c]`, `infant.<kind>.M` ->
`v2.world.INFANT[<kind>][0]`, `policy.<kind>.<field>` -> `v2.world.POLICY[<kind>].<field>`). If the registry cannot be
imported nothing is excluded and the output carries `registry unavailable: anchors may be included`.

JSON additions (schema string unchanged, keys additive): top-level `excluded_anchor_count`, `registry_used`, `warnings`;
`config.include_anchors`, `config.excluded_anchor_count`, `config.registry_used`.

A parameter that a model reads once at import (a derived module constant) would show no effect: it will be flagged as
indistinguishable from seed noise, which is a prompt to check, not proof of irrelevance.

## Outputs per run

Counts are scaled to 24 tails and 1 year: unscheduled removals per kind (`removals.<kind>`, incl. NFF returns), MTBUR in
flight hours per kind (`mtbur_fh.<kind>`, censored at total exposure when there are zero removals), `removals.total`,
`nff_share`, `engine_shop_visits` (engine overhauls, planned or not), `deferrals` (MEL items opened). No cost proxy is computed.

## Methods

1. Tornado (one at a time): each parameter low and high, same seeds (common random numbers, so differences are paired).
   Effect = mean over seeds of (high - low); `se` from the paired differences. Flagged
   `indistinguishable_from_seed_noise` when |effect| <= 2 se or below baseline seed sd / sqrt(n seeds).
2. Morris elementary effects over parameter GROUPS (each module, plus infant / false_call / policy; all members of a group
   share one multiplier). 4-level grid, r random trajectories, fixed sim seed. mu* ranks overall influence; sigma large
   relative to mu* means interaction or nonlinearity. Units: output change per full low-to-high sweep.
3. Envelope: N Latin-hypercube joint perturbations of all parameters; p5/p50/p95 per output.

## How to read the tornado

Bars are `high - low` of the output; red/blue = raising the parameter increases/decreases it; grey = indistinguishable from
seed noise (do not rank those). Compare bar size with `baseline_seed_sd` in the JSON.

## Runtime

Single run cost is dominated by the cycle loop (about 20 s for 24 tails x 1 yr on a laptop, more under load, plus 1-4 s of
initial-wear build). Quick mode (3 tails, 0.2 yr, 2 seeds, 6 params, 4 workers) takes well under 60 s. Full mode (24 tails,
1 yr, 3 seeds, 48 tornado parameters chosen round-robin over groups, Morris r=4 over 12 groups, envelope N=30 over all
parameters) is a roughly 400-run job: tens of minutes with 4 workers. Use `--max-params` to change the tornado size; all
discovered parameters would take hours.

## Caveats (read before quoting any number)

- Assumption-driven. The +/-30% ranges are generic, not calibrated uncertainty; the envelope shows how wide the output is
  under those assumptions, it is not a confidence interval.
- Seed noise: removal counts per kind are small integers; 2-3 seeds cannot resolve small effects, and the one-seed
  Morris/envelope runs inherit that noise.
- Parameters are perturbed independently of any physical coupling between them (e.g. a ground-truth fit); joint draws can be
  physically inconsistent.
- Perturbing priors does not test structural model error.
- Anchor parameters: scaling a reference or healthy-state value (for example `electrical.capacity_healthy`,
  `landing_gear.P_NOM_KPA`, `*_limit`) shifts the health-index normalisation rather than representing physical uncertainty,
  so these produce huge "effects". They are excluded by default (see above); results run with `--include-anchors` are
  scale artifacts and should not be ranked against rates and shape parameters.

## Observed example (not a result to quote; run before anchors were excluded by default)

12 tails, 1 yr, seeds 1 and 7, 27 parameters (round-robin), Morris r=2, envelope N=6; 605 s on a busy 4-worker machine
(~8.8 s per run). Top influences by |high - low|: removals.avionics: avionics.beta, infant.avionics.M, brakes.K_APP_MM;
removals.tires: tires.TREAD_NEW_MM, tires.TREAD_LIMIT_MM, brakes.E_REF_MJ; removals.apu: apu.healthy_margin_c,
apu.start_loss_c; removals.ecs: ecs.fouling_inf; deferrals: apu.healthy_margin_c, avionics.eta_cycles; nff_share:
anchor parameters dominate. Many 27-parameter subsets contain only one parameter per module, so most effects are zero
because the relevant parameters were not sampled; raise `--max-params` for a real ranking.
