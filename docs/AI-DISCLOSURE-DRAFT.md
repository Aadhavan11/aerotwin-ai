# AI-assistance disclosure: DRAFT (check the event's real rules before using)

**Status:** the Hacklanta '26 rules on AI assistance, pre-written code and the submission deadline are **unverified**. A search on 2026-10-11 found only a
secondhand listing (Oct 9 to 11, Georgia State University, teams of up to 4, separate application at hacklanta.dev); `hacklanta.dev` was not reachable from the
build environment. Someone on the team must read the official rules and adjust this text. Nothing here is a claim about what the rules require.

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

## Still to confirm
- Does the event require disclosure, and in which place (README, Devpost field, form)?
- Is AI-generated code allowed, and is pre-written or open-source code allowed (we use OR-Tools, SimPy, scikit-learn, FastAPI, pydantic; all open source)?
- Real submission deadline and format.
