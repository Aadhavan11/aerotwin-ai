"""C-MAPSS sensor names used everywhere in AeroTwin.

Single source of truth for the keys of ``TelemetryRow.values`` for engine
components. The simulator (``sim/telemetry.py``) and the scorer
(``ml/health.py``) must both use these exact strings.

Names and units follow the NASA C-MAPSS readme (Saxena and Goebel, 2008).
Only the 14 sensors that carry degradation signal in FD001 are kept.
The others are dropped. Checked against train_FD001.txt (20,631 rows, 100 units):
s1, s5, s10, s16, s18, s19 have a single value; s6 has two values (std 0.0014).
All 14 kept sensors vary. See ``DROPPED``.
"""
from __future__ import annotations

from typing import NamedTuple


class Sensor(NamedTuple):
    column: str   # C-MAPSS column name: s2 .. s21
    name: str     # key used in TelemetryRow.values
    unit: str
    label: str


SENSORS: tuple[Sensor, ...] = (
    Sensor("s2", "T24", "degR", "LPC outlet temperature"),
    Sensor("s3", "T30", "degR", "HPC outlet temperature"),
    Sensor("s4", "T50", "degR", "LPT outlet temperature"),
    Sensor("s7", "P30", "psia", "HPC outlet pressure"),
    Sensor("s8", "Nf", "rpm", "Fan speed"),
    Sensor("s9", "Nc", "rpm", "Core speed"),
    Sensor("s11", "Ps30", "psia", "HPC static pressure"),
    Sensor("s12", "phi", "pps/psi", "Fuel flow to Ps30 ratio"),
    Sensor("s13", "NRf", "rpm", "Corrected fan speed"),
    Sensor("s14", "NRc", "rpm", "Corrected core speed"),
    Sensor("s15", "BPR", "-", "Bypass ratio"),
    Sensor("s17", "htBleed", "-", "Bleed enthalpy"),
    Sensor("s20", "W31", "lbm/s", "HPT coolant bleed"),
    Sensor("s21", "W32", "lbm/s", "LPT coolant bleed"),
)

# Keys of TelemetryRow.values for engine components, in a fixed order.
SENSOR_NAMES: list[str] = [s.name for s in SENSORS]

# C-MAPSS raw column -> AeroTwin sensor name.
COLUMN_TO_NAME: dict[str, str] = {s.column: s.name for s in SENSORS}
NAME_TO_COLUMN: dict[str, str] = {s.name: s.column for s in SENSORS}

UNITS: dict[str, str] = {s.name: s.unit for s in SENSORS}
LABELS: dict[str, str] = {s.name: s.label for s in SENSORS}

# Sensors dropped for FD001 (constant or near-constant). Listed for the record
# and for the data check in ml.cmapss; they never appear in TelemetryRow.values.
DROPPED: dict[str, str] = {
    "s1": "T2 fan inlet temperature",
    "s5": "P2 fan inlet pressure",
    "s6": "P15 bypass-duct pressure",
    "s10": "epr engine pressure ratio",
    "s16": "farB burner fuel-air ratio",
    "s18": "Nf_dmd demanded fan speed",
    "s19": "PCNfR_dmd demanded corrected fan speed",
}

# Raw file layout: unit, cycle, three operating settings, 21 sensors (26 columns).
RAW_COLUMNS: list[str] = (
    ["unit", "cycle", "op1", "op2", "op3"] + [f"s{i}" for i in range(1, 22)]
)
