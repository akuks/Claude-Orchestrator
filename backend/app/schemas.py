from datetime import date, datetime
import re
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class InputFile(BaseModel):
    name: str
    content_base64: str


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    title: Optional[str] = None
    project: Optional[str] = None
    project_id: Optional[str] = None
    priority: str = "normal"
    tags: list[str] = Field(default_factory=list)
    model: Optional[str] = None
    max_turns: int = Field(default=25, ge=1, le=200)
    max_budget_usd: Optional[float] = Field(default=None, ge=0)
    claude_md: Optional[str] = None
    input_files: list[InputFile] = Field(default_factory=list)
    requires_approval: bool = False
    brief: Optional[Any] = None  # checked by clean_brief (a plain-text 422), not by Pydantic
    launch_item_id: Optional[str] = None  # an item of the project's launch list (phase 5b)


class TaskOut(BaseModel):
    id: str
    title: str
    prompt: str
    project: Optional[str]
    project_id: Optional[str]
    status: str
    priority: str
    tags: list[str]
    model: Optional[str]
    model_used: Optional[str]
    max_turns: int
    max_budget_usd: Optional[float] = None
    attempt: int = 0
    stage_attempt: int = 0
    launch_item_id: Optional[str] = None
    outputs: Optional[dict] = None  # typed outputs parsed from the result (phase 5c)
    exit_code: Optional[int]
    error: Optional[str]
    result_text: Optional[str]
    num_turns: Optional[int]
    total_cost_usd: Optional[float]
    duration_ms: Optional[int]
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    parent_task_id: Optional[str]
    root_id: Optional[str]
    session_id: Optional[str]
    schedule_id: Optional[str]
    agent_id: Optional[str] = None
    allow_remote: bool = True
    read_only: bool = False
    workflow_run_id: Optional[str] = None
    stage_index: Optional[int] = None
    requires_approval: bool = False
    risk: str = "info"
    decision_reason: Optional[str] = None
    created_by: Optional[str] = None
    decided_by: Optional[str] = None
    thread_count: int = 1  # number of steps in this task's thread
    origin: str = "user"
    notes: Optional[str] = None
    notes_updated_at: Optional[datetime] = None
    recoveries: int = 0
    recovery: Optional[dict] = None
    brief: Optional[dict] = None
    brief_bounds: list = []
    # per viewer, set on the task and thread reads only: may the viewer follow up / Continue, and own the session
    can_manage: bool = False
    can_resume: bool = False
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}

    @field_validator("brief_bounds", mode="before")
    @classmethod
    def _bounds_none(cls, v):
        return [] if v is None else v


class TaskLaunchLink(BaseModel):
    launch_item_id: Optional[str]  # required; null unlinks


class FollowupCreate(BaseModel):
    prompt: str = Field(min_length=1)


class ContinueCreate(BaseModel):
    # auto | resume | fresh (checked by the route: a bad mode is 400)
    mode: str | None = "auto"


class QuestionCreate(BaseModel):
    # raw size caps (422) before any work; the stored limits are checked after redaction and cleaning
    # (questions.service.clean_fields): 422
    question: str = Field(max_length=10000)
    options: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=20)
    recommendation: Optional[str] = Field(default=None, max_length=2000)
    blocks: Optional[str] = Field(default=None, max_length=3000)
    reversible: Optional[bool] = None


class QuestionAnswer(BaseModel):
    # exactly one of: a free answer, or accept=true (checked by the route: 422)
    answer: Optional[str] = Field(default=None, max_length=10000)
    accept: Optional[bool] = None
    suggest_decision: bool = False


class QuestionLaunchItem(BaseModel):
    number: int
    title: str


class QuestionOut(BaseModel):
    id: str
    task_id: str
    root_id: str
    project_id: Optional[str]
    question: str
    options: list[str]
    recommendation: Optional[str]
    blocks: Optional[str]
    reversible: Optional[bool]
    source: str
    status: str
    answer: Optional[str]
    answered_by: Optional[str]
    answered_at: Optional[datetime]
    followup_task_id: Optional[str]
    created_at: datetime
    task_title: Optional[str] = None
    can_answer: bool = False
    launch_item: Optional[QuestionLaunchItem] = None

    model_config = {"from_attributes": True}


class ArtifactOut(BaseModel):
    id: int
    filename: str
    rel_path: str
    size: int
    mime: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    seq: int
    type: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class StatsOut(BaseModel):
    tasks_today: int
    running: int
    queued: int
    completed_today: int
    success_rate: float
    avg_duration_ms: Optional[float]
    by_status: dict


# ---- MCP management --------------------------------------------------------


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    transport: str = "stdio"  # stdio | http
    scope: str = "team"  # team | user | project
    project: Optional[str] = None
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    # co-guard: dev | prod | unknown; action for tools with no policy row
    environment: str = "unknown"
    default_action: str = "require_approval"
    start_in_project: bool = False


class McpServerUpdate(BaseModel):
    scope: Optional[str] = None
    project: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    url: Optional[str] = None
    env: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None
    environment: Optional[str] = None
    default_action: Optional[str] = None
    start_in_project: Optional[bool] = None


class McpToolInfo(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class McpServerOut(BaseModel):
    id: str
    name: str
    transport: str
    scope: str
    project: Optional[str]
    command: Optional[str]
    args: list
    url: Optional[str]
    enabled: bool
    status: str
    status_detail: Optional[str]
    tools: list
    has_env: bool = False
    has_headers: bool = False
    environment: str = "unknown"
    default_action: str = "require_approval"
    start_in_project: bool = False
    last_checked_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class PolicyIn(BaseModel):
    tool_name: str
    classification: str = "write"
    action: str = "require_approval"


class PolicyOut(BaseModel):
    id: int
    server_id: str
    tool_name: str
    classification: str
    action: str

    model_config = {"from_attributes": True}


class McpProbeOut(BaseModel):
    ok: Optional[bool]
    tools: list
    error: Optional[str] = None


class McpObservabilityOut(BaseModel):
    window_days: int
    total_calls: int
    total_errors: int
    failure_rate: float
    by_server: list
    top_tools: list


# ---- Security findings ----------------------------------------------------


class FindingOut(BaseModel):
    id: int
    project_id: str
    severity: str
    category: Optional[str]
    cwe: Optional[str]
    title: str
    file: Optional[str]
    line: Optional[str]
    description: Optional[str]
    remediation: Optional[str]
    status: str
    scans_count: int
    first_seen: datetime
    last_seen: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}


class FindingUpdate(BaseModel):
    status: Optional[str] = None  # open | fixed | accepted | false_positive
    severity: Optional[str] = None


# ---- Projects & memory (Phase 3) ------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    directory: Optional[str] = None  # defaults to projects_dir/slug
    instructions: Optional[str] = None
    default_model: str = "sonnet"
    memory_enabled: bool = True
    budget_usd: Optional[float] = None
    github_repo: Optional[str] = None
    auto_review_prs: bool = False
    env: dict[str, str] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    directory: Optional[str] = None
    instructions: Optional[str] = None
    default_model: Optional[str] = None
    memory_enabled: Optional[bool] = None
    memory: Optional[str] = None  # manual memory edit
    archived: Optional[bool] = None
    budget_usd: Optional[float] = None
    github_repo: Optional[str] = None
    auto_review_prs: Optional[bool] = None
    env: Optional[dict[str, str]] = None
    # Who webhook-triggered work runs as; null = the project creator.
    automation_owner_id: Optional[str] = None
    # co-guard (maintainer-editable); null means "leave it".
    guard_enabled: Optional[bool] = None
    approval_wait_seconds: Optional[int] = Field(None, ge=60, le=900)
    # co-guard policy (Phase 2, maintainer-editable, validated by the route).
    trust: Optional[str] = None
    quarantine: Optional[bool] = None
    on_no_answer: Optional[str] = None
    readonly_paths: Optional[list["ReadonlyPathIn"]] = None
    reference_project_ids: Optional[list[str]] = None
    # token care (phase 3b, maintainer-editable): null turns the fresh-session hand-over off.
    fresh_session_tokens: Optional[int] = Field(None, ge=50000, le=900000)
    # code map (phase 6b, maintainer-editable): needs a project directory
    code_map: Optional[bool] = None


class ReadonlyPathIn(BaseModel):
    path: str = Field(max_length=1000)
    reason: Optional[str] = Field(None, max_length=2000)


ProjectUpdate.model_rebuild()


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    directory: str
    instructions: Optional[str]
    default_model: str
    memory: str
    memory_enabled: bool
    memory_updated_at: Optional[datetime]
    archived: bool
    created_at: datetime
    budget_usd: Optional[float] = None
    github_repo: Optional[str] = None
    auto_review_prs: bool = False
    env: dict = {}
    automation_owner_id: Optional[str] = None
    guard_enabled: bool = True
    approval_wait_seconds: int = 300
    trust: str = "own"
    quarantine: bool = False
    on_no_answer: str = "deny"
    readonly_paths: list = []
    reference_project_ids: list = []
    fresh_session_tokens: Optional[int] = None
    # create/import only: why suggest_trust chose `trust`
    trust_reason: Optional[str] = None
    code_map: bool = False
    licence: Optional[dict] = None
    licence_badge: Optional[dict] = None  # {label, tone}, computed by the server
    task_count: int = 0
    total_cost_usd: float = 0.0
    my_role: Optional[str] = None

    @field_validator("env", mode="before")
    @classmethod
    def _env_default(cls, v):
        return v or {}  # existing rows store NULL

    @field_validator("readonly_paths", "reference_project_ids", mode="before")
    @classmethod
    def _list_default(cls, v):
        return v if isinstance(v, list) else []

    model_config = {"from_attributes": True}


class ApprovalOut(TaskOut):
    # Blast-radius context so the approver can decide safely before running.
    context: dict = {}


class MemoryOut(BaseModel):
    memory: str
    memory_prev: Optional[str]
    memory_updated_at: Optional[datetime]


class TaskSummaryOut(BaseModel):
    id: int
    task_id: str
    title: str
    summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- Templates & schedules (Phase 4) --------------------------------------


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    prompt: str = Field(min_length=1)
    project_id: Optional[str] = None
    model: Optional[str] = None
    max_turns: int = Field(default=25, ge=1, le=200)
    priority: str = "normal"
    tags: list[str] = Field(default_factory=list)


class TemplateOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    prompt: str
    project_id: Optional[str]
    model: str
    max_turns: int
    priority: str
    tags: list
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    system_prompt: str = ""  # the agent's role/instructions
    default_prompt: Optional[str] = None  # default task input
    project_id: Optional[str] = None  # optional default project (overridable)
    model: Optional[str] = None
    tier: Optional[str] = None  # strongest | standard | light (the org's model map); model overrides it
    max_turns: int = Field(default=25, ge=1, le=200)
    max_budget_usd: Optional[float] = Field(default=None, ge=0)
    priority: str = "normal"
    tags: list[str] = Field(default_factory=list)
    requires_approval: bool = False  # gate every run at the task level
    allow_remote: bool = False  # permit outbound SSH/SCP/SFTP
    read_only: bool = False  # tool-enforced read-only (block Edit/Write/commit/push)
    folder: str = ""  # UI grouping path, e.g. "Canonizer/Dev"
    coordinator: bool = False  # can analyse + delegate to other agents


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    default_prompt: Optional[str] = None
    project_id: Optional[str] = None
    model: Optional[str] = None
    tier: Optional[str] = None
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    priority: Optional[str] = None
    tags: Optional[list[str]] = None
    requires_approval: Optional[bool] = None
    allow_remote: Optional[bool] = None
    read_only: Optional[bool] = None
    folder: Optional[str] = None
    coordinator: Optional[bool] = None


class AgentOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    system_prompt: str
    default_prompt: Optional[str]
    project_id: Optional[str]
    model: str
    tier: Optional[str] = None
    max_turns: int
    max_budget_usd: Optional[float]
    priority: str
    tags: list
    requires_approval: bool = False
    allow_remote: bool = False
    read_only: bool = False
    folder: str = ""
    coordinator: bool = False
    lint: list[str] = []  # non-blocking warnings (model / description), set by the agents router
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentRun(BaseModel):
    prompt: Optional[str] = None  # overrides the agent's default_prompt
    project_id: Optional[str] = None  # overrides the agent's default project
    input_files: list[InputFile] = Field(default_factory=list)  # attached task files
    parent_task_id: Optional[str] = None  # a coordinator dispatching a child
    brief: Optional[Any] = None


class AgentBulkRun(BaseModel):
    # One task per prompt — upload a task list and the agent works through each.
    prompts: list[str] = Field(min_length=1)
    project_id: Optional[str] = None


class DispatchItem(BaseModel):
    agent: str  # agent id OR exact name
    prompt: str = Field(min_length=1)
    brief: Optional[Any] = None


class DispatchRequest(BaseModel):
    # A coordinator fans out: create one child task per item, linked to parent.
    items: list[DispatchItem] = Field(min_length=1, max_length=8)
    parent_task_id: Optional[str] = None
    project_id: Optional[str] = None  # default project for children


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cron: str = Field(min_length=1)
    prompt: Optional[str] = None  # required unless agent_id is set
    agent_id: Optional[str] = None
    project_id: Optional[str] = None
    model: Optional[str] = None
    max_turns: int = Field(default=25, ge=1, le=200)
    priority: str = "normal"
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    notify: str = "never"  # never | on_failure | always
    notify_webhook: Optional[str] = None
    requires_approval: bool = False


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    prompt: Optional[str] = None
    agent_id: Optional[str] = None
    project_id: Optional[str] = None
    model: Optional[str] = None
    max_turns: Optional[int] = None
    priority: Optional[str] = None
    tags: Optional[list[str]] = None
    enabled: Optional[bool] = None
    notify: Optional[str] = None
    notify_webhook: Optional[str] = None
    requires_approval: Optional[bool] = None


class ScheduleOut(BaseModel):
    id: str
    name: str
    cron: str
    enabled: bool
    prompt: Optional[str] = None
    agent_id: Optional[str] = None
    project_id: Optional[str]
    model: str
    max_turns: int
    priority: str
    tags: list
    notify: str
    notify_webhook: Optional[str]
    requires_approval: bool = False
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    created_at: datetime
    # Owner (runs are created, approved and billed as this user).
    created_by: Optional[str] = None
    # Why the scheduler skips it right now (scheduler.owner_block_reason).
    blocked_reason: Optional[str] = None

    model_config = {"from_attributes": True}


class ServerEntry(BaseModel):
    """One row of a project's servers.yaml inventory (Remote Ops)."""

    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=64)
    pem: str = Field(min_length=1, max_length=500)  # path to the key on the host
    notes: Optional[str] = None


# ---- Workflows (saved stage pipelines) ------------------------------------


class WorkflowStageItem(BaseModel):
    agent: str  # agent id or exact name
    prompt: str = Field(min_length=1)
    outputs: Optional[Any] = None  # checked by gates.spec.validate_stages


class WorkflowStage(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Gate/approval fields are optional here; gates.spec.validate_stages is the
    # authoritative check (agent stages need items, and so on).
    items: Optional[list[WorkflowStageItem]] = None
    kind: Optional[str] = None
    project: Optional[str] = None
    run: Optional[list[str]] = None
    retry: Optional[str] = None
    before: Optional[list[str]] = None
    timeout_min: Optional[int] = None
    message: Optional[str] = None
    auto_retry: Optional[Any] = None  # checked by validate_stages (must be a bool)
    fix: Optional[Any] = None  # gate fix loop, checked by validate_stages


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    budget_usd: Optional[float] = Field(default=None, ge=0.01, le=10000, strict=True)
    stages: list[WorkflowStage] = Field(min_length=1)


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    stages: Optional[list[WorkflowStage]] = None
    budget_usd: Optional[float] = Field(default=None, ge=0.01, le=10000, strict=True)


class WorkflowOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    stages: list
    budget_usd: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowRunCreate(BaseModel):
    input: str = ""  # the PR/issue/description this run acts on


class WorkflowRunSummaryOut(BaseModel):
    """A run without its log and per-stage state: list and action responses."""

    id: str
    workflow_id: str
    name: str
    input: str
    status: str
    current_stage: int
    stages: list
    project_ids: list[str] = []
    budget_usd: Optional[float] = None
    created_by: Optional[str] = None  # who started the run (run page header)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowRunOut(WorkflowRunSummaryOut):
    # Per-stage state keyed by str(index) and the run log [{at, text}] (phase 5a).
    stage_state: dict = {}
    log: list = []

    @field_validator("stage_state", "log", mode="before")
    @classmethod
    def _none_empty(cls, v, info):
        return ({} if info.field_name == "stage_state" else []) if v is None else v


class BudgetRaise(BaseModel):
    """POST /workflows/runs/{id}/budget: the new cap (must also be more than spent)."""

    budget_usd: float = Field(ge=0.01, le=10000, strict=True)


class StageReject(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_len(cls, v):
        v = (v or "").strip()
        if not 1 <= len(v) <= 500:
            raise ValueError("a reason of 1 to 500 characters is required")
        return v


class LiveStageOut(BaseModel):
    name: str
    # done | current | next | failed | waiting_approval
    state: str
    task_count: int


class LiveItemOut(BaseModel):
    """A live workflow run or coordinator thread, for the Home comet scene."""

    kind: str  # run | thread
    id: str
    title: str
    project_ids: list[str] = []
    status: str
    created_at: datetime
    stages: list[LiveStageOut]


# ---- Skill packs (Claude Code plugins) ------------------------------------


class SkillPackCreate(BaseModel):
    name: Optional[str] = None  # derived from repo if omitted
    source_url: Optional[str] = None  # git repo to clone
    local_path: Optional[str] = None  # or an existing local plugin dir
    description: Optional[str] = None
    scope: str = "global"  # global | project
    project_id: Optional[str] = None
    pinned_sha: Optional[str] = None  # install at exactly this commit (40 hex)


class SkillPackUpdate(BaseModel):
    enabled: Optional[bool] = None
    scope: Optional[str] = None
    project_id: Optional[str] = None


class SkillPackOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    source_url: Optional[str]
    local_path: str
    version: Optional[str]
    enabled: bool
    scope: str
    project_id: Optional[str]
    created_at: datetime
    current_sha: Optional[str] = None
    latest_sha: Optional[str] = None
    checked_at: Optional[datetime] = None
    update_available: bool = False

    model_config = {"from_attributes": True}


class ContextDocCreate(BaseModel):
    path: str  # e.g. "runbooks/deploy.md"
    title: Optional[str] = None  # derived from the path if omitted
    abstract: Optional[str] = None  # derived from the body if omitted
    body: str = ""
    scope: str = "global"  # global | project
    project_id: Optional[str] = None
    enabled: bool = True


class ContextDocUpdate(BaseModel):
    path: Optional[str] = None
    title: Optional[str] = None
    abstract: Optional[str] = None
    body: Optional[str] = None
    scope: Optional[str] = None
    project_id: Optional[str] = None
    enabled: Optional[bool] = None


class ContextDocOut(BaseModel):
    id: str
    path: str
    title: str
    abstract: str
    body: str
    enabled: bool
    scope: str
    project_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SetupIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    role: str = "member"
    password: str


class UserPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    role: Optional[str] = None
    active: Optional[bool] = None


class PasswordResetIn(BaseModel):
    password: str


class UserBrief(BaseModel):
    id: str
    name: str
    email: str

    model_config = {"from_attributes": True}


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TeamMembersIn(BaseModel):
    user_ids: list[str]


class TeamOut(BaseModel):
    id: str
    name: str
    members: list[UserBrief] = Field(default_factory=list)


class MemberIn(BaseModel):
    subject_type: str
    subject_id: str
    role: str


class MemberOut(BaseModel):
    subject_type: str
    subject_id: str
    role: str
    label: str


# --------------------------------------------------------------------------- credentials

_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _secret(v: Optional[str]) -> str:
    # null is not "clear" for a secret (use DELETE); blank is never a secret.
    if v is None or not v.strip():
        raise ValueError("must be a non-empty string")
    v = v.strip()
    if _CTRL.search(v) or any(c.isspace() for c in v):
        raise ValueError("must not contain whitespace or control characters")
    return v


class CredentialsIn(BaseModel):
    """Fields left out are unchanged; git_name/git_email null (or blank) clears."""

    model_config = {"extra": "forbid"}

    claude_token: Optional[str] = Field(default=None, max_length=4096)
    claude_kind: Optional[Literal["oauth_token", "api_key"]] = None
    github_token: Optional[str] = Field(default=None, max_length=4096)
    git_name: Optional[str] = Field(default=None, max_length=200)
    git_email: Optional[str] = Field(default=None, max_length=320)

    @field_validator("git_name", "git_email")
    @classmethod
    def _identity(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if _CTRL.search(v):  # lands in a gitconfig file: no newlines/control chars
            raise ValueError("must not contain control characters")
        return v or None

    @field_validator("git_email")
    @classmethod
    def _email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and (v.count("@") != 1 or " " in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("must be an email address")
        return v

    @model_validator(mode="after")
    def _check(self):
        for f in ("claude_token", "github_token"):
            if f in self.model_fields_set:
                setattr(self, f, _secret(getattr(self, f)))
        has_token = "claude_token" in self.model_fields_set
        has_kind = "claude_kind" in self.model_fields_set and self.claude_kind is not None
        if has_token != has_kind:
            raise ValueError("claude_token and claude_kind must be given together")
        return self


class CredentialStatus(BaseModel):
    claude: Literal["own", "fallback", "none"]
    claude_kind: Optional[Literal["oauth_token", "api_key"]] = None
    github: bool
    git_name: Optional[str] = None
    git_email: Optional[str] = None


class CredentialTestIn(BaseModel):
    which: Literal["claude", "github"]


class CredentialTestOut(BaseModel):
    ok: bool
    detail: str


class OrgFallbackIn(BaseModel):
    model_config = {"extra": "forbid"}

    api_key: str = Field(max_length=4096)

    @field_validator("api_key")
    @classmethod
    def _key(cls, v: str) -> str:
        return _secret(v)


class OrgFallbackOut(BaseModel):
    set: bool
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class AskIn(BaseModel):
    question: str = ""  # validated in the route, after the 404


class AskOut(BaseModel):
    id: str
    task_id: str
    question: str
    answer: Optional[str] = None
    status: str
    error: Optional[str] = None
    cost_usd: Optional[float] = None
    model: Optional[str] = None
    created_at: datetime
    answered_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- decisions (phase 4a)


class DecisionIn(BaseModel):
    kind: str = "decision"
    title: str
    why: str = ""
    see: Optional[str] = None
    touches: list[str] = Field(default_factory=list)
    source: str = ""


class DecisionPatch(BaseModel):
    version: int
    kind: Optional[str] = None
    title: Optional[str] = None
    why: Optional[str] = None
    see: Optional[str] = None
    touches: Optional[list[str]] = None
    source: Optional[str] = None


class DecisionSupersede(BaseModel):
    by: int


class DecisionOut(BaseModel):
    id: str
    number: int
    display_id: str
    kind: str
    title: str
    why: str
    see: Optional[str] = None
    touches: list[str]
    decided_by: Optional[str] = None
    decided_by_name: Optional[str] = None
    decided_at: datetime
    last_verified_at: datetime
    source: str
    status: str
    superseded_by: Optional[int] = None
    version: int
    created_at: datetime
    updated_at: datetime
    stale_flagged_at: Optional[datetime] = None
    stale_reason: Optional[str] = None


class ProposalIn(BaseModel):
    title: str
    why: str = ""
    touches: list[str] = Field(default_factory=list)
    kind: str = "decision"


class ProposalAccept(BaseModel):
    kind: str
    title: str
    why: str = ""
    see: Optional[str] = None
    touches: list[str] = Field(default_factory=list)


class ProposalOut(BaseModel):
    id: str
    source: str
    kind: str
    title: str
    why: str
    touches: list[str]
    evidence: str
    status: str
    task_id: Optional[str] = None
    task_title: Optional[str] = None
    thread_title: Optional[str] = None
    proposed_by: Optional[str] = None
    proposed_by_name: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    decision_number: Optional[int] = None
    created_at: datetime


class ProposalList(BaseModel):
    items: list[ProposalOut]
    pending: int


# --------------------------------------------------------------------------- launch list (phase 5b)


class LaunchPut(BaseModel):
    launch_date: Optional[date] = None
    critical_paths: list[str] = Field(default_factory=list)
    version: Optional[int] = None  # required once the launch row exists
    raise_risk: Optional[bool] = Field(default=None, strict=True)  # omitted keeps the stored value


class ScopeCheckIn(BaseModel):
    prompt: str = Field(default="", max_length=20000)


class ScopeCheckOut(BaseModel):
    new_scope: bool
    open: int
    would_be: int
    launch_date: Optional[date] = None


class LaunchEstimateOut(BaseModel):
    median_days: float
    min_days: int
    max_days: int
    n: int


class LaunchFactsOut(BaseModel):
    launch_date: Optional[date] = None
    open: int
    done: int
    would_be: int
    after: list[str] = []
    critical_touched: list[str] = []
    estimate: Optional[LaunchEstimateOut] = None


class LaunchItemIn(BaseModel):
    title: Optional[Any] = None
    section: str = "before"


class LaunchItemPatch(BaseModel):
    version: Optional[int] = None
    title: Optional[str] = None
    section: Optional[str] = None
    status: Optional[str] = None


class LaunchSettingsOut(BaseModel):
    launch_date: Optional[date] = None
    critical_paths: list[str] = []
    raise_risk: bool = False
    version: int


class LaunchItemOut(BaseModel):
    id: str
    number: int
    title: str
    section: str
    status: str
    added_at: datetime
    started_at: Optional[datetime] = None
    done_at: Optional[datetime] = None
    added_by: Optional[str] = None
    added_by_name: Optional[str] = None
    version: int


class LaunchOut(BaseModel):
    launch: Optional[LaunchSettingsOut] = None
    items: list[LaunchItemOut] = []
    all_linked_done: dict[int, bool] = {}


# ---- Code map (phase 6b) ----------------------------------------------------


class CodeMapOut(BaseModel):
    enabled: bool
    status: str  # never | running | ok | failed
    sha: Optional[str] = None
    indexed_at: Optional[datetime] = None
    behind: Optional[int] = None  # commits between sha and HEAD; null when unknown
    error: Optional[str] = None  # the raw reason: shown under Details only
    failure: Optional[str] = None  # failed: error | timeout | start (the status line's plain words)
    next_try_at: Optional[datetime] = None
    server_registered: bool = False


class CodeMapLogOut(BaseModel):
    lines: list[str]
