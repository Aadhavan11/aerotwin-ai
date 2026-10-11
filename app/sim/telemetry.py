"""Deterministic synthetic fallback and optional team C-MAPSS replay adapter."""
import hashlib
import random
from app.contracts.models import TelemetryRow
from app.sim.clock import START

SENSOR_NAMES = ['T24','T30','T50','P30','Nf','Nc','Ps30','phi','NRf','NRc','BPR','htBleed','W31','W32']
BASE = [642,1580,1400,554,2388,9050,47,522,2388,8138,8.4,392,39,23]

class TelemetrySource:
    def __init__(self, seed=1234, trajectories=None):
        self.seed = seed
        self.trajectories = trajectories or {}
        self.positions = {}
        self.origins = {}
        self.wear = {}
        self.faults = {}
        self.now = START
        self.source = 'cmapss_replay' if self.trajectories else 'synthetic_fallback'

    def advance(self, tail, component_id, n_cycles):
        if n_cycles < 0 or not isinstance(n_cycles, int):
            raise ValueError('n_cycles must be a nonnegative integer')
        rows = []
        for _ in range(n_cycles):
            cycle = self.positions.get(component_id, 0)+1
            self.positions[component_id] = cycle
            seed = int.from_bytes(hashlib.sha256(f'{self.seed}:{component_id}:{cycle}'.encode()).digest()[:8], 'big')
            rng = random.Random(seed)
            age = (cycle-self.origins.get(component_id,0))*self.wear.get(component_id,1)+self.faults.get(component_id,0)
            if '/engine_' in component_id:
                if self.trajectories:
                    units = sorted(self.trajectories)
                    unit = units[int.from_bytes(hashlib.sha256(component_id.encode()).digest()[:4],'big')%len(units)]
                    trajectory = self.trajectories[unit]
                    position = min(max(int(age)-1,0),len(trajectory)-1)
                    values = dict(trajectory[position]['values'])
                else:
                    values = {name: float(base+rng.gauss(0,abs(base)*.0005)) for name,base in zip(SENSOR_NAMES,BASE)}
                    values['T50'] += age*1.2
                    values['Nc'] -= age*2
                    values['Ps30'] -= age*.04
            else:
                values = {'wear_index': min(1., .05+age*.003), 'temperature': 40+age*.2+rng.gauss(0,.2), 'vibration': .1+age*.002}
            rows.append(TelemetryRow(tail=tail,component_id=component_id,cycle=cycle,ts=self.now,values=values))
        return rows

    def inject(self, component_id, event_type, severity=1.):
        if event_type=='anomaly':
            if self.trajectories:
                units = sorted(self.trajectories)
                unit = units[int.from_bytes(hashlib.sha256(component_id.encode()).digest()[:4],'big')%len(units)]
                length = len(self.trajectories[unit])
                self.faults[component_id] = max(0,length-25-(self.positions.get(component_id,0)-self.origins.get(component_id,0)))
            else:
                self.faults[component_id] = 150*severity
        elif event_type=='accelerated_degradation':
            self.wear[component_id] = 1+4*severity
