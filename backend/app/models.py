import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Index,
    UniqueConstraint,
    text,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .constants import Priority, Status
from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # The agent that ran this task, and its role prompt (--append-system-prompt).
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Workflow linkage: set when this task belongs to a workflow run's stage.
    workflow_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    stage_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether this task may open outbound SSH/SCP/SFTP. False → ssh/scp/sftp are
    # added to --disallowedTools (deny wins even under bypassPermissions). Ad-hoc
    # tasks default True; agent tasks inherit the agent's allow_remote flag.
    allow_remote: Mapped[bool] = mapped_column(default=True)
    # When true, file-mutating and git-write tools are blocked via
    # --disallowedTools (tool-enforced read-only, e.g. a reviewer/auditor).
    read_only: Mapped[bool] = mapped_column(default=False)

    # Approvals (Phase 5): a task requiring approval waits in `awaiting_approval`
    # until a human approves (→ queued) or rejects (→ cancelled).
    requires_approval: Mapped[bool] = mapped_column(default=False)
    risk: Mapped[str] = mapped_column(String(16), default="info")  # info|warning|critical
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped["Task"] = relationship(back_populates="artifacts")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # Per-project environment for this project's task subprocesses (toolchain
    # scoping). Merged over the inherited env. The special key PATH_PREPEND is
    # prepended to PATH (e.g. a project-specific PHP/Node version) rather than
    # replacing it.
    env: Mapped[dict] = mapped_column(JSON, default=dict)

    # Living memory — auto-updated after each task, plus a prior copy for diffing.
    memory: Mapped[str] = mapped_column(Text, default="")
    memory_prev: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_enabled: Mapped[bool] = mapped_column(default=True)
    memory_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    archived: Mapped[bool] = mapped_column(default=False)
    # Whose credentials webhook-triggered work runs on; None = the project creator.
    automation_owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # co-guard: check every tool call of this project's tasks; how long a call
    # that needs a person's approval waits for one (60-900 s).
    guard_enabled: Mapped[bool] = mapped_column(default=True)
    approval_wait_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ActionApproval(Base):
    """A running task's request (filed by co-guard's hook with the task's agent
    token) for a person to allow one tool call. pending -> approved | rejected
    | expired; a pending row past expires_at, or whose task stopped running,
    reads as expired. Each row answers exactly one waiting hook (one-shot)."""

    __tablename__ = "action_approvals"
    # One pending request per (task, idempotency key); decided ones may repeat it.
    __table_args__ = (
        Index(
            "uq_action_approvals_pending_key", "task_id", "idempotency_key", unique=True,
            sqlite_where=text("status = 'pending'"), postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    # Effective project (the task's own, or its thread root's) and the task's
    # owner, captured at filing: the decide list filters on them in SQL.
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    requested_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    tool: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    cwd: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    command_hash: Mapped[str] = mapped_column(String(128), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskSummary(Base):
    __tablename__ = "task_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Agent(Base):
    """A reusable, governed agent profile: a role (system prompt) + model +
    budget + optional default project. Run it against any project or its default."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, default="")  # the role
    default_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)  # default task

    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(32), default="")
    max_turns: Mapped[int] = mapped_column(Integer, default=25)
    max_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # When true, every run of this agent is approval-gated at the task level
    # (e.g. a remediation agent that changes remote state).
    requires_approval: Mapped[bool] = mapped_column(default=False)
    # When true, this agent may open outbound SSH/SCP/SFTP (remote login).
    # Off by default: SSH is opt-in per agent.
    allow_remote: Mapped[bool] = mapped_column(default=False)
    # When true, the agent cannot modify code — Edit/Write/commit/push are blocked
    # via --disallowedTools (tool-enforced read-only for reviewers/auditors).
    read_only: Mapped[bool] = mapped_column(default=False)
    # Organisational folder path for the UI (slash-separated, e.g. "Canonizer/Dev").
    # Empty = ungrouped. Purely for display grouping.
    folder: Mapped[str] = mapped_column(String(200), default="")
    # When true, this agent's tasks get a live registry of the other agents + the
    # local API injected, so it can analyse work and delegate by dispatching
    # child tasks to specialists (a coordinator / tech-lead).
    coordinator: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(32), default="")
    max_turns: Mapped[int] = mapped_column(Integer, default=25)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # If set, the schedule runs this agent (its role/model/budget) instead of the
    # inline prompt config above.
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_scan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    server: Mapped["McpServer"] = relationship(back_populates="policies")


class McpCall(Base):
    __tablename__ = "mcp_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    server: Mapped[str] = mapped_column(String(120), index=True)
    tool: Mapped[str] = mapped_column(String(200))
    is_error: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Workflow(Base):
    """A saved, reusable pipeline: ordered stages, each with one or more agent
    steps that run in parallel. Run it on an input (PR/issue/description) and the
    runner advances stage by stage.

    stages shape (JSON):
      [{"name": "Develop", "items": [{"agent": "<id|name>", "prompt": "..."}]}, ...]
    Each item prompt may contain {{input}}, replaced with the run's input.
    """

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkflowRun(Base):
    """One execution of a workflow on a given input."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workflow_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))  # snapshot of workflow name
    input: Mapped[str] = mapped_column(Text, default="")
    # pending | running | paused | completed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    current_stage: Mapped[int] = mapped_column(Integer, default=0)
    stages: Mapped[list] = mapped_column(JSON, default=list)  # snapshot at run time
    # Projects the run's stages target (snapshot at run time), used for visibility.
    project_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SkillPack(Base):
    """An installed Claude Code plugin (skill pack), loaded into agent tasks via
    --plugin-dir. Enable globally or scope to a project; tasks for that project
    (or all tasks, if global) get the plugin's skills available."""

    __tablename__ = "skill_packs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # git repo
    local_path: Mapped[str] = mapped_column(String(500))  # dir passed to --plugin-dir
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # "global" applies to every task; "project" applies only to its project_id.
    scope: Mapped[str] = mapped_column(String(16), default="global")
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContextDoc(Base):
    """A curated knowledge document in the Context Store (runbooks, conventions,
    architecture, decisions). Loaded into agent tasks in tiers: the one-line
    abstract (L0) is always injected in an index; the full body (L2) is written
    to a read-only snapshot dir the agent opens with Read only when relevant.
    Complements project memory (auto-captured) — this is human-curated."""

    __tablename__ = "context_docs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Hierarchical path, e.g. "runbooks/deploy.md". Unique per scope+project.
    path: Mapped[str] = mapped_column(String(300), index=True)
    title: Mapped[str] = mapped_column(String(200))
    abstract: Mapped[str] = mapped_column(String(400), default="")  # L0
    body: Mapped[str] = mapped_column(Text, default="")  # L2
    enabled: Mapped[bool] = mapped_column(default=True)
    # "global" applies to every task; "project" only to its project_id's tasks
    # (and overrides a global doc with the same path).
    scope: Mapped[str] = mapped_column(String(16), default="global")
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Stored normalized (stripped + lowercased) so the unique index is case-insensitive.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="member")  # "admin" | "member"
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @validates("email")
    def _normalize_email(self, _key, value: str) -> str:
        # Enforced at the model so no code path can bypass case-insensitive uniqueness.
        return value.strip().lower()


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    # sha256 hex of the raw session id; the raw id only ever lives in the cookie.
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(16))  # "agent-task" | "personal"
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)


class ProjectMember(Base):
    """A grant of a role on a project to a user or a team."""

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "subject_type", "subject_id", name="uq_project_member"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    subject_type: Mapped[str] = mapped_column(String(8))  # "user" | "team"
    subject_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "contributor" | "maintainer"


class UserCredential(Base):
    """A user's personal credentials. Secrets live only in the *_enc vault blobs
    (app.crypto); the API never returns them."""

    __tablename__ = "user_credentials"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    claude_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "oauth_token" | "api_key"
    claude_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    git_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class OrgSetting(Base):
    """Admin-managed org-wide secret settings (e.g. "fallback_anthropic_api_key"),
    stored encrypted."""

    __tablename__ = "org_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
