"""Security-review findings: extract structured findings from a scan's report
and reconcile them into the per-project findings tracker.

After a security review task finishes, a cheap Claude call parses the human
report into JSON findings. Each is fingerprinted (project + title + file +
category) so the same issue is tracked across scans — new / recurring / fixed.
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import Finding, Task

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


async def _extract_json(prompt: str, model: str) -> dict:
    """Run Claude with --json-schema so the output is guaranteed valid JSON."""
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


async def extract_findings(task_id: str) -> None:
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None or not task.project_id or not task.result_text:
            return
        if not is_security_task(task):
            return
        project_id = task.project_id
        report = task.result_text
        full_scan = "(full)" in (task.title or "")

    data = await _extract_json(_EXTRACT_PROMPT + report[:12000], settings.memory_model)
    items = data.get("findings", []) if isinstance(data, dict) else []

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
