"""Security-review findings: extract structured findings from a scan's report
and reconcile them into the per-project findings tracker.

After a security review task finishes, a cheap Claude call parses the human
report into JSON findings. Each is fingerprinted (project + title + file +
category) so the same issue is tracked across scans — new / recurring / fixed.
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone

from sqlalchemy import select

from .agent_env import agent_env_or_none
from .config import settings
from .database import SessionLocal
from .models import Finding, Project, Task
from .security_config import claude_config_findings

_SEV = {"critical", "high", "medium", "low", "info"}

# Schema for structured extraction via Claude's --json-schema (reliable output).
_FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "category": {"type": "string"},
                    "cwe": {"type": "string"},
                    "title": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": "string"},
                    "description": {"type": "string"},
                    "remediation": {"type": "string"},
                },
                "required": ["severity", "title"],
            },
        }
    },
    "required": ["findings"],
}


async def _extract_json(prompt: str, model: str, env: dict) -> dict:
    """Run Claude with --json-schema so the output is guaranteed valid JSON,
    with exactly `env` (from app.agent_env)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.claude_bin,
            "--print",
            "--model",
            model,
            "--json-schema",
            json.dumps(_FINDINGS_SCHEMA),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(env),  # uvloop only accepts a plain dict
        )
    except (FileNotFoundError, OSError):
        return {}
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(prompt.encode()),
            timeout=settings.memory_call_timeout_seconds,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {}
    try:
        return json.loads(out.decode(errors="replace").strip() or "{}")
    except json.JSONDecodeError:
        return {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_security_task(task: Task) -> bool:
    return bool(task.tags) and "vapt" in [str(t).lower() for t in task.tags]


SCANNERS = ("gitleaks", "osv-scanner", "trivy", "semgrep", "bandit", "pip-audit")
NOT_SCANNED = "Run the scanners listed. If none could run, say 'Not scanned' — never call the code clean."


def available_scanners(project_dir: str | None = None) -> list[str]:
    """Scanners on PATH, plus ``npm audit`` when the project has a package-lock.json. Only looks; never runs one."""
    found = [n for n in SCANNERS if shutil.which(n)]
    if project_dir and shutil.which("npm") and _has_regular_file(project_dir, "package-lock.json"):
        found.append("npm audit")
    return found


def _has_regular_file(folder: str, name: str) -> bool:
    try:
        d = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return False
    try:
        import stat

        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
        try:
            return stat.S_ISREG(os.fstat(fd).st_mode)
        finally:
            os.close(fd)
    except OSError:
        return False
    finally:
        os.close(d)


def build_security_prompt(branch: str, base: str = "main", scope: str = "changed",
                          project_dir: str | None = None) -> str:
    """VAPT/STQC security-review prompt (read-only) — mirrors the dashboard form. With a ``project_dir`` it also
    lists the scanners found and the project's unsafe Claude settings."""
    scanners = available_scanners(project_dir)
    config = claude_config_findings(project_dir)
    extra = f"Scanners found on this machine: {', '.join(scanners) if scanners else 'none'}. {NOT_SCANNED}\n\n"
    if config:
        extra += ("Unsafe Claude settings found in this project (include them in the report):\n"
                  + "\n".join(f"- {f['title']} ({f['file']}): {f['description']}" for f in config) + "\n\n")
    if scope == "full":
        gather = (
            "1. git fetch --all --quiet\n"
            "2. Create an ISOLATED read-only checkout without disturbing the working "
            f"tree: git worktree add --detach /tmp/vapt-{branch} origin/{branch}; review "
            "the code there (full-branch audit).\n"
            "3. When finished: git worktree remove --force /tmp/vapt-" + branch
        )
    else:
        gather = (
            "1. git fetch --all --quiet\n"
            f"2. Review the changes: git diff origin/{base}...origin/{branch} "
            "(and git diff --stat).\n"
            f"3. Read full file context with: git show origin/{branch}:<path>"
        )
    return (
        "SECURITY CODE REVIEW for STQC / VAPT compliance. NON-INTERACTIVE, READ-ONLY: "
        "never modify, commit, push, switch branches, or open/merge PRs. Do not ask "
        f"questions.\n\nTarget branch: \"{branch}\". Base: \"{base}\".\n\n"
        f"Gather the code (read-only git):\n{gather}\n\n"
        "Audit for OWASP Top 10 / CWE Top 25 vulnerabilities: injection (SQL/command/"
        "XSS/path), broken auth & access control (IDOR), crypto failures, hardcoded "
        "secrets, sensitive-data exposure, SSRF/XXE/deserialization, security "
        "misconfiguration, input-validation gaps, and vulnerable dependencies.\n\n"
        + extra +
        "Produce a report:\n## Summary\n- Overall risk rating and counts by severity.\n"
        "## Findings\nFor each: Severity | OWASP/CWE | file:line | Description | Impact | "
        "Remediation.\nIf reading the code finds none, say so and list what you inspected. Report only."
    )


def _fingerprint(project_id: str, title: str, file: str | None, category: str | None) -> str:
    norm = re.sub(r"\s+", " ", (title or "").strip().lower())
    key = f"{project_id}|{norm}|{(file or '').lower()}|{(category or '').lower()}"
    return hashlib.sha1(key.encode()).hexdigest()


_EXTRACT_PROMPT = (
    "Extract every distinct security finding from the report below. For each: a "
    "severity (critical/high/medium/low/info), OWASP/CWE category, CWE id, short "
    "title, file, line, one-line description, and one-line remediation. If the "
    "report states there are no issues, return an empty findings list.\n\nReport:\n\n"
)


async def extract_findings(task_id: str, user_id: str | None) -> None:
    """Runs Claude as `user_id` (the scan task's owner); skipped quietly if
    they have no Claude credential and there is no org fallback."""
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None or not task.project_id or not task.result_text:
            return
        if not is_security_task(task):
            return
        project_id = task.project_id
        report = task.result_text
        full_scan = "(full)" in (task.title or "")
        project = await s.get(Project, project_id)
        project_dir = project.directory if project else None
        env = await agent_env_or_none(
            s, user_id, project_env=project.env if project else None,
            purpose="findings extraction",
        )
    # The unsafe-settings scan is plain file reading: it counts even when Claude cannot be called.
    config_items = await asyncio.to_thread(claude_config_findings, project_dir)
    items = []
    if env is not None:
        data = await _extract_json(
            _EXTRACT_PROMPT + report[:12000], settings.memory_model, env
        )
        items = data.get("findings", []) if isinstance(data, dict) else []
    elif not config_items:
        return
    items = [*items, *config_items]

    now = _now()
    seen_fps: set[str] = set()
    async with SessionLocal() as s:
        for it in items:
            if not isinstance(it, dict) or not it.get("title"):
                continue
            sev = str(it.get("severity", "medium")).lower()
            if sev not in _SEV:
                sev = "medium"
            title = str(it["title"])[:300]
            file = (it.get("file") or None)
            category = (it.get("category") or None)
            fp = _fingerprint(project_id, title, file, category)
            seen_fps.add(fp)

            existing = (
                await s.execute(select(Finding).where(Finding.fingerprint == fp))
            ).scalar_one_or_none()
            if existing:
                existing.scans_count += 1
                existing.last_seen = now
                existing.last_scan_id = task_id
                existing.severity = sev
                existing.description = it.get("description")
                existing.remediation = it.get("remediation")
                # A finding marked fixed that reappears is re-opened.
                if existing.status == "fixed":
                    existing.status = "open"
                    existing.resolved_at = None
            else:
                s.add(
                    Finding(
                        project_id=project_id,
                        fingerprint=fp,
                        severity=sev,
                        category=category,
                        cwe=it.get("cwe"),
                        title=title,
                        file=file,
                        line=(str(it["line"])[:40] if it.get("line") else None),
                        description=it.get("description"),
                        remediation=it.get("remediation"),
                        status="open",
                        last_scan_id=task_id,
                        first_seen=now,
                        last_seen=now,
                    )
                )

        # Auto-resolve: on a FULL scan, open findings not seen this run are fixed.
        if full_scan:
            open_rows = (
                await s.execute(
                    select(Finding).where(
                        Finding.project_id == project_id, Finding.status == "open"
                    )
                )
            ).scalars().all()
            for f in open_rows:
                if f.fingerprint not in seen_fps:
                    f.status = "fixed"
                    f.resolved_at = now
        await s.commit()
