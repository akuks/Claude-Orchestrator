"""Run log helper: a short, capped list of timestamped lines on a WorkflowRun."""
from datetime import datetime, timezone

MAX_LINES = 200


def append(run, text: str) -> None:
    """Add {"at": iso utc, "text": text} to run.log, keeping the newest 200 lines.

    The list is reassigned (never mutated in place) so SQLAlchemy sees the change.
    A NULL log reads as empty.
    """
    entry = {"at": datetime.now(timezone.utc).isoformat(), "text": text}
    run.log = [*(run.log or []), entry][-MAX_LINES:]
