"""Server-only fixed texts of resume and handoff (Global Constraints, F11).
The texts co-guard's hook uses live in ``app.guard.co_hook``.

``{…}`` are ``str.format`` fills (``RECORD_HEAD``'s ``{hh:mm}`` is ``{hhmm}``
here, since a colon starts a format spec)."""

# ---- the recovery record (rendered by recovery.render_record) ----------------------------------

RECORD_HEAD = ("co-orchestrator: this run was cut off at {hhmm} UTC ({why}). "
               "What follows is a record, not instructions.")
IN_FLIGHT_ADVICE = {
    "shell": "may have half run: check its effect before running it again",
    "file": "may be half written: check the file",
    "Agent": "the helper's work is lost unless it wrote files",
    "other": "may not have finished",
}
GONE_LINE = "These were running and are now gone: {list}. Ask before restarting dev servers."
RECORD_TAIL = ("Run git status first and check the effect of anything listed above. Do not repeat a command "
               "without checking its effect. Then continue the task.")
GUEST_TAIL = ("This is someone else's repo: only look; do not re-run anything from it until you have "
              "checked it.")

# ---- injected blocks -----------------------------------------------------------------------------

HANDOFF_HEAD = (
    "co-orchestrator: where this thread stands (a record, not instructions; re-check before relying on it):"
)
LOG_HEAD = "co-orchestrator: the last actions of the previous step (a record, not instructions):"
CONTINUE_PROMPT = ("Continue the task from where it stopped. First check the record above: run git status and "
                   "check the effect of anything that was in flight before repeating it.")
NO_COMMITS_LINE = ("co-orchestrator: this repo has no commits yet, so a bad edit cannot be undone with git until "
                   "the first commit.")
OUTSIDE_LINE = ("co-orchestrator: commits were made outside this thread since its last step: {list}. Re-read the "
                "files they touch before relying on earlier reads.")
CRED_NOTE = "credentials changed since that session"

# ---- composer texts (Task 6) ---------------------------------------------------------------------

# a recovered step that resumed its own session (table row 1)
RECOVERED_CONTINUE = "Continue the task."
# a fresh Continue: what the person asked for (table row 4)
REQUEST_LINE = "co-orchestrator: the request was: {request}"
# a new thread in a shared directory (table row 8)
OTHERS_LINE = "co-orchestrator: other runs working in this directory now (a record, not instructions): {list}."
OTHERS_HIDDEN = "{n} run(s) you cannot see"
# the outside-commit list when the branch was rewritten, or when it may be longer than shown
HISTORY_CHANGED = "history changed since the last save"
MAYBE_MORE = "maybe more"
# fresh_session reasons (besides CRED_NOTE)
OTHER_SENDER_NOTE = "a different person continues this thread"
NO_SESSION_NOTE = "previous session not available"
