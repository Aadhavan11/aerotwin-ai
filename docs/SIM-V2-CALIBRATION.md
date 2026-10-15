# SIM-V2 calibration: hydraulics (UCI test rig) and battery (NASA cells)

Code: `app/calib/hydraulic.py`, `app/calib/battery.py`. Fits: `app/sim/v2/calibration/hydraulics_fit.json`,
`electrical_fit.json` (committed; the models load their `overrides` at import via `_apply_fit`, unfitted priors stay in
`PRIOR_PARAMS`). Regenerate: `python -m app.calib.hydraulic` / `python -m app.calib.battery`; add `--validate` for the
before/after tables. Data (git-ignored, public): `data/external/uci_hydraulic/` (ZeMA, CC BY 4.0) and
`data/external/nasa_battery_5/extracted/fy08q4/` (NASA PCoE; override dirs with `AEROTWIN_HYDRAULIC_DIR`,
`AEROTWIN_BATTERY_DIR`). Both are lab data, read as plain numbers only.

**Headline.** Neither dataset can calibrate failure *rates* or absolute *limits* of an aircraft system. What the data
did support: (hydraulics) sensor sensitivities, noise floors and which condition levels are detectable; (battery)
the shape of capacity fade, the coupling of resistance growth to fade, and reading scatter. Of 36 hydraulics and 36
electrical PARAMS, 0 and 3 respectively were changed.

## Decision rules (fixed before looking at results)
* Noise: lab instruments are a lower bound for fielded sensors, so `new = max(rig estimate, prior)`.
* Sensitivity: override only if the rig measures the same physical effect, the sign agrees with the model, and the
  uncertainty (cell-level) excludes the prior or zero as stated per parameter. Otherwise the prior stays and the
  JSON `decisions` block says why.
* Translation is relative (sd/mean, R/R0, pp of own initial capacity) wherever absolute units differ between rig and aircraft.

## 1. Hydraulics (UCI rig, 2205 cycles, 144 label cells, 1449 stable)
Each 60 s cycle is summarised by sensor means; per sensor, OLS on four label severities (0 healthy, 1 "close to total
failure": cooler (100-L)/97, valve (100-L)/27, leak L/2, accumulator (130-L)/40), stable cycles only, cell-bootstrap SEs.
Coefficient = change in the sensor's cycle mean at full severity of that label.

| sensor | cooler | valve | leak | accumulator | R2 | within-cell sd (pooled) | median cell sd | resolution |
|---|---|---|---|---|---|---|---|---|
| PS1 pressure bar | +1.7 | +0.1 (ns) | +3.0 | +3.3 | 0.12 | 2.89 | 0.016 | 0.01 |
| PS5 pressure bar | -1.26 | 0.00 (ns) | +0.006 (ns) | +0.012 (ns) | 0.89 | 0.005 | 0.004 | 0.001 |
| EPS1 power W | -64 | +2 (ns) | +72 | +38 | 0.32 | 38.8 | 1.9 | 0.2 |
| FS1 flow l/min | -1.12 | -0.02 (ns) | -0.93 | -0.62 | 0.31 | 0.51 | 0.005 | 0.001 |
| TS1 temp degC | +17.2 | -0.05 (ns) | -0.11 (ns) | +0.21 (ns) | 0.82 | 0.064 | 0.029 | 0.003 |
| TS4 temp degC | +17.6 | -0.02 (ns) | -0.01 (ns) | +0.30 (ns) | 0.83 | 0.077 | 0.039 | 0.003 |
| VS1 vibration mm/s | +0.129 | +0.007 (ns) | +0.018 | +0.008 | 0.73 | 0.016 | 0.005 | 0.001 |

(ns = bootstrap |t| < 2; all 14 sensors are in the JSON.) Detectability (best sensor, d' = |shift| / pooled within-cell sd,
threshold 3): cooler detectable at hi-equivalent 0.18 (20% efficiency) and 0 (d' 220-270, temperatures); pump leakage
only at "severe" (d' 4.2, TS2; weak leak d' 2.1); accumulator from 115 bar on (d' 3.6 at 115, 9.7 at 90, FS2); **valve degradation is not detectable from cycle-mean features at any
level (d' <= 0.9)**.

### Mapping to model parameters
| parameter | prior | rig evidence | decision |
|---|---|---|---|
| `pressure_noise` | 15 kPa | PS1 median within-cell sd 0.016 bar = 1.0e-4 relative -> 2.0 kPa at 20700 kPa | prior kept (rig below floor) |
| `temp_noise` | 1.5 degC | TS1-4 median within-cell sd 0.03 degC | prior kept |
| `heat_eta` | 4.0 degC | mean TS change per full pump leak -0.14 +/- 0.67 (t = -0.2) | prior kept (wrong sign, not significant) |
| `leak_weight`, `precharge_weight` | 1.0 | PS1 *rises* with leak (+1.8%, t 3.7) and low accumulator pressure (+2.1%, t 4.0) | prior kept: sign conflict with the model, where pressure falls with damage |
| case drain, fluid qty, filter dp | - | no corresponding rig sensor | prior kept |
| rates, `life_cycles`, failure Weibull | - | degradation is stepped by experiment design | not estimable |

Sensor resolutions (smallest recorded gap) are in the JSON; the model has no quantisation step, so they are not applied.

### Before/after (healthy cell = 10 stable cycles; sim = 400 fresh units / 400 units x 1500 flights, nominal context)
Fitted equals prior (no override fired), so before = after by construction:

| comparison | KS | Wasserstein / real sd | real sd | sim sd |
|---|---|---|---|---|
| healthy pressure, relative deviation | 0.49 | 8.6 | 6e-5 | 7.3e-4 |
| healthy fluid temperature, degC | 0.50 | 53 | 0.022 | 1.55 |
| all-cycle pressure vs own healthy mean | 0.71 | 1.9 | - | - |
| all-cycle temperature vs own healthy mean | 0.66 | 1.0 | - | - |

The simulated sensors are 12x (pressure) and 70x (temperature) noisier than the rig; this is intended (rig is a
lab bench) but is not a match. The all-cycle rows are poor because the rig's dominant degradation is a cooler fault
(+8.9 degC mean), which the model has no component for, and because the pressure-sign conflict above.

## 2. Battery (NASA B0005/B0006/B0007/B0018: 168/168/168/132 discharges, 24 degC ambient)
Fade = pp of own initial capacity; end fade 28.2 / 41.4 / 24.0 / 27.4 pp. Candidates fitted per cell (least squares) and
with held-out cells (fit on 3, test on the 4th), both in cycles and in life fraction (cycle / window length, which
assumes the window length is known):

| model | in-sample RMSE (pp, mean of 4) | held-out RMSE, cycles | held-out RMSE, life fraction |
|---|---|---|---|
| linear | 2.15 | 5.81 | 5.52 |
| exp decay 100(1-e^-rk) | 1.99 | 5.73 | 5.55 |
| exp accel a(e^bk - 1) | 2.66 | 6.08 | 5.61 |
| knee (softplus, 4 par) | 1.88 | 5.87 | 5.58 |

Held-out RMSE by cell (cycles, exp decay): B0005 4.1, B0006 9.2 (end error -12 pp), B0007 7.2 (+12), B0018 2.5.
Reading: candidates are **not distinguishable** out of sample (differences of 0.1-0.3 pp against a 5.5 pp error);
cell-to-cell rate spread (0.14-0.25 pp/cycle, B0006 worst) dominates. Fitted knees sit early (cycle 21-27) and are
not a late-life knee. Share of end fade reached at half the window: 0.54 / 0.57 / 0.54 / 0.54 (0.5 = linear): mildly
front-loaded, i.e. no accelerating end-of-life in this window. The model's fade (linear in cycles + sqrt calendar)
is therefore consistent with these cells in shape; nothing was changed in dynamics.

Resistance (Re + Rct, R/R0 vs fade): end R/R0 1.18 / 1.23 / 1.45 / 0.98; per-cell slope per pp 0.0080 / 0.0053 / 0.0151 /
-0.0023. Cell mean 0.0065, 95% t-interval [-0.005, 0.018] (4 cells). Held-out-cell RMSE of R/R0 0.05-0.17.

Temperature acceleration: **not estimable**. All cells ran at 24 degC; mean measured cell temperature differs by <2 degC
(self-heating, confounded with discharge cut-off 2.5/2.2/2.5/2.7 V). `activation_k` stays a prior.

### Mapping to model parameters
| parameter | prior | rig | decision |
|---|---|---|---|
| `resistance_fade` | 0.03 per pp | 0.0065 (interval above excludes 0.03) | **overridden to 0.0065**; the data reject the prior's size but do not pin the value |
| `capacity_noise` | 0.05 pp | first-difference sd/sqrt2 = 0.66 pp (robust 0.28; smooth-fit residual 1.99, includes regeneration jumps) | **overridden to 0.66** |
| `resistance_noise` | 0.01 mohm | 1.5% relative scatter x 8 mohm = 0.12 mohm | **overridden to 0.12** |
| `cycle_fade`, `calendar_fade`, `life_cycles` | - | 30% fade in ~100-170 cycles vs model ~2000 flights | not transferable, prior kept |
| `activation_k`, `capacity_limit` | - | no temperature variation / lab cut-offs | prior kept |

Unit translation: model `fade_pct` is pp of healthy capacity = rig fade pp; resistance transfers as R/R0 only;
rig cycles are not flights. `resistance_rate` is still a prior and adds up to ~7% of R0 over nominal life on top of the
fitted fade coupling (possible mild double counting).

### Before/after (sim = 300 units run to the rig's mean end fade, 30.2 pp; real = pooled 4 cells)
| channel | | KS | Wasserstein / real sd | real mean | sim mean | real sd | sim sd |
|---|---|---|---|---|---|---|
| fade pp | prior | 0.088 | 0.135 | 16.7 | 16.3 | - | - |
| fade pp | fitted | 0.087 | 0.134 | 16.7 | 16.3 | - | - |
| R/R0 | prior | 0.64 | 3.12 | 1.14 | 1.54 | 0.126 | 0.327 |
| R/R0 | fitted | 0.16 | 0.18 | 1.14 | 1.15 | 0.126 | 0.099 |

Fade matches only because the sim window was scaled to the rig's end fade (2088 flights vs 132-168 cycles); it is
not an independent check. R/R0 improved because `resistance_fade` was fitted to it; it is in-sample for the 4 cells.

## Limits (plain language)
* The hydraulic rig is a 160 bar lab bench with a stepped, designed fault grid. It says nothing about when an
  aircraft pump fails, how fast filters clog or how fluid ages. All hydraulics rates are still assumed priors (no citation); in this revision the per-flight damage rates were divided by 4.5 (life 2000 -> 9000 flights) to move the removal rate to a plausible order, a prior change, not a data fit.
* The one effect the rig does show strongly (cooler failure, +17 degC, -1.3 bar on PS5) has no counterpart in the model.
  The rig's pressure rises with leak and low precharge, opposite to the model; this is probably a load-controlled bench
  artefact, but it was not resolved, so pump pressure sensitivity remains a prior.
* Valve wear is invisible in cycle-mean features here; dynamic features were not tried.
* Rig noise (lab sensors, averaged 60-6000 samples) is far below the priors, so the noise fit left
  hydraulics unchanged. Real fleet sensor noise is unknown.
* The cells are 2 Ah 18650 cells, 4 units, 1 temperature, 1 current profile. The 5.5 pp held-out fade error and
  the wide resistance interval are the honest uncertainty. The three battery overrides are small-sample relative
  couplings and noise, not validated aircraft battery behaviour.
* Validation is distributional (KS, Wasserstein) and partly in-sample; it is not a prediction test of the simulator.
