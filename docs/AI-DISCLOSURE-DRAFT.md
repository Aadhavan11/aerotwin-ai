# AI-assistance disclosure (checked against the official rules)

**Rules checked:** `docs/HACKLANTA-RULES.md` (Shamit read the Hacker Guide on Sat 2026-10-10, 11:35 PM ET). AI tools are allowed; there is no separate AI-disclosure rule.
Projects that "misrepresent how they were built" are ineligible, and anything that existed before Hacklanta must be disclosed, so we state plainly that Claude was used.
Deadline: **Sun Oct 11, 9:00 AM ET, no exceptions** (internal target 8:15 AM). Re-read the guide before submitting, it is updated live.

## Short text for the README and Devpost

> **AI assistance.** This project was built with the help of Claude (Anthropic), used as a coding and writing assistant by team members.
> Claude helped draft code, tests and documentation; the team reviewed and ran the results. The prototype itself does not rely on a language
> model for any number or safety decision: the maintenance schedule comes from a constraint solver (OR-Tools CP-SAT), and the agents call
> deterministic tools. All aircraft, fleet, task-card and degradation data are simulated (NASA C-MAPSS for engine degradation, otherwise synthetic).
> Results are simulated relative to a stated baseline; they are not verified airline savings and the system is advisory only.

## Per-module note (scheduler, what-if, agent tools: Pardhu)

> The scheduler (`app/solver`), what-if engine (`app/whatif`) and agent tool registry (`app/agents`) were written with Claude. They were checked by
> 299 automated tests, an independent constraint checker, and a script (`scripts/mutation_check.py`) that deliberately breaks 30 rules one at a time
> to confirm the tests catch each one.

## Pre-existing material (my part)
Nothing in `app/solver`, `app/whatif`, `app/agents` or their tests existed before the event. Open-source libraries only: OR-Tools, SimPy, pydantic, FastAPI, PyYAML.
The 10 task cards are synthetic and written during the event.
