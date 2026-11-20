# deepseek-harness review — what to borrow (2026-11-20)

Source: https://github.com/deepseek-ai/deepseek-harness (MIT, TypeScript, "developer preview"). Read via the GitHub API (READMEs/docs only; no code executed or cloned).

**What it is:** a single-user, local agent harness for DeepSeek models where everything (model adapter, tools, session log, agent loop) is a plugin on the vendored Cordis framework. Tool calls go through a pipeline: pre-execute hooks/permissions/sandbox → deny-only guards → execute (timeout/retry) → post-execute. Per-call sandbox levels (read-only / workspace-write / full). Subagents in-process or out-of-process (incl. Claude Code), a team task board, JS workflow scripts (`agent()/parallel()/pipeline()/phase()`), experimental worktree, auto-review and forge (GitHub/GitLab) packages. No multi-user/org features.

## Borrow (ideas, rewritten in Python — no code copied)
| # | Idea (their path) | Maps to our | Effort |
|---|---|---|---|
| 1 | **Fresh-agent loop with a bounded structured handoff** (`packages/experimental/tool-ralph`): each round a fresh child sees only the objective, round n/cap and the previous report `{status: continue/complete/blocked, summary, evidence, next, blocker}`; ends complete / blocked / budget-limited; oversize report fails rather than truncates | Workflow v2 review→fix loop (pair with our check stages, since reports aren't self-verifying) | S–M |
| 2 | **Worktree lifecycle** (`packages/experimental/worktree`, `tool-worktree`): new named branch per create from a local commit, source checkout untouched, files/branch retained on exit, cleanup as a separate archive step | Phase 3b worktree-per-unit | S |
| 3 | **Loop/hang guards** (`packages/guard/repeat-tool-reminder`): advisory nudges at 3/5/8 identical tool calls; per-tool timeouts return clear errors | Worker: watch stream-json for repeated identical tool calls → event/nudge/cap | S |
| 4 | **Workflow primitives** (`packages/workflow/workflow`): parallel/pipeline/phase, failures resolve rather than reject, "use workflows only for many independent pieces" | Workflow v2 design reference | M |
| 5 | **Spill large outputs** (`packages/spill`): store big tool results out of context, give the model a locator | Context Store / long runs | S–M |
| 6 | **Auto-review of risky actions** (`packages/experimental/auto-review`): a model pre-screens pending risky calls, escalates denials to a human; off by default | Optional pre-filter for our approvals queue | M |
| 7 | **Test discipline** (`docs/testing.md`): real-API e2e self-skips without a key; recorded-session replay for keyless deterministic regression tests | Eval/reliability feature; replaying recorded Claude stream-json in tests | S |

## Don't borrow
Cordis plugin framework (TS rewrite, no fit with our Python backend); their sandbox backends (Claude Code CLI has its own; theirs are single-user and "not production-secure" per SAFETY.md); DeepSeek model/account/GUI packages; their single-user session store (our multi-user scoping is ahead).

## License notes
Repo is MIT (keep the notice if copying code). `vendor/` (Cordis) is MIT per THIRD_PARTY_NOTICES.md. Dependencies include Apache-2.0 and `@anthropic-ai/claude-agent-sdk` (non-MIT) — never copy from those. Recommendation: take the designs, rewrite in Python; no notice obligations then.
