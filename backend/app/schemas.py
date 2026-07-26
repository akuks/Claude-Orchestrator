from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class InputFile(BaseModel):
    name: str
    content_base64: str


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    title: Optional[str] = None
    project: Optional[str] = None
    priority: str = "normal"
    tags: list[str] = Field(default_factory=list)
    model: str = "sonnet"
    max_turns: int = Field(default=25, ge=1, le=200)
    claude_md: Optional[str] = None
    input_files: list[InputFile] = Field(default_factory=list)


class TaskOut(BaseModel):
    id: str
    title: str
    prompt: str
    project: Optional[str]
    status: str
    priority: str
    tags: list[str]
    model: str
    max_turns: int
    exit_code: Optional[int]
    error: Optional[str]
    result_text: Optional[str]
    num_turns: Optional[int]
    total_cost_usd: Optional[float]
    duration_ms: Optional[int]
    parent_task_id: Optional[str]
    root_id: Optional[str]
    session_id: Optional[str]
    thread_count: int = 1  # number of steps in this task's thread
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class FollowupCreate(BaseModel):
    prompt: str = Field(min_length=1)


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


class McpServerUpdate(BaseModel):
    scope: Optional[str] = None
    project: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list[str]] = None
    url: Optional[str] = None
    env: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None


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
