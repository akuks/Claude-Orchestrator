import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .constants import Priority, Status
from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    project: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(32), default=Status.QUEUED, index=True)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # Requested model ("" = let Claude Code use its own configured default).
    model: Mapped[str] = mapped_column(String(32), default="")
    # Actual model Claude Code resolved to, captured from the stream.
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_turns: Mapped[int] = mapped_column(Integer, default=25)
    # Hard spend cap passed to Claude (--max-budget-usd); None = uncapped.
    max_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Auto-retry attempt counter (0 = first run).
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    workspace_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Thread identity: a root task's root_id == its own id; follow-ups inherit the
    # parent's root_id. The feed lists only roots; the drawer lists thread steps.
    root_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # Claude Code session for this run, captured from the stream. A follow-up
    # sets resume_session_id to its parent's session_id so the CLI resumes it.
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set when this task was created by a schedule (for run history).
    schedule_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # Approvals (Phase 5): a task requiring approval waits in `awaiting_approval`
    # until a human approves (→ queued) or rejects (→ cancelled).
    requires_approval: Mapped[bool] = mapped_column(default=False)
    risk: Mapped[str] = mapped_column(String(16), default="info")  # info|warning|critical
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Results
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    task: Mapped["Task"] = relationship(back_populates="events")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    rel_path: Mapped[str] = mapped_column(String(1000))
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    task: Mapped["Task"] = relationship(back_populates="artifacts")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    directory: Mapped[str] = mapped_column(String(500))

    # Dashboard-managed project instructions (injected into task context, not
    # written into the repo, to avoid clobbering a real CLAUDE.md).
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str] = mapped_column(String(32), default="sonnet")
    # Optional monthly spend budget in USD; used for over-budget flags/alerts.
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # GitHub repo ("owner/name") this project maps to, for webhook triggers.
    github_repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Auto-run a security review when a PR is opened/updated on this repo.
    auto_review_prs: Mapped[bool] = mapped_column(default=False)

    # Living memory — auto-updated after each task, plus a prior copy for diffing.
    memory: Mapped[str] = mapped_column(Text, default="")
    memory_prev: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_enabled: Mapped[bool] = mapped_column(default=True)
    memory_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TaskSummary(Base):
    __tablename__ = "task_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(32), default="")
    max_turns: Mapped[int] = mapped_column(Integer, default=25)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    cron: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(default=True)

    # Task config fired on each tick.
    prompt: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(32), default="")
    max_turns: Mapped[int] = mapped_column(Integer, default=25)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    # Notifications: never | on_failure | always, sent to a Slack webhook.
    notify: Mapped[str] = mapped_column(String(16), default="never")
    notify_webhook: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # If set, each fired run waits in the approval inbox before executing.
    requires_approval: Mapped[bool] = mapped_column(default=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Finding(Base):
    """A security finding tracked across scans (per project, keyed by fingerprint)."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    severity: Mapped[str] = mapped_column(String(16), default="medium")
    category: Mapped[str | None] = mapped_column(String(160), nullable=True)
    cwe: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    line: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # open | fixed | accepted | false_positive
    status: Mapped[str] = mapped_column(String(20), default="open")
    scans_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_scan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    transport: Mapped[str] = mapped_column(String(16), default="stdio")  # stdio | http
    scope: Mapped[str] = mapped_column(String(16), default="team")  # team | user | project
    project: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)

    # stdio transport
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list] = mapped_column(JSON, default=list)
    # http transport
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Encrypted {"env": {...}, "headers": {...}} — never returned in plaintext.
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    status_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    policies: Mapped[list["McpToolPolicy"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )


class McpToolPolicy(Base):
    __tablename__ = "mcp_tool_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(200))
    classification: Mapped[str] = mapped_column(String(16), default="write")  # read|write|dangerous
    action: Mapped[str] = mapped_column(String(20), default="require_approval")
    # action: auto_approve | require_approval | block
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    server: Mapped["McpServer"] = relationship(back_populates="policies")


class McpCall(Base):
    __tablename__ = "mcp_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    server: Mapped[str] = mapped_column(String(120), index=True)
    tool: Mapped[str] = mapped_column(String(200))
    is_error: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
