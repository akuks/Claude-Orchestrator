"""Security-review findings: extract structured findings from a scan's report
and reconcile them into the per-project findings tracker.

After a security review task finishes, a cheap Claude call parses the human
report into JSON findings. Each is fingerprinted (project + title + file +
category) so the same issue is tracked across scans — new / recurring / fixed.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .memory import _run_claude
from .models import Finding, Task

_SEV = {"critical", "high", "medium", "low", "info"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_security_task(task: Task) -> bool:
    return bool(task.tags) and "vapt" in [str(t).lower() for t in task.tags]


def _fingerprint(project_id: str, title: str, file: str | None, category: str | None) -> str:
    norm = re.sub(r"\s+", " ", (title or "").strip().lower())
    key = f"{project_id}|{norm}|{(file or '').lower()}|{(category or '').lower()}"
    return hashlib.sha1(key.encode()).hexdigest()


def _parse_json_array(text: str) -> list:
    if not text:
        return []
    # Strip code fences and isolate the outermost JSON array.
    text = re.sub(r"```(json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


_EXTRACT_PROMPT = (
    "You are a parser. Extract every security finding from the report below into a "
    "JSON array. Output ONLY valid JSON — no prose, no markdown fences. Each element:\n"
    '{"severity":"critical|high|medium|low|info","category":"OWASP/CWE label or null",'
    '"cwe":"CWE-89 or null","title":"short title","file":"path or null",'
    '"line":"line/range or null","description":"one line","remediation":"one line"}\n'
    "If the report states there are no issues, output []. Report:\n\n"
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

    raw = await _run_claude(_EXTRACT_PROMPT + report[:12000], settings.memory_model, None)
    items = _parse_json_array(raw)

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
