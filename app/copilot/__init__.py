"""Copilot backend: chat, work orders, audit, knowledge base, LLM tool loop."""


def reset_all() -> None:
    """Forget drafts, decisions, the audit log and chat sessions (POST /scenario/reset)."""
    from app.copilot import audit, chat, workorders

    audit.reset()
    workorders.reset()
    chat.reset()
    from app.ops import mel

    mel.reset()
    try:
        from app.insights import disruptions

        disruptions.reset()
    except Exception:  # noqa: BLE001
        pass
