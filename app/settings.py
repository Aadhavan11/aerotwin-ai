"""Settings from environment variables / .env. Never print or log secrets (ANTHROPIC_API_KEY)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: python-dotenv is in requirements.txt, but do not crash without it
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# One switch for everything, plus one per module group: MOCK_TWIN, MOCK_SOLVER, MOCK_WHATIF, MOCK_SIM, MOCK_AGENTS, MOCK_COPILOT
MOCK: bool = _flag("MOCK")
SEED: int = int(os.environ.get("SEED", "1234"))
DB_PATH: Path = Path(os.environ.get("DB_PATH", str(ROOT / "data" / "aerotwin.db")))
LLM_MODEL: str = os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
HAS_LLM_KEY: bool = bool(os.environ.get("ANTHROPIC_API_KEY"))  # only the boolean is ever exposed
# Telemetry engine behind the simulator service: "v1" (app.sim.telemetry, default) or "v2" (app.sim.v2.World).
SIM_ENGINE: str = os.environ.get("SIM_ENGINE", "v1").strip().lower()
SIM_START = "2026-10-12T12:00:00Z"  # SimClock origin (UTC)
# Optional ops-to-solver links (default OFF: the golden plan is unchanged). LLP_TASKS: life-limited parts near their limit become
# solver tasks (T-LLP-...); MEL_TASKS: open MEL deferrals become rectification tasks (T-MEL-...). See app/solver/links.py.
LLP_TASKS: bool = _flag("LLP_TASKS")
MEL_TASKS: bool = _flag("MEL_TASKS")
VERSION = "0.1.0"


def mock_for(group: str) -> bool:
    """True if this module group should use the mock router."""
    return MOCK or _flag(f"MOCK_{group.upper()}")
