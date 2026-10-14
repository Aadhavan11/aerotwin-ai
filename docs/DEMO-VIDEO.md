# Demo video (2-3 minutes, captions only)

The walkthrough is recorded from a scratch copy of the app (same code and data as the public demo, so nothing on the live site changes), in a headless browser
with an on-screen cursor and captions, and encoded with ffmpeg. The language-model agent run is shown sped up (marked on screen). Everything shown is simulated.

What it shows, in order (the captions are on the video):

1. Sign in with a position (operations, maintenance, crew, dispatch, admin).
2. Fleet overview: health of 24 aircraft; the aircraft registry first, then the maintenance stations; a question typed into the registry search is answered in place.
3. Aircraft twin: the whole aircraft, then "Where the fault is" (camera fly-in to engine 2, point of failure, sensors against the healthy baseline, deviations in red); an ask field on the fault.
4. Ask bar (Ctrl/Cmd+K): the AI sees the screen ("What is the tail number of this aircraft?" is answered as SIM-017 without being told).
5. Copilot page: conversation, the five agents in language-model mode drafting work orders, approval by a person, the audit trail.
6. Maintenance plan and Simulation lab.
7. Other positions: pilot (readiness brief, trips, duty and rest), dispatch (flight release), and the master account switching positions.

Honest labels: Duty & rest, Weather, Report a defect, Crew roster and Messages are previews with sample data (docs/ROLE-VIEWS.md).

To re-record: the scratch recorder is not part of the repo; any screen recorder over https://shamitbasu.com/hacklanta works (sign in with a demo account first).
