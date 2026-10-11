# Hacklanta 2026: the official rules, as they affect us

Source: the organizers' Hacker Guide (public page: https://aluminum-soprano-222.notion.site/Hacklanta-2026-Hacker-Guide-3b81ccc4ed5c80f4b717c0ccc4b5df54), read **Sat 2026-10-10, 11:35 PM ET**.
The organizers say it is updated live, so **re-read it before submitting** and watch the event Discord for announcements (the Google Form link is posted there).
This file records what the guide says; where it is silent we say so instead of guessing.

## Dates (ET)

| When | What |
|---|---|
| Fri Oct 9, 8:30 PM | Hacking starts |
| **Sun Oct 11, 9:00 AM** | **Hacking ends. All materials must be submitted by then: "NO EXCEPTIONS!"** |
| Sun Oct 11, 9:30-11:30 AM | Judging Expo, Student Center East State Ballroom |
| Sun Oct 11, 12:45 PM | Closing ceremony (winners) |

Our internal times: **freeze the code at 7:00 AM, submit by 8:15 AM** (45 minutes of buffer for upload problems), then rehearse.

## How judging works (this answers "walk-around or on stage?")

It is **table judging at an expo, not a stage**:

- Desks with **2 judges each**. You wait in line until staff call you to an open table, present, then leave the room (staff direct the flow).
- **3 minutes to pitch + 2 minutes of Q&A**, for the **whole team** (it is not 3 minutes per person).
- The guide's sample judge questions: Does the project address the track's prompt? How polished is the prototype or demo? How effective is the team's presentation? Is there a clear, compelling use case?
- Demoing at judging is worth the most points in the prize shop (200). Stay through Sunday.

What follows for us: one presenter plus one person driving the laptop, the flow in [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md) (it is already 3 minutes), the demo running locally with no internet, a recorded backup of the same flow, and answers ready for the Q&A below.

## Track and side challenges

- **Main track: AI/ML** ("put models to work: agents, assistants, and data pipelines that take real tasks off someone's plate"). A team may enter **one** main track and **at most 3** side challenges.
- Side challenges listed: Mercedes (computer vision), MLH (ElevenLabs, Tiger Data, Gemini API, Vultr, Solana, Auth0, GoDaddy domain name), Replit (small-business app), Photon (agents in iMessage), Shock Tracker (zero user data). **None fits AeroTwin without extra work, so the default is none.** Revisit only if someone has spare time.
- Open: the event's stated theme is "Music Festival". The guide does not say projects must follow it (the tracks are separate). Ask the organizers on Discord if we want to be sure.

## What to submit (all required; ONE member submits)

1. **Devpost** project: title and description, the problem and who it serves, team roster (max 4), main track, technologies / APIs / datasets used, **disclosure of anything made before Hacklanta**, any instructions for judges.
2. **Public GitHub repo** with a useful README (all the code). Ours is public.
3. **Working demo link or clear run instructions.**
4. **Demo video, 2-3 minutes, listed as PUBLIC** (a YouTube or Drive link is fine).
5. **Google Form** (link posted in Discord before the deadline): Devpost link, project name, members, track, challenges. **Not submitting the form means the project is not considered.** One member only, for both Devpost and the form.

All links must stay accessible throughout judging.

## Originality and AI rules (exact wording)

- "An original, working project built during the official Hacklanta hacking period."
- "Open-source libraries, APIs, frameworks, AI tools, and starter templates are allowed. **Disclose anything that existed before Hacklanta.**"
- "The main design, development, and integration must happen during the event."
- "Previously completed projects, projects reused from another hackathon without major new work, or projects that **misrepresent how they were built** are not eligible."

The guide has **no AI-disclosure rule**: AI tools are allowed. **House rule (Shamit, 2026-10-11): we publish no AI-use statement unless he asks for one or an event rule requires one, and then it is run by him first.** We never claim the project was built without AI tools; if a judge asks how it was built, answer truthfully.

### What existed before hacking started (to disclose on Devpost)

- The **AeroTwin AI concept document** (Aadhavan) and a separate planning document for a different idea (the parked `parked-meridian/` folder): planning text only, no code.
- Public data and open-source libraries: NASA C-MAPSS (FD001), scikit-learn, OR-Tools, FastAPI, React, three.js and the rest of `requirements.txt` / `web/package.json`.
- No application code existed before the event: this repository was created on Sat Oct 10 at 7:22 PM, after hacking opened.
- Suggestion: remove `parked-meridian/` from the final repo (history keeps it) so judges do not wonder what it is, or mention it in the disclosure.

## Likely judge questions (answers must match what the code does)

- *Is the data real?* Engine degradation is NASA C-MAPSS run-to-failure data; the fleet, flights, stations, parts and technicians are simulated and labeled SIMULATED everywhere.
- *Does an LLM decide maintenance?* No. The schedule comes from a constraint solver, the agents call deterministic tools, and a person must approve every work order.
- *What if the model is wrong?* Deadlines use the conservative p10 remaining-life estimate and are hard constraints; the system is advisory only and certifies nothing.
- *Compared with what?* A stated static baseline (service at the home base in the last window before the deadline). Report only numbers the run printed.

## Checklist (owners are proposals; tick in a small commit)

- [ ] Everyone: confirm you are registered at hacklanta.dev and in the Discord (the guide marks the Discord "REQUIRED").
- [ ] Demo run once on a laptop with the NASA files (`make data`), then `make demo-check` passes.
- [ ] **Backup video recorded** and uploaded as PUBLIC (Aadhavan, by 7:30 AM).
- [ ] **Devpost text** written from this file's list (Aadhavan; draft help available).
- [ ] README top section: what it is and how to run it (done).
- [ ] Decide `parked-meridian/` (remove or disclose).
- [ ] **Google Form** submitted once (one member), after the Devpost link exists.
- [ ] Submitted by 8:15 AM Sunday; links tested from a logged-out browser.
