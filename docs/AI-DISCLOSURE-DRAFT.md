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

## Per-module note (health model, digital twin, copilot, LLM mode: Aashirth)

> The remaining-life and anomaly models (`app/ml`), the digital twin state engine (`app/twin`), the copilot, work-order and audit backend
> (`app/copilot`) and the LLM mode of the agent workflow (`app/agents/llm_mode.py`) were written with Claude. They are covered by 111 automated
> tests (`tests/test_sensors.py`, `test_cmapss.py`, `test_ml.py`, `test_twin.py`, `test_copilot.py`, `test_llm_mode.py`).
>
> - **Models:** a random forest for remaining useful life and an isolation forest for anomalies, trained on NASA C-MAPSS FD001. The reported
>   error (MAE 10.84, RMSE 14.39 flights on the last window of 100 test engines; 3.8 late in life) is measured on engines that no model was
>   fitted or calibrated on, and is saved in `app/ml/artifacts/eval.json`. They are not validated on airline data.
> - **No language model decides anything.** The copilot and the agents use an LLM only to choose tools and phrase an answer. Every number in an
>   answer must appear in a tool result, or the answer is replaced by a template built from the data. Any API error falls back to the scripted path.
> - **Work orders** are always created waiting for approval; only a named person can approve or reject one, and every step is written to the audit log.
> - **Not exercised against the live service:** the Anthropic API path was tested only with a fake client (no key was available during the build).
>   The demo uses the scripted path.
> - **Third-party assets:** NASA C-MAPSS data (public), scikit-learn, FastAPI, pydantic, three.js and the other libraries in
>   `requirements.txt` and `web/package.json`. If a third-party 3D aircraft model is added, it must be credited with its licence here.
