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
    # Default per-task spend cap in USD (--max-budget-usd). None = uncapped.
    default_task_budget_usd: float | None = None
    # Fallback model(s) if the primary is unavailable (--fallback-model). "" = off.
    fallback_model: str = ""
    # Auto-retry transient failures (rate limits / overload / network) with
    # exponential backoff. 0 attempts disables it.
    retry_max_attempts: int = 2
    retry_backoff_seconds: int = 30
    # Max bytes for a single line of Claude's stream-json output. The default
    # asyncio StreamReader limit is 64 KB, which large tool results (e.g. big PR
    # diffs / file contents) exceed. 64 MB is generous headroom.
    stream_buffer_limit_bytes: int = 64 * 1024 * 1024

    # Secrets / credential vault
    secret_key: str | None = None
    secret_key_file: Path = Path("./.secret.key")

    # MCP management
    mcp_health_interval_seconds: int = 300
    mcp_probe_timeout_seconds: int = 15

    # GitHub webhook (auto-trigger reviews). Set the secret you configure in the
    # repo's webhook settings; blank disables signature verification.
    github_webhook_secret: str = ""

    # Scheduling (Phase 4)
    scheduler_interval_seconds: int = 30

    # Approvals (Phase 5): auto-reject pending approvals older than this.
    approval_timeout_seconds: int = 86400  # 24h; 0 disables auto-reject
    approval_check_interval_seconds: int = 60
    # Force approval for any task classified 'critical' risk (merge/deploy/delete/
    # force-push), even if it wasn't explicitly flagged — so dangerous automations
    # can never run unattended.
    gate_critical_approval: bool = True

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
