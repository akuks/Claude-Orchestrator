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

    # API
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]


settings = Settings()
settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
