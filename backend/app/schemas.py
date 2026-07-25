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
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

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
