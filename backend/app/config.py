from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CO_", env_file=".env", extra="ignore")

    # Storage
    database_url: str = "sqlite+aiosqlite:///./claude_orchestrator.db"
    workspaces_dir: Path = Path("./workspaces")

    # Claude Code worker
    claude_bin: str = "claude"
    default_model: str = "sonnet"
    default_max_turns: int = 25
    worker_concurrency: int = 3
    task_timeout_seconds: int = 1800
    claude_permission_mode: str = "bypassPermissions"

    # Secrets / credential vault
    secret_key: str | None = None
    secret_key_file: Path = Path("./.secret.key")

    # MCP management
    mcp_health_interval_seconds: int = 300
    mcp_probe_timeout_seconds: int = 15

    # Projects & memory (Phase 3)
    projects_dir: Path = Path("./projects")
    context_budget_tokens: int = 6000
    memory_enabled_default: bool = True
    memory_model: str = "haiku"
    memory_max_chars: int = 6000  # ~1500 tokens
    memory_call_timeout_seconds: int = 120

    # API
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]


settings = Settings()
settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
settings.projects_dir.mkdir(parents=True, exist_ok=True)
