"""One constant per concept: flights/day, engine mean on-wing life, LLP limits live in app.sim.constants."""
import re
from pathlib import Path

import pytest

from app.ops import llp
from app.sim import constants as C
from app.sim.v2 import constants as C2
from app.sim.v2 import drivers, live
from app.sim.v2.models import engine

ROOT = Path(__file__).resolve().parents[1]


def test_reexport_is_identity():
    for name in C2.__all__:
        assert getattr(C2, name) is getattr(C, name)


def test_flights_per_day_single_source():
    from app.solver import common
    assert drivers.LEGS_PER_DAY == C.FLIGHTS_PER_DAY == 6.0
    assert llp.DEFAULT_CPD == common.DEFAULT_LEGS_PER_DAY == C.DEFAULT_LEGS_PER_DAY == C.FLIGHTS_PER_DAY


def test_engine_life_single_source():
    assert engine.PARAMS["flights_per_model_cycle"] == C.ENGINE_FLIGHTS_PER_MODEL_CYCLE
    assert live.MEAN_FIT_LIFE == C.ENGINE_CMAPSS_MEAN_LIFE_CYCLES
    mean_flights = live.MEAN_FIT_LIFE * engine.PARAMS["flights_per_model_cycle"]
    assert mean_flights == pytest.approx(C.ENGINE_MEAN_ON_WING_FLIGHTS)
    assert 15000 <= C.ENGINE_MEAN_ON_WING_FLIGHTS <= 25000


def test_llp_limits_single_source():
    for key, spec in llp.CATALOG.items():
        assert spec["limit"] == C.LLP_CYCLE_LIMITS[key]
    assert engine.PARAMS["llp_limit_cycles"] == C.ENGINE_MODEL_LLP_LIMIT_CYCLES == C.LLP_CYCLE_LIMITS["hpc_rotor"]


def test_v1_counter_cap_is_legacy_only():
    from app.insights import reliability
    assert reliability.ENGINE_SLOT_LIFE == float(C.LEGACY_V1_ENGINE_COUNTER_CAP)
    assert C.LEGACY_V1_ENGINE_COUNTER_CAP != C.ENGINE_MEAN_ON_WING_FLIGHTS


def test_no_redefinition_in_source():
    """Grep-style: modules must not re-declare these numbers as literals."""
    patterns = {
        "app/sim/v2/drivers.py": r"^LEGS_PER_DAY\s*=\s*[0-9.]+",
        "app/solver/common.py": r"^DEFAULT_LEGS_PER_DAY\s*=\s*[0-9.]+",
        "app/ops/llp.py": r"^DEFAULT_CPD\s*=\s*[0-9.]+",
        "app/sim/v2/models/engine.py": r"\"flights_per_model_cycle\":\s*[0-9.]+\s*,|\"llp_limit_cycles\":\s*[0-9.]+\s*,",
        "app/sim/v2/live.py": r"^MEAN_FIT_LIFE\s*=\s*[0-9.]+",
        "app/sim/fleet.py": r"min\(cycles,\s*8200\)",
        "app/insights/reliability.py": r"^ENGINE_SLOT_LIFE\s*=\s*[0-9.]+",
    }
    for rel, pat in patterns.items():
        text = (ROOT / rel).read_text()
        assert not re.search(pat, text, flags=re.M), f"{rel} re-declares a shared constant"
    assert "limit\": 25000" not in (ROOT / "app/ops/llp.py").read_text()
