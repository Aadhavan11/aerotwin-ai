"""Do the tests actually bite? Break one rule at a time in a scratch COPY of the code and check that the suite fails.

    python scripts/mutation_check.py          # about 1 minute; prints CAUGHT / SURVIVED per mutation, exit code 1 if any survives

Each mutation replaces one exact snippet (a constraint, a safety rule, a policy) with a broken version. The real files are never touched.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (what we break, file, exact old snippet, broken replacement)
MUTATIONS = [
    ("deadline ignored when building candidates", "app/solver/candidates.py", "max_end = min(dl, w_e + self.max_ext_min, sh_hi)", "max_end = min(w_e + self.max_ext_min, sh_hi)"),
    ("extension cap ignored", "app/solver/candidates.py", "max_end = min(dl, w_e + self.max_ext_min, sh_hi)", "max_end = min(dl, w_e + 99999, sh_hi)"),
    ("technician shift ignored", "app/solver/candidates.py", "max_end = min(dl, w_e + self.max_ext_min, sh_hi)", "max_end = min(dl, w_e + self.max_ext_min)"),
    ("part ready time ignored", "app/solver/candidates.py", "lo = max(w_s, earliest, opt.ready_min, sh_lo)", "lo = max(w_s, earliest, sh_lo)"),
    ("closed hangar still usable", "app/solver/candidates.py", "if st is None or st.hangar_slots <= 0:", "if st is None:"),
    ("unavailable technician still used", "app/solver/candidates.py", "avail = [t for t in with_skill if t.available]", "avail = with_skill"),
    ("forced station ignored", "app/solver/candidates.py", "and (self.force_tails is None or task.tail in self.force_tails)):", "and False):"),
    ("RUL deadline uses p50 instead of p10", "app/solver/tasks.py", "n = int(p10 + 1e-9)", "n = int(comp.rul_cycles.p50 + 1e-9)"),
    ("overdue routine check silently planned", "app/solver/tasks.py", "if due < now:", "if False:"),
    ("CP-SAT: hangar capacity dropped", "app/solver/cpsat.py", "m.add_cumulative([ivs[i] for i in ids], [1] * len(ids), slots)", "pass"),
    ("CP-SAT: part stock limit dropped", "app/solver/cpsat.py", "m.add(sum(cands[i].part_need * xs[i] for i in ids) <= stock.get(pool, 0))", "pass"),
    ("CP-SAT: technician no-overlap dropped", "app/solver/cpsat.py", "            m.add_no_overlap([ivs[i] for i in ids])\n    for ids in by_tail", "            pass\n    for ids in by_tail"),
    ("CP-SAT: aircraft no-overlap dropped", "app/solver/cpsat.py", "    for ids in by_tail.values():\n        if len(ids) > 1:\n            m.add_no_overlap([ivs[i] for i in ids])", "    pass"),
    ("CP-SAT: unscheduled tasks not penalised", "app/solver/cpsat.py", "obj.append(BIG * un)", "obj.append(0 * un)"),
    ("CP-SAT: stability penalty off", "app/solver/cpsat.py", "if previous is not None and c_change:", "if False:"),
    ("greedy ignores part stock", "app/solver/ledger.py", "if c.opt.pool and self.stock.get(c.opt.pool, 0) < c.part_need:\n            return None", "pass"),
    ("greedy ignores hangar capacity", "app/solver/ledger.py", "        if len(overlapping) < slots:\n            return True", "        return True"),
    ("baseline takes the FIRST window, not the last", "app/solver/baseline.py", "w = max(no_ext)   # the LAST window that fits without extension", "w = min(no_ext)"),
    ("final validity guard disabled", "app/solver/engine.py",
        "problems = validate_plan(state, ctx.tasks, assignments, ctx.max_ext_h, force_station, force_tails)\n    if problems and mode_run == \"cpsat\":",
        "problems = []\n    if problems and mode_run == \"cpsat\":"),
    ("replan diff forgets added tasks", "app/solver/plan_metrics.py", "added = [new[t] for t in sorted(new) if t not in old]", "added = []"),
    ("part delay not applied", "app/whatif/events.py", "ps.eta_hours = lead + float(event.delay_hours)", "ps.eta_hours = 0.0"),
    ("technician skill level ignored", "app/solver/skills.py", "any(n == need_name and lvl >= need_level for", "any(n == need_name for"),
    ("exposure margin sign flipped", "app/solver/exposure.py", "minutes_between(t.deadline, a.end)", "minutes_between(a.end, t.deadline)"),
    ("exposure counts flights after the fix", "app/solver/exposure.py", "now <= aware(l.sched_dep) < start", "aware(l.sched_dep) >= start"),
    # agents (piece 11)
    ("work orders drafted already approved", "app/copilot/workorders.py", 'status="pending_approval", plan_id=plan.id, evidence=evidence', 'status="approved", plan_id=plan.id, evidence=evidence'),
    ("work orders without evidence", "app/copilot/workorders.py", 'status="pending_approval", plan_id=plan.id, evidence=evidence', 'status="pending_approval", plan_id=plan.id, evidence=[]'),
    ("explanation invents a number", "app/agents/scripted.py", "{m.late_or_unscheduled} late or unscheduled.\"]", "{m.late_or_unscheduled + 7} late or unscheduled.\"]"),
    ("crashed run left as running", "app/agents/runner.py", 'RUNS.update(run_id, status="error"', 'RUNS.update(run_id, status="running"'),
    ("fallback from llm mode still reported as llm", "app/agents/runner.py", 'RUNS.update(run_id, mode="scripted", status="running")', 'RUNS.update(run_id, status="running")'),
    ("steps appended out of order (no tool_result)", "app/agents/scripted.py", 'self.refs[name] = self.step(agent, "tool_result"', 'self.refs[name] = self.step(agent, "summary"'),
]


def main() -> int:
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, rel, old, new in MUTATIONS:
            work = Path(tmp) / "w"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            for d in ("app", "tests", "fixtures"):
                if (ROOT / d).exists():
                    shutil.copytree(ROOT / d, work / d, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            target = work / rel
            text = target.read_text()
            if old not in text:
                print(f"[SKIPPED ] {name}: snippet not found in {rel} (the code changed; update this list)")
                survived.append(name)
                continue
            target.write_text(text.replace(old, new, 1))
            r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider"],
                               cwd=work, capture_output=True, text=True)
            ok = r.returncode != 0
            print(f"[{'CAUGHT  ' if ok else 'SURVIVED'}] {name}")
            if not ok:
                survived.append(name)
    print(f"\n{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
