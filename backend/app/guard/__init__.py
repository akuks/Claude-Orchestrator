"""co-guard: the orchestrator's agent-side guard (Phase 1).

``shell``/``rules`` decide tool calls (stdlib only, copied into the plugin),
``co_hook`` is the hook Claude Code runs, ``plugin`` generates the read-only
plugin, ``runtime`` wires a task (policy, spool, approval address).

Inline ``!`cmd``` in skills, slash commands and skill packs is disabled for
every task (``disableSkillShellExecution``, Ruling R20c): it would run outside
co-guard's PreToolUse check.
"""
