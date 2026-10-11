"""Agent tool registry + scripted runner (feature 3.4, scripted mode; piece 11 in work/OWNERSHIP.md). Owner: Pardhu.

    from app.agents.tools import TOOLS, call_tool          # the 16 deterministic tools of CONTRACTS.md section 6 plus suggest_repositioning
    from app.agents.runner import run_workflow             # run_workflow(goal, scope, mode) -> AgentRun
The LLM only ever chooses and explains; every number comes from a tool result. LLM mode itself is piece 12 (not here):
``mode="llm"`` runs the scripted workflow and records ``mode="scripted"`` in the AgentRun.
"""
