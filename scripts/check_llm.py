#!/usr/bin/env python3
"""Check the live LLM path once the key is set:  python -m scripts.check_llm [--base http://localhost:8000]

Put your own ANTHROPIC_API_KEY in `.env` (copy `.env.example`; never commit it, never paste it in chat), restart the API,
then run this. It asks one copilot question and runs the agents once in LLM mode, and says plainly whether each one was
answered by the model or fell back to the scripted path (and why). The key is never read or printed here.
Exit code 0 only if both used the LLM.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def call(base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read() or b"null")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    base = ap.parse_args().base.rstrip("/")
    status = call(base, "GET", "/copilot/status")
    print(f"model {status['model']}; key present: {status['llm_key_present']}; LLM available: {status['llm_available']}")
    if not status["llm_available"]:
        print("LLM mode is not available. Add ANTHROPIC_API_KEY to .env (see .env.example), restart the API, run this again.")
        return 2
    call(base, "POST", "/sim/step", {"hours": 0})  # makes sure the simulator is attached
    ans = call(base, "POST", "/copilot/chat", {"session_id": "check-llm", "message": "Which aircraft need maintenance soonest, and why?"})
    st = call(base, "GET", "/copilot/status")
    ok_chat = st["last_mode"] == "llm"
    print(f"copilot chat: {'LLM' if ok_chat else 'FELL BACK to scripted'} ({st['last_note']})")
    print("  answer:", ans["answer"][:240].replace("\n", " "))
    run = call(base, "POST", "/agents/run", {"goal": "Plan maintenance for the aircraft that need attention", "scope": {}, "mode": "llm"})
    for _ in range(180):
        if run["status"] != "running":
            break
        time.sleep(1)
        run = call(base, "GET", f"/agents/runs/{run['id']}")
    ok_run = run["mode"] == "llm" and run["status"] == "done"
    print(f"agent run {run['id']}: mode {run['mode']}, status {run['status']}, {len(run['steps'])} steps, {len(run['work_orders'])} work orders")
    if run["mode"] != "llm":
        print("  fell back to scripted; the API log says why:", "; ".join(run.get("missing_info", [])) or "see the server log")
    return 0 if (ok_chat and ok_run) else 1


if __name__ == "__main__":
    sys.exit(main())
