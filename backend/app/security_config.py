"""An unsafe-settings scan of a project's Claude config (phase 7e).

``claude_config_findings(project_dir)`` reads ``.claude/settings.json``, ``.claude/settings.local.json`` and
``.mcp.json`` (no links followed, regular files, at most 256 KB, valid JSON or skipped) and returns findings in
the shape the findings tracker takes (``severity``, ``category``, ``title``, ``file``, ``description``,
``remediation``). Titles start "Claude settings: ". A value that looks like a token is never shown, only the
name of the setting that holds it. Never raises."""

from __future__ import annotations

import json
import os

from .recommend import detect
from .redaction import TOKEN_SHAPES, URL_USERINFO, clean_text, redact

FILES = ((".claude", "settings.json"), (".claude", "settings.local.json"), (".mcp.json",))
WIDE_ALLOWS = frozenset({"*", "Bash", "Bash(*)"})
PREFIX = "Claude settings: "
_MAX_NODES = 5000


def _safe(name) -> str:
    """A name from the file, for a title, a sentence or the prompt: masked with the redaction patterns, hidden
    characters cleaned, cut to 60 characters."""
    return clean_text(redact(str(name), []))[:60]


def _finding(severity, title, file, description, remediation):
    return {"severity": severity, "category": "Security misconfiguration", "cwe": "", "title": PREFIX + title,
            "file": file, "line": "", "description": description, "remediation": remediation}


def _token_paths(node, path, out, budget):
    """Names (never values) of settings whose string value looks like a token."""
    budget[0] -= 1
    if budget[0] < 0 or len(out) >= 10:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _token_paths(v, f"{path}.{k}" if path else str(k), out, budget)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _token_paths(v, f"{path}[{i}]", out, budget)
    elif isinstance(node, str) and (TOKEN_SHAPES.search(node) or URL_USERINFO.search(node)):
        out.append(path)


def _scan(doc, name) -> list[dict]:
    out = []
    if not isinstance(doc, dict):
        return out
    hooks = doc.get("hooks")
    if isinstance(hooks, dict) and hooks:
        events = ", ".join(_safe(k) for k in sorted(str(k) for k in hooks)[:8])
        out.append(_finding(
            "medium", f"hooks in {name}", name,
            f"{name} runs commands automatically on these events: {events}. A hook runs with your permissions.",
            "Check every hook command, and remove any you do not recognise."))
    perms = doc.get("permissions")
    allow = perms.get("allow") if isinstance(perms, dict) else None
    wide = sorted({a for a in allow or [] if isinstance(a, str) and a.strip() in WIDE_ALLOWS})
    if wide:
        out.append(_finding(
            "high", f"wide permission in {name}", name,
            f"{name} allows {', '.join(_safe(w) for w in wide)}, so the agent may run any command without asking.",
            "Allow only the specific commands the project needs."))
    servers = doc.get("mcpServers")
    if isinstance(servers, dict) and servers:
        names = ", ".join(_safe(k) for k in sorted(str(k) for k in servers)[:8])
        out.append(_finding(
            "low", f"connected tools in {name}", name,
            f"{name} sets up connected tools (MCP servers): {names}. They run on the machine and can read what the agent can.",
            "Keep only tools you trust, and pin their versions."))
    tokens: list[str] = []
    _token_paths(doc, "", tokens, [_MAX_NODES])
    if tokens:
        out.append(_finding(
            "high", f"a token-like value in {name}", name,
            f"{name} holds what looks like a secret at: {', '.join(_safe(t) for t in tokens[:5])}.",
            "Move the secret out of the file (use an environment variable) and rotate it."))
    return out


def claude_config_findings(project_dir) -> list[dict]:
    try:
        if not project_dir:
            return []
        root_fd = os.open(str(project_dir), os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return []
    found: list[dict] = []
    try:
        for parts in FILES:
            try:
                data = detect._file_at(root_fd, parts)
                if data is None:
                    continue
                doc = json.loads(data.decode("utf-8", errors="replace"))
            except (ValueError, RecursionError, OSError):
                continue
            found += _scan(doc, "/".join(parts))
    finally:
        os.close(root_fd)
    return found
