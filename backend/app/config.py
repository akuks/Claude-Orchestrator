from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CO_", env_file=".env", extra="ignore")

    # Storage
    database_url: str = "sqlite+aiosqlite:///./claude_orchestrator.db"
    # Disable connection pooling (tests: asyncpg connections can't cross event loops).
    db_null_pool: bool = False
    workspaces_dir: Path = Path("./workspaces")

    # Claude Code worker
    claude_bin: str = "claude"
    default_model: str = "sonnet"
    default_max_turns: int = 25
    worker_concurrency: int = 5
    task_timeout_seconds: int = 1800
    claude_permission_mode: str = "bypassPermissions"
    # Default per-task spend cap in USD (--max-budget-usd). None = uncapped.
    # A sane global ceiling so no single task can run away; agents can override.
    default_task_budget_usd: float | None = 2.0
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
    # How often the workflow runner advances active runs.
    workflow_runner_interval_seconds: int = 5
    # Cap on how much of a stage's output is passed to later stages via {{prev}}
    # / {{stageN}} — keeps chained input (and token cost) bounded.
    workflow_stage_output_max_chars: int = 8000

    # Approvals (Phase 5): auto-reject pending approvals older than this.
    approval_timeout_seconds: int = 86400  # 24h; 0 disables auto-reject
    approval_check_interval_seconds: int = 60
    # Crash recovery: how often RUNNING rows with no live worker are swept
    # (app.resume.recovery.SWEEP_SECONDS).
    recovery_sweep_seconds: int = 60
    # Force approval for any task classified 'critical' risk (merge/deploy/delete/
    # force-push), even if it wasn't explicitly flagged — so dangerous automations
    # can never run unattended.
    gate_critical_approval: bool = True

    # Skill packs (Claude Code plugins, loaded via --plugin-dir)
    skillpacks_dir: Path = Path("./skillpacks")

    # Context Store (curated knowledge docs, tiered: abstracts injected, bodies on disk)
    context_index_max_chars: int = 4000  # cap on the injected L0 index (~1000 tokens)

    # Projects & memory (Phase 3)
    projects_dir: Path = Path("./projects")
    context_budget_tokens: int = 3000  # injected project memory + summaries per task
    memory_enabled_default: bool = True
    memory_model: str = "haiku"
    memory_max_chars: int = 6000  # ~1500 tokens
    memory_call_timeout_seconds: int = 120

    # Suggested decisions (phase 4b)
    proposals_enabled: bool = True
    proposals_pending_cap: int = 50
    proposals_model: str = "haiku"

    # Auth (multi-user)
    session_ttl_days: int = 7
    login_max_failures: int = 5
    login_window_seconds: int = 900
    # Bootstrap admin, created on first start when no users exist.
    admin_email: str = ""
    admin_password: str = ""

    # API
    # Base URL a coordinator agent uses to dispatch to other agents (self-call).
    api_base: str = "http://localhost:8200"
    # Where uvicorn listens (start.sh: CO_BIND_HOST / CO_BACKEND_PORT). co-guard's
    # hook talks to the server only at this LITERAL ip:port (never a hostname an
    # agent could answer on); a wildcard bind means loopback.
    bind_host: str = "127.0.0.1"
    backend_port: int | None = None  # None: the port of api_base
    # co-guard watchdog: a tool call with no co-guard PreToolUse hook seen in the
    # stream within this many seconds stops the task.
    guard_hook_grace_seconds: float = 15.0
    # Verify the approval address is this server before each task (Ruling R22).
    # Tests turn it off (no server listens there); never turn it off in production.
    guard_verify_api: bool = True
    # The UI's public base URL (e.g. https://orchestrator.example.com), for
    # links in notifications. "" = notifications name the page instead.
    public_url: str = ""
    # "Ask about this run" (phase 3c): one cheap, tool-less call over the run's record.
    ask_enabled: bool = True
    ask_model: str = "haiku"
    ask_budget_usd: float = 0.25
    ask_timeout_seconds: int = 90
    ask_concurrency: int = 3
    ask_hourly_limit: int = 30
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5200",  # start.sh UI (Vite proxy keeps the browser Origin)
        "http://127.0.0.1:5200",
    ]


settings = Settings()
# Absolute paths so workspace/MCP-config paths are unambiguous regardless of cwd.
settings.workspaces_dir = settings.workspaces_dir.resolve()
settings.projects_dir = settings.projects_dir.resolve()
settings.skillpacks_dir = settings.skillpacks_dir.resolve()
settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
settings.projects_dir.mkdir(parents=True, exist_ok=True)
settings.skillpacks_dir.mkdir(parents=True, exist_ok=True)
