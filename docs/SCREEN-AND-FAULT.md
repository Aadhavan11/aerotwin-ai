# The AI sees the screen, and the fault view

## The copilot and agents can see what you see
The web app sends a **ScreenContext** with every copilot question and every agent run: the page, the role, the aircraft on screen, the selected system, the simulation time and the visible text.

- **Screen questions are answered from the screen.** On the Aircraft twin page, "what is the tail number?" answers "You are on the Aircraft twin page, looking at tail SIM-017". Also: "what page am I on?", "which engine is selected?", "what's my role?", "what is the simulation time?". On a page that is not about one aircraft it says so.
- **"This aircraft" means the aircraft on screen.** "What is wrong with this aircraft?" returns everything known about it, worst system first: status, systems, planned maintenance and the next legs.
- **LLM mode and the agents get the same view.** The copilot prompt carries the screen and the aircraft brief (the numbers in them count as legitimate sources for the "no invented numbers" check); agent runs receive `scope.screen`. The replay cache ignores it, so a rehearsed run still replays.
- **Fleet questions are untouched.** "Which aircraft needs attention?" still goes to the normal planner tools.
- Code: `app/copilot/screen.py`, `web/src/lib/screen.ts`, `web/src/components/CopilotDrawer.tsx`; tests in `tests/test_screen_context.py`.

## Where the fault is (Aircraft twin page)
For an engine, the page leads with a close-up of the real engine from the supplied 3D model (the rest of the aircraft faded, the engine skin see-through) and a table of all 14 sensors: healthy baseline, latest reading, change and deviation, sorted by how far each has moved toward failure (red = the largest, amber = smaller).

- **Baseline** = the average of healthy engines (first 60% of life of the model's fit engines, NASA C-MAPSS FD001), from `GET /ml/sensors`. **Deviation** = distance from it in the standard deviations of those healthy engines. Only movement in the direction a sensor takes towards failure counts as a warning (a sensor that falls when engines wear is not flagged for rising).
- **The marker** sits at the engine module (fan, bypass, LPC, HPC, combustor, HPT, LPT) that the largest warnings belong to. It says where to look first; it is an inference from the sensor pattern, not an inspection result, and the page says so.
- With no fault the section says "No fault: sensors are near baseline" and shows no marker.
- Code: `web/src/components/FaultFocus.tsx` and the `focus` mode of `web/src/components/AircraftScene.tsx`.

## Fleet overview statistics (fixed 2026-10-11)
Healthy (no open watch or alert) and available (not in maintenance or AOG) are two different numbers and are labeled as such; the priority card only shows an aircraft that needs something and names the component in the worst status; the status bar has a fixed order. A maintenance stations table shows bays free, technicians on shift now by skill, parts on hand and what is out of stock or on its way with where it re-supplies from.


## The AI inside the product (2026-10-11)

There is no side drawer any more. The AI is where the question comes up:

* **Ask bar** (top right, `Ctrl/Cmd+K` or `/`): search aircraft, pages and work orders, or ask a question and read the answer in place. Suggestions follow the page.
* **Ask fields** on the Fleet registry search (type a question), the aircraft fault close-up, Control room, Maintenance plan, Work orders, Departures, Flight release, Trips and the readiness brief.
* **Copilot page** (`/copilot`): *Ask* (conversation, optional "focus on one aircraft"), *Agents* (the five agents' timeline, built-in or language-model mode), *Work orders* (approve or reject) and *Audit trail*.
  The conversation and the agent timeline scroll to the newest content as it arrives, and stop following if you scroll up to read.
* Every question carries the screen (page, aircraft, selection, clock, visible text), so "this aircraft" and "it" mean what you are looking at.
* If the language model's agent summary quotes a number no tool returned, the run now asks the model once to restate it without that number before falling back to the built-in workflow (`app/agents/llm_mode.py`).

The aircraft twin shows the whole aircraft first. Below it, "Where the fault is" opens wide on the airframe and the camera swoops in on the engine while the rest fades;
"Replay fly-in" runs it again, and reduced-motion settings skip the animation.
