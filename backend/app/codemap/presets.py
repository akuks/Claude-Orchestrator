"""Pinned MCP presets. Plain data; nothing installs by itself."""

PRESETS: list[dict] = [
    {
        "key": "code-map",
        "name": "Code map",
        "description": "Builds a searchable map of a project's code so tasks can look things up instead of reading every file.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "codebase-memory-mcp@0.12.1"],
        "pinned": "0.12.1",
    },
    {
        "key": "browser-playwright",
        "name": "Browser (Playwright)",
        "description": "Lets a task open web pages, click and read them in a real browser.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@0.0.86"],
        "pinned": "0.0.86",
    },
]
