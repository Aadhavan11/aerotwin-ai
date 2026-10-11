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


# --------------------------------------------------------------------------
# Non-engine components: rule-based demo models
# --------------------------------------------------------------------------
class GenericSpec(NamedTuple):
    kind: str          # ComponentKind
    wear_signal: str   # key in TelemetryRow.values; the signal the rule watches
    worsens: str       # "up" or "down": which way the signal moves as it wears
    healthy: float     # typical value when new
    limit: float       # value at which health is 0 (the component is at its limit)
    unit: str
    extras: tuple[str, ...]  # other keys the telemetry source should send


# SYNTHETIC DEMO VALUES. Not real limits, not from any manual. The rule is
# "health falls linearly from `healthy` to `limit`"; the label is
# "rule-based demo model" everywhere it is shown.
GENERIC: dict[str, GenericSpec] = {
    "brakes": GenericSpec("brakes", "pad_wear_mm", "up", 0.0, 12.0, "mm", ("disc_temp_c",)),
    "landing_gear": GenericSpec("landing_gear", "strut_pressure_kpa", "down", 1800.0, 1200.0, "kPa", ("shock_load_g",)),
    "hydraulics": GenericSpec("hydraulics", "pump_pressure_kpa", "down", 20700.0, 17200.0, "kPa", ("fluid_temp_c",)),
    "avionics": GenericSpec("avionics", "error_rate_per_khr", "up", 0.2, 5.0, "1/kh", ("box_temp_c",)),
}

# Keys of TelemetryRow.values per component kind (engine uses SENSOR_NAMES).
TELEMETRY_KEYS: dict[str, list[str]] = {
    "engine": SENSOR_NAMES,
    **{k: [g.wear_signal, *g.extras] for k, g in GENERIC.items()},
}


def kind_from_component_id(component_id: str) -> str:
    """'SIM-017/engine_2' -> 'engine'; 'SIM-017/brakes' -> 'brakes'."""
    name = component_id.split("/")[-1]
    return "engine" if name.startswith("engine") else name


# Where each engine sensor is measured in a CFM56-type two-spool turbofan:
# (module, station, short description). Used to place readings on the engine section.
ENGINE_STATIONS: dict[str, tuple[str, str, str]] = {
    "Nf": ("fan", "N1", "Fan / low-pressure spool speed"),
    "NRf": ("fan", "N1", "Corrected fan speed"),
    "BPR": ("bypass", "duct", "Bypass ratio"),
    "T24": ("lpc", "24", "Booster (LPC) exit temperature"),
    "Nc": ("hpc", "N2", "Core / high-pressure spool speed"),
    "NRc": ("hpc", "N2", "Corrected core speed"),
    "htBleed": ("hpc", "bleed", "HPC bleed enthalpy"),
    "T30": ("hpc", "3", "HPC exit temperature"),
    "P30": ("hpc", "3", "HPC exit total pressure"),
    "Ps30": ("hpc", "3", "HPC exit static pressure"),
    "phi": ("combustor", "4", "Fuel flow to Ps30 ratio"),
    "W31": ("hpt", "cooling", "HPT coolant bleed"),
    "W32": ("lpt", "cooling", "LPT coolant bleed"),
    "T50": ("lpt", "5", "LPT exit temperature"),
}
