import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Index,
    UniqueConstraint,
    text,
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    false,
    true,
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
    # Which attempt of the stage this task belongs to (gate retries, phase 5a).
    stage_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    outputs: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # typed outputs (phase 5c)
    # The launch-list item this task is linked to (phase 5b); same project as the task.
    launch_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("launch_items.id", ondelete="SET NULL"), nullable=True, index=True)
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

    # Resume and handoff (phase 3a). origin: user | agent | guard | continue. The rest is
    # written by later tasks (heartbeat/head_at_start: run capture; credential_ref:
    # compose; notes, run_pgid, run_proc_start, recoveries, recovery: recovery).
    origin: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    brief: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # CAN/CANNOT change brief (phase 4c)
    brief_bounds: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)  # enclosing briefs' scopes
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_pgid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_proc_start: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recoveries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recovery: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    head_at_start: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Token care (phase 3b): this step was queued by the automatic fresh continue.
    auto_continued: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Claude plan limits (phase 7c): a queued task does not start before not_before, and wait_reason is
    # the plain sentence shown while it waits ("Paused — your Claude plan limit was reached. ...").
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wait_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # plan-limit hits in a row (reset when a run gets past the limit); the third stops the task
    plan_requeues: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

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
    # The repository's first commit when last seen (phase 7d): a different one later means the folder was cloned again.
    root_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)

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
    # co-guard policy (Phase 2): "own" | "external" (an external repo's text is
    # data: the data line is added and its rules file is never read);
    # quarantine holds the repo's install/build/test/run commands;
    # on_no_answer "deny" | "deny_and_resume"; readonly_paths [{path, reason}]
    # (absolute, or relative to `directory`); reference_project_ids: other
    # projects mounted read-only. Lists are never NULL.
    trust: Mapped[str] = mapped_column(String(16), default="own", server_default="own")
    quarantine: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    on_no_answer: Mapped[str] = mapped_column(String(20), default="deny", server_default="deny")
    readonly_paths: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    reference_project_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # Token care (phase 3b): context size (tokens) at which a step is handed to a
    # fresh session; null = off. Allowed 50000-900000.
    fresh_session_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # High-water mark of recorded decision numbers (phase 4a); never decreases.
    decision_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # High-water mark of launch-list item numbers (phase 5b); never decreases.
    launch_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Licence hint (phase 6a): {id, name, kind, file, hash, checked_at}; NULL = never checked.
    licence: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # Code map (phase 6b): keep an index of this project's code (maintainers set it).
    code_map: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Design skills (phase 6c): tell agents on a project with a user interface to use them; optional brand skill.
    ui_skills: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    brand_skill: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProjectIndex(Base):
    """The code map index record of a project (phase 6b): one row per project, made on the first job.
    status never | running | ok | failed; `sha` is the commit the index was built at."""

    __tablename__ = "project_indexes"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="never", server_default="never")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_try_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # Phase 2: the hook's grant_key of the call (sha256 hex); resume_until set
    # when the project's on_no_answer is deny_and_resume; delivery "inline" |
    # "resume" | "dropped" (approved while a step was active, and that step was
    # cancelled: no follow-up); scope of the approval "once" | "run"; the follow-up task an
    # approval in the resume phase created or joined; the grants it made.
    grant_key: Mapped[str] = mapped_column(String(64), default="", server_default="")
    resume_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery: Mapped[str] = mapped_column(String(16), default="inline", server_default="inline")
    scope: Mapped[str | None] = mapped_column(String(8), nullable=True)
    followup_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    grant_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")


class ActionRule(Base):
    """A custom co-guard rule (Phase 2): scope "org" (admins) or "project"
    (its maintainers). kind "confirm" (needs approval) | "never" (hard deny);
    match "text" (case-insensitive substring) | "regex". Its policy id is
    ``<scope>.<slug>``; the slug never changes after creation."""

    __tablename__ = "action_rules"
    __table_args__ = (
        UniqueConstraint("scope", "project_id", "slug", name="uq_action_rules_slug"),
        # NULLs are distinct in a unique constraint: org slugs need their own index.
        Index(
            "uq_action_rules_org_slug", "scope", "slug", unique=True,
            sqlite_where=text("project_id IS NULL"), postgresql_where=text("project_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(16))
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    match: Mapped[str] = mapped_column(String(16))
    pattern: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class GuardOrgSettings(Base):
    """The org's co-guard settings (a single row, id 1): built-ins switched
    off (only rules.DISABLEABLE) and dev database patterns; also the org's
    model tiers and (phase 7d) the tidying switch."""

    __tablename__ = "guard_org_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, autoincrement=False)
    disabled_rules: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    dev_db_patterns: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    ground_rules: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    model_tiers: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # phase 7d: tidying old run history ({"enabled", "days", "last_run", "last_count"}; app.retention)
    retention: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TaskHandoff(Base):
    """Where a thread stands (one row per thread, root_id unique): a structured
    `content` and its rendered `text`, with the previous version kept so an edit
    can be undone. status open | closed."""

    __tablename__ = "task_handoffs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    root_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(8), default="open", server_default="open")
    reason: Mapped[str] = mapped_column(String(16), default="", server_default="")
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    content: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    prev_content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prev_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class TaskUsage(Base):
    """A step's token figures (phase 3b), one row per step: the latest
    main-thread context size, its peak, the running cost estimate (the result's
    total_cost_usd once finished), the breakdown (UsageTracker.snapshot) and the
    bands co-guard fired ([{"band", "ts"}])."""

    __tablename__ = "task_usage"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    root_id: Mapped[str] = mapped_column(String(32), index=True)
    context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_context: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    bands: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PlanLimitStatus(Base):
    """The latest Claude plan-limit reading per credential owner (phase 7c): a user id, or "org" for the
    org fallback key. ``raw`` is the stream's rate_limit_info (capped at 2 KB); ``credential_ref`` is the
    credential's keyed fingerprint (never the credential)."""

    __tablename__ = "plan_limit_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    credential_owner: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    resets_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    api_key: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    credential_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # a rejected hold beyond 6 hours counts in full only once confirmed; run_ref: the run that wrote it
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    run_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class TaskQuestion(Base):
    """A question an agent needs a person to answer (phase 3b Questions inbox).
    source agent (the agent token) | person (filed by a person) | final_text; status open | answered |
    dismissed."""

    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    root_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocks: Mapped[str | None] = mapped_column(Text, nullable=True)
    reversible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="agent")
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    followup_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunAsk(Base):
    """A person's private question about a run, answered from the run's record (phase 3c).
    status pending | answered | failed."""

    __tablename__ = "run_asks"
    __table_args__ = (
        Index("ix_run_asks_user_id_created_at", "user_id", "created_at"),
        # One pending ask per person per run, enforced by the database (a double-click files one).
        Index("uq_run_asks_one_pending", "task_id", "user_id", unique=True,
              sqlite_where=text("status = 'pending'"), postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectDecision(Base):
    """A recorded project decision (phase 4a): D-<number>, naming the files it touches.
    kind decision | rule | lesson | reference; status active | retired | superseded.
    `version` is an optimistic lock."""

    __tablename__ = "project_decisions"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_project_decisions_project_number"),
        Index("ix_project_decisions_project_id_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12), default="decision", server_default="decision")
    title: Mapped[str] = mapped_column(String(200))
    why: Mapped[str] = mapped_column(Text, default="", server_default="")
    see: Mapped[str | None] = mapped_column(String(300), nullable=True)
    touches: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    source: Mapped[str] = mapped_column(String(300), default="", server_default="")
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    stale_flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class DecisionShowing(Base):
    """A task that was shown a recorded decision (phase 8a), one row per task and decision number (the first
    showing's time kept), and what happened to that task afterwards. outcome None (not settled, or cancelled)
    | passed | completed | fixed | failed; retried: the task had automatic retries."""

    __tablename__ = "decision_showings"
    __table_args__ = (
        UniqueConstraint("task_id", "decision_number", name="uq_decision_showings_task_number"),
        Index("ix_decision_showings_project_number", "project_id", "decision_number"),
        Index("ix_decision_showings_project_shown", "project_id", "shown_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    decision_number: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)
    retried: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectLaunch(Base):
    """A project's launch settings (phase 5b): one row per project, created on first write.
    `version` is an optimistic lock."""

    __tablename__ = "project_launches"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    launch_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    critical_paths: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    raise_risk: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class LaunchItem(Base):
    """One launch-list item (phase 5b), shown #<number>. section before | after;
    status open | started | done | dropped. `version` is an optimistic lock."""

    __tablename__ = "launch_items"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_launch_items_project_number"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(String(8), default="before", server_default="before")
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    added_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class DecisionProposal(Base):
    """A suggested decision awaiting review (phase 4b).
    source run | lesson | answer | agent | person; status pending | accepted | rejected."""

    __tablename__ = "decision_proposals"
    __table_args__ = (
        UniqueConstraint("project_id", "dedupe_key", name="uq_decision_proposals_project_dedupe"),
        Index("ix_decision_proposals_project_id_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(12), default="decision", server_default="decision")
    title: Mapped[str] = mapped_column(String(200))
    why: Mapped[str] = mapped_column(Text, default="", server_default="")
    touches: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    evidence: Mapped[str] = mapped_column(String(300), default="", server_default="")
    dedupe_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    proposed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ActionGrant(Base):
    """A person's approval that covers later calls (Phase 2). scope "run": the
    whole thread (root_id) until expires_at; "once": one call of task_id.
    key_kind "rule" (key "*"), "tool" (an MCP tool name) or "command" (the
    call's grant_key). One row per rule id."""

    __tablename__ = "action_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    root_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(8))
    rule_id: Mapped[str] = mapped_column(String(100))
    key_kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


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
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True)  # strongest | standard | light
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
    # serverInfo.version from the last successful handshake (0023); None when the tool did not say
    server_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # co-guard (Phase 2): "dev" | "prod" | "unknown" (unknown counts as prod for
    # db-write); default_action for tools with no policy row: auto_approve |
    # require_approval | block. Servers that existed before 0007 keep
    # auto_approve (their old behaviour); new ones need approval.
    environment: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    default_action: Mapped[str] = mapped_column(
        String(20), default="require_approval", server_default="auto_approve"
    )
    # "Start in the project folder" (0019): off (the default, and every server
    # from before 0019) starts a stdio tool from a private, empty folder.
    start_in_project: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

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
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # snapshot of the workflow budget
    # Projects the run's stages target (snapshot at run time), used for visibility.
    project_ids: Mapped[list] = mapped_column(JSON, default=list)
    # Per-stage timing, keyed by str(stage_index): {"started_at", "finished_at"} iso strings (phase 5a).
    stage_state: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # Run log: [{"at": iso, "text": str}], newest 200 (see gates/runlog.py).
    log: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkflowGateRun(Base):
    """One execution of one gate command (phase 5a)."""

    __tablename__ = "workflow_gate_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    stage_index: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    handout: Mapped[str] = mapped_column(String(32))
    command: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(8))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))
    runner: Mapped[str | None] = mapped_column(String(32), nullable=True)
    passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failing: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    flaky: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    code_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    log_name: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
    # Update check (phase 6a): the remote HEAD last seen, and when.
    latest_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class UserPreferences(Base):
    """A person's own standing preferences (phase 8b): added to the system prompt of every task they start.
    Only the person writes it (``/me/preferences``); ``items`` is a list of ``{id, text, on}``."""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class OrgSetting(Base):
    """Admin-managed org-wide secret settings (e.g. "fallback_anthropic_api_key"),
    stored encrypted."""

    __tablename__ = "org_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
