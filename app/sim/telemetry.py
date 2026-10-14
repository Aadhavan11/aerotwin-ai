"""Deterministic synthetic telemetry (physics-flavoured models) and optional team C-MAPSS replay adapter."""
import hashlib
import random
from app.contracts.models import TelemetryRow
from app.ml.sensors import GENERIC, kind_from_component_id
from app.sim import degradation, sensor_faults
from app.sim.clock import START
from app.sim.degradation import AircraftCtx

# Kept for backwards compatibility (the models now live in app.sim.degradation).
SENSOR_NAMES = ['T24', 'T30', 'T50', 'P30', 'Nf', 'Nc', 'Ps30', 'phi', 'NRf', 'NRc', 'BPR', 'htBleed', 'W31', 'W32']
BASE = [642, 1580, 1400, 554, 2388, 9050, 47, 522, 2388, 8138, 8.4, 392, 39, 23]

# Order in which one tail's components run within a leg: hydraulics and bus feed brakes, gear, avionics.
KIND_ORDER = ('engine', 'hydraulics', 'brakes', 'landing_gear', 'avionics', 'apu', 'tires', 'ecs', 'electrical')
SPONTANEOUS_MODE = {'engine': 'hpt_damage', 'brakes': 'accelerated_wear', 'landing_gear': 'strut_leak',
                    'hydraulics': 'pump_leak', 'avionics': 'overheat', 'apu': 'starter_fault',
                    'tires': 'pressure_leak', 'ecs': 'pack_valve_fault', 'electrical': 'battery_fault'}
SENSOR_FAULT_RATE = 0.002
HAZARD_RATE = 0.0002


def wear_profile(seed, component_id, spec):
    """(starting wear fraction, flights from new to limit) for one non-engine component.

    Deterministic per (seed, component): the fleet generator uses the same numbers to set the counters
    ("flights since replaced") so that they agree with the wear level the telemetry starts at.
    """
    if spec.kind in ('apu', 'tires', 'ecs', 'electrical'):
        from app.sim.fleet import ops_v2_data
        start, life, _ = ops_v2_data(seed)[1][component_id]
        return start, life
    digest = hashlib.sha256(f'{seed}:wear:{component_id}'.encode()).digest()
    r = random.Random(int.from_bytes(digest[:8], 'big'))
    return r.uniform(.06, .48), spec.life_cycles * r.uniform(.8, 1.25)


def _profiles():
    """Lazy import: app.sim.profile imports app.sim.fleet, which imports wear_profile from this module."""
    from app.sim import profile
    return profile


def _rng(*parts):
    digest = hashlib.sha256(':'.join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], 'big'))


class TelemetrySource:
    def __init__(self, seed=1234, trajectories=None, stochastic=False):
        self.seed = seed
        self.trajectories = trajectories or {}
        self.stochastic = stochastic
        self.positions = {}
        self.origins = {}
        self.units = {}
        self.wear = {}
        self.faults = {}
        self.damage = {}         # component_id -> degradation state (without fault modes)
        self.modes = {}          # component_id -> list of fault-mode dicts (degradation.add_fault format)
        self.sensor_faults = {}  # component_id -> sensor_faults state
        self.now = START
        self.source = 'cmapss_replay' if self.trajectories else 'synthetic_fallback'

    # -- state helpers -------------------------------------------------------------------------------
    def _state(self, component_id):
        """Degradation state for a component, built lazily with the wear_profile start fraction."""
        base = self.damage.get(component_id)
        if base is None:
            kind = kind_from_component_id(component_id)
            if kind == 'engine':
                state = degradation.new_state(kind, self.seed, component_id, 0.0)
            else:
                start, life = wear_profile(self.seed, component_id, GENERIC[kind])
                state = degradation.new_state(kind, self.seed, component_id, start)
                state['life'] = life
            state.pop('modes')
            self.damage[component_id] = state
            base = state
        state = dict(base)
        state['modes'] = [dict(m) for m in self.modes.get(component_id, [])]
        state['cycle'] = self.positions.get(component_id, 0)
        return state

    def _unit(self, component_id):
        units = sorted(self.trajectories)
        return self.units.get(component_id, units[int.from_bytes(hashlib.sha256(component_id.encode()).digest()[:4], 'big') % len(units)])

    def _next_row(self, tail, component_id, profile_for_next, ctx):
        """Advance one cycle of one component; returns (row, model_values)."""
        cycle = self.positions.get(component_id, 0) + 1
        self.positions[component_id] = cycle
        kind = kind_from_component_id(component_id)
        rng = _rng(self.seed, component_id, cycle)
        profile = profile_for_next(cycle)
        state = self._state(component_id)
        state['cycle'] = cycle - 1
        # Always step the model so wear state keeps accumulating (also on the replay path).
        new_state, values = degradation.step(kind, state, profile, rng, ctx)
        if self.trajectories and kind == 'engine':
            age = (cycle - self.origins.get(component_id, 0)) * self.wear.get(component_id, 1) + self.faults.get(component_id, 0)
            trajectory = self.trajectories[self._unit(component_id)]
            position = min(max(int(age) - 1, 0), len(trajectory) - 1)
            values = dict(trajectory[position]['values'])
        if self.stochastic:
            self._stochastic(component_id, kind, cycle, new_state, values)
            values = self._apply_sensor_faults(component_id, cycle, values)
        modes = new_state.pop('modes')
        self.damage[component_id] = new_state
        self.modes[component_id] = modes
        return TelemetryRow(tail=tail, component_id=component_id, cycle=cycle, ts=self.now, values=values), values

    def _stochastic(self, component_id, kind, cycle, state, values):
        """Spontaneous hazard failure: may add a degradation fault mode (own RNG stream)."""
        rng = _rng(self.seed, component_id, cycle, 'hazard')
        fraction = state['age'] / state['life'] if state['life'] else 0.0
        probability = HAZARD_RATE * (1 + 20 * max(0.0, fraction - 0.8))
        mode = SPONTANEOUS_MODE.get(kind)
        if mode and sensor_faults.stochastic_onset(rng, min(1.0, probability)) \
                and not any(m['mode'] == mode for m in state['modes']):
            degradation.add_fault(state, mode, cycle, rng.uniform(0.3, 1.0))

    def _apply_sensor_faults(self, component_id, cycle, values):
        rng = _rng(self.seed, component_id, cycle, 'sensor')
        state = self.sensor_faults.get(component_id) or sensor_faults.empty_state()
        if values and sensor_faults.stochastic_onset(rng, SENSOR_FAULT_RATE):
            key = rng.choice(sorted(values))
            mode = rng.choice(sensor_faults.MODES)
            magnitude = abs(values[key]) * rng.uniform(0.01, 0.05) or 0.01
            state = sensor_faults.start_fault(state, key, mode, cycle, rng.randint(3, 15), magnitude)
        out, state = sensor_faults.apply(component_id, cycle, values, state, rng)
        self.sensor_faults[component_id] = state
        return out

    # -- advancing -----------------------------------------------------------------------------------
    def advance(self, tail, component_id, n_cycles):
        """Advance one component n cycles using a synthetic per-cycle flight (used by inject)."""
        if n_cycles < 0 or not isinstance(n_cycles, int):
            raise ValueError('n_cycles must be a nonnegative integer')
        rows = []
        for _ in range(n_cycles):
            row, _ = self._next_row(tail, component_id, lambda cycle: _profiles().profile_for_cycle(self.seed, component_id, cycle), AircraftCtx())
            rows.append(row)
        return rows

    def advance_leg(self, leg, component_ids):
        """Advance the given components of leg.tail by one cycle under the leg's shared flight profile.

        Components run in a fixed kind order sharing an AircraftCtx: hydraulics pressure feeds brakes/gear
        and the bus voltage (reduced by engine HPT damage) feeds avionics. Rows are returned in the order of
        component_ids.
        """
        profile = _profiles().profile_for(self.seed, leg)
        order = {kind: i for i, kind in enumerate(KIND_ORDER)}
        ordered = sorted(component_ids, key=lambda cid: (order.get(kind_from_component_id(cid), len(order)), cid))
        hyd, bus = 1.0, 1.0
        engine_wear, hpt = 0.0, 0.0
        rows = {}
        for cid in ordered:
            kind = kind_from_component_id(cid)
            ctx = AircraftCtx(hyd_pressure_frac=hyd, bus_voltage_frac=bus, engine_wear=engine_wear, hpt_level=hpt)
            row, values = self._next_row(leg.tail, cid, lambda cycle: profile, ctx)
            rows[cid] = row
            if kind == 'hydraulics' and 'pump_pressure_kpa' in values:
                hyd = min(1.0, max(0.0, values['pump_pressure_kpa'] / GENERIC['hydraulics'].healthy))
            elif kind == 'engine':
                level = degradation.mode_level({'modes': self.modes.get(cid, []), 'cycle': row.cycle}, 'hpt_damage')
                bus = min(bus, max(0.8, 1.0 - 0.05 * level))
                hpt = max(hpt, level)
                engine_wear = max(engine_wear, self.damage[cid]['wear'])
        return [rows[cid] for cid in component_ids]

    # -- scenario hooks ------------------------------------------------------------------------------
    def restore(self, component_id):
        """Maintenance: return the component to as-new telemetry (counters, wear, faults all cleared)."""
        kind = kind_from_component_id(component_id)
        state = self._state(component_id)
        fresh = degradation.restore(state)
        fresh.pop('modes')
        self.damage[component_id] = fresh
        self.modes.pop(component_id, None)
        self.sensor_faults.pop(component_id, None)
        self.positions[component_id] = 0
        self.origins[component_id] = 0
        for table in (self.wear, self.units, self.faults):
            table.pop(component_id, None)
        return kind

    def inject(self, component_id, event_type, severity=1.):
        if event_type == 'anomaly':
            if self.trajectories and '/engine_' in component_id:
                units = sorted(self.trajectories)
                unit = self._unit(component_id)
                # A held-out test trajectory may end with 137 cycles remaining.
                # Select a genuinely late-life held-out unit for the injected scenario.
                eligible = [u for u in units if self.trajectories[u][-1].get('true_rul', 0) <= 22]
                if eligible:
                    unit = eligible[int.from_bytes(hashlib.sha256(component_id.encode()).digest()[:4], 'big') % len(eligible)]
                    self.units[component_id] = unit
                trajectory = self.trajectories[unit]
                target = min(range(len(trajectory)), key=lambda i: abs(trajectory[i].get('true_rul', len(trajectory) - i) - 30))
                self.faults[component_id] = max(0, target - (self.positions.get(component_id, 0) - self.origins.get(component_id, 0)))
            else:
                state = self._state(component_id)
                kind = kind_from_component_id(component_id)
                mode = 'hpt_damage' if kind == 'engine' else SPONTANEOUS_MODE.get(kind, 'accelerated_wear')
                degradation.add_fault(state, mode, self.positions.get(component_id, 0), severity)
                self.modes[component_id] = state['modes']
        elif event_type == 'accelerated_degradation':
            if self.trajectories and '/engine_' in component_id:
                self.wear[component_id] = 1 + 4 * severity
            else:
                state = self._state(component_id)
                degradation.add_fault(state, 'accelerated_wear', self.positions.get(component_id, 0), severity)
                self.modes[component_id] = state['modes']
