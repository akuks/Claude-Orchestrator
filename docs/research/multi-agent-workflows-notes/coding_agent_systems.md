# How Real-World Coding-Agent Systems Run Multiple Agents on Complex Codebases (as of Nov 2026)

Research date: 2026-11-20. About 18 tool calls. Primary sources (vendor docs and engineering blogs) are marked [primary]. Third-party blogs and aggregators are marked [secondary]. Vendor self-reported numbers are marked [vendor claim]. Many 2026 product details could only be found in secondary coverage and should be checked against the vendors' docs.

## 1. How do current products handle multi-agent / parallel coding?

### Takeaway
By late 2026 every major product supports parallel agents, and they converge on two shapes. The first is a **local orchestrator plus isolated workers**: Claude Code subagents and Agent Teams, Cursor's Agents Window with `/multitask`, and the Codex app with worktrees. The second is **cloud task-per-sandbox, producing a PR**: Codex cloud, Copilot coding agent through Mission Control, Jules, Devin and managed Devins, and Cursor Cloud Agents. Coordination features (shared task lists, coordinators that message children) are newer and often labelled experimental.

### Cited Findings
**Claude Code (Anthropic)**
- **Three parallelism levels:**
  - Subagents run inside one session, have their own context, and return a result to the caller.
  - Agent Teams are separate Claude Code instances coordinated by a "team lead" session. They share a task list and a mailbox, and teammates message each other directly.
  - Manual parallel sessions run in git worktrees, with no automated coordination.
  - [primary] [Claude Code docs: Agent teams](https://code.claude.com/docs/en/agent-teams)
- **Status:** Agent Teams are "experimental and disabled by default", enabled with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. Teammates are not spawned in non-interactive `-p` / Agent SDK mode. [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **Team mechanics:**
  - Tasks are pending, in progress or completed, with dependencies. Blocked tasks are not self-claimed.
  - "Task claiming uses file locking to prevent race conditions."
  - Mailboxes are JSON files at `~/.claude/teams/{team}/inboxes/{agent}.json`.
  - Teammates load CLAUDE.md, MCP servers and skills, but **not** the lead's conversation history. Task context must go in the spawn prompt.
  - Limits: no nested teams, one team per session, and in-process teammates are not restored on `/resume`.
  - [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **Official sizing guidance:**
  - "Start with 3-5 teammates for most workflows." If you have 15 independent tasks, 3 teammates is a good starting point.
  - "5-6 tasks per teammate". "Three focused teammates often outperform five scattered ones."
  - Token cost "scales linearly" with the number of teammates.
  - [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **Release date:** Agent Teams reportedly shipped in Feb 2026 alongside Opus 4.6. [secondary] [morphllm](https://www.morphllm.com/claude-code-agent-teams)
- **Worktrees:** `claude --worktree <name>` creates worktrees at `<repo>/.claude/worktrees/<name>/` on a branch `worktree-<name>`. [secondary] [morphllm](https://www.morphllm.com/claude-code-agent-teams). The worktrees docs page exists ([code.claude.com/docs/en/worktrees](https://code.claude.com/docs/en/worktrees.md)) but was not fetched.
- **Newer options:** A Nov 2026 guide says Anthropic's docs now list "subagents, agent view, agent teams, dynamic workflows and projects" as ways to run tasks concurrently. It recommends giving every file-editing session its own worktree. [secondary] [wotai.co, updated 2026-11-07](https://wotai.co/blog/claude-code-parallel-agents)

**Anthropic C-compiler experiment (measured, Feb 2026)** [primary] [Anthropic Engineering: Building a C compiler](https://www.anthropic.com/engineering/building-c-compiler)
- **Setup:**
  - 16 Claude instances, each in its own Docker container, ran with no human intervention.
  - Each container had a bare git repo mounted at `/upstream`, cloned to `/workspace`, and pushed back to upstream.
  - A bash loop restarted a fresh Claude Code session whenever one finished.
- **Results:** About 2,000 sessions, about 2B input and 140M output tokens, "just under $20,000", and about 2 weeks. It produced a roughly 100k-line Rust C compiler that builds Linux 6.9 on x86, ARM and RISC-V.
- **Specialised roles:** Some agents worked on deduplication, performance, codegen quality, Rust code review and documentation.

**Cursor**
- **Agents Window:**
  - Cursor 3 (2 Apr 2026) replaced Composer with a standalone Agents Window for running agents in parallel. [secondary] [digitalapplied](https://www.digitalapplied.com/blog/cursor-3-agents-window-complete-guide)
  - Cursor 3.2 (24 Apr 2026) added `/multitask`, which fans a request out to async subagents. It also added background tasks across branches with one-click "promote to foreground", plus multi-root workspaces. [secondary] [Futurum](https://futurumgroup.com/insights/cursor-3-2-reframes-the-ide-as-an-agent-execution-runtime/), [doolpa](https://doolpa.com/news/cursor-3-2-multitask-worktrees-multi-root-workspaces-april-24-2026)
  - Cursor does not promise dependency-aware scheduling. [secondary] [Futurum](https://futurumgroup.com/insights/cursor-3-2-reframes-the-ide-as-an-agent-execution-runtime/)
- **Cloud Agents:** "Background Agents" were renamed "Cloud Agents" in the 2.0 era. They run on remote VMs, and reports cite "up to 8 parallel agents per prompt". Sources disagree on dates and limits. [secondary] [morphllm](https://www.morphllm.com/cursor-background-agents), [dev.to](https://dev.to/jangwook_kim_e31e7291ad98/cursor-20-8-parallel-ai-agents-and-visual-editor-bridge-50nk)
- **Research harness (14 Jan 2026):** Hundreds of concurrent agents ran on a single project, using "trillions of tokens" in total. Full detail is under question 3. [primary] [Cursor: Scaling long-running autonomous coding](https://cursor.com/blog/scaling-agents)
- **Follow-up post:** "Towards self-driving codebases" ([cursor.com/blog/self-driving-codebases](https://cursor.com/blog/self-driving-codebases)) was not fetched.

**OpenAI Codex**
- **Codex app:** A macOS desktop "command center" launched 2 Feb 2026. It runs multiple agents, groups threads by project, and uses built-in git worktrees. [secondary] [heise](https://heise.de/-11165126), [verdent](https://verdent.ai/guides/codex-app-first-impressions-2026)
- **Cloud tasks:**
  - Cloud tasks run in isolated OpenAI-hosted containers.
  - `codex cloud exec --attempts 3` gives best-of-N.
  - One guide cites up to 6 concurrent cloud threads.
  - Worktree retention figures conflict: 15 most recent versus cleanup above 10 or after 4 days.
  - [secondary] [developertoolkit.ai](https://developertoolkit.ai/en/codex/automate/multi-agent-workflows/)
- **Original launch:** The original cloud Codex launch post is at [openai.com/index/introducing-codex](https://openai.com/index/introducing-codex/) and was not fetched.

**Devin (Cognition)**
- **Managed Devins (19 Mar 2026):** A main Devin "acts as a coordinator". It:
  - scopes the work and assigns pieces to child Devins, each in "its own isolated virtual machine" with shell, browser and dev environment and a clean context
  - messages children mid-task and monitors each child's ACU (compute) consumption
  - pauses or stops children that are done or off track
  - schedules its own follow-ups, and reads child trajectories to improve future breakdowns
  - "resolves any conflicts", though the post does not say how

  Examples: a security audit run "10 at a time" and testing 10 features. Available to all users. [primary] [Cognition: Devin can now manage Devins](https://cognition.com/blog/devin-can-now-manage-devins)
- **Scheduled Devins (20 Mar 2026):** Combined with managed Devins, for example a weekly QA pass that spawns a managed Devin per app page. [primary per search snippet] [Cognition: schedule Devins](https://cognition.ai/blog/devin-can-now-schedule-devins)
- **Unverified "Devin Bots" report:** A secondary report describes "Devin Bots" with supervisory agents, claiming beta testers merged "4.5x more PRs per message". Not found on Cognition's site, so it is an unverified marketing-style claim. [secondary] [hermes-ai.net](https://hermes-ai.net/news/cognition-s-devin-bots-now-run-parallel-coding-sessions-hitting-2-000-pull/)

**GitHub Copilot coding agent / Agent HQ**
- **Agent HQ (GitHub Universe, Oct 2025):** Mission Control is one place to assign, steer and track agents. Agent HQ is pitched as hosting third-party agents from Anthropic, OpenAI, Google, Cognition, xAI and others inside Copilot subscriptions. [primary] [GitHub blog: Welcome home, agents](https://github.blog/news-insights/company-news/welcome-home-agents/)
- **Mission Control (1 Dec 2025):**
  - Prompts go to one or many repos.
  - Custom agents are defined by `agents.md` in the repo. GitHub analysed 2,500 such files.
  - Real-time session logs let you pause, refine or restart a run.
  - Each task produces PRs.
  - Single tasks take 30s to a few minutes. Parallel drafts take minutes to an hour.
  - [primary] [GitHub blog: How to orchestrate agents using mission control](https://github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control/)
- **Governance:** Admins control which agents and models are allowed and see usage metrics. Agents commit only to designated branches. [secondary] [VentureBeat](https://venturebeat.com/ai/githubs-agent-hq-aims-to-solve-enterprises-biggest-ai-coding-problem-too)

**Google Jules**
- **Plan approval:** The Jules API (v1alpha) auto-approves plans by default. Setting `requirePlanApproval: true` plus a `:approvePlan` call enforces a human gate. [primary] [Jules API docs](https://developers.google.com/jules/api)
- **VMs and concurrency:** Each task runs in its own cloud VM. Reported caps are 3 concurrent on free, 15 on Pro and 60 on Ultra, with an older beta cap of 2. [secondary, may change] [zenml / rywalker / tooldirectory](https://rywalker.com/research/jules)
- **Auto-approval caveat:** The web UI plan reportedly auto-approves on a timer if the user leaves. [secondary] [rywalker](https://rywalker.com/research/jules)

**OpenHands (All Hands)**
- **SDK delegation:** The Software Agent SDK lets a main agent spawn sub-agents for parallel work, which return results to it. [primary] [OpenHands SDK: agent delegation](https://docs.openhands.dev/sdk/guides/agent-delegation.md)
- **Refactor SDK:** In private beta. It offers task decomposition, progress tracking and fixers/verifiers, scaling "from 3 to 30 agents". [secondary/vendor] [OpenHands webinar](https://luma.com/openhands-webinar-dec-2025)

**Factory / Augment / Amp**
- **Factory:** Droids run in the CLI, a desktop app or headless in CI. Custom Droids act as subagents, and "Missions" split work into subtasks and coordinate parallel Droids. Enterprise customers named include Nvidia, Adobe and EY. [secondary, vendor claims] [digitalapplied](https://www.digitalapplied.com/blog/factory-ai-multi-agent-coding-platform-review), [rywalker](https://rywalker.com/research/factory-ai)
- **Augment:** "Intent" is described as using a coordinator/specialist/verifier model. [secondary, vendor site] [augmentcode.com](https://www.augmentcode.com/tools/best-agentic-development-environments)
- **Amp:** No reliable source found on its subagents.

### Inferences
- The industry default unit of work is now "one agent → one isolated environment → one branch/PR". Products differ mainly in where the sandbox lives (local worktree, container or VM) and in how much automated coordination sits above it.
- Vendor docs (Anthropic, GitHub, OpenHands) independently recommend about 3–5 concurrent agents for real work. The constraint they cite is human review and coordination capacity, not compute.

### Gaps
- Official Codex docs for the app, cloud limits and the AGENTS.md hierarchy were not fetched. The Codex figures come from secondary sources.
- Cursor 3.x official changelog details were not fetched, and dates and limits come from secondary sources.
- Amp's subagent/oracle design and Augment Intent's mechanics have no primary source here.
- SWE-agent's multi-agent features were not researched. It is primarily a single-agent research harness.

## 2. How is concurrent work isolated, and how are results merged?

### Takeaway
Isolation comes in three tiers: **git worktree on the developer machine**, **container**, and **per-task cloud VM**. Merge strategy ranges from "PR per task, human-reviewed" (Copilot, Jules, Codex cloud) through a "rolling integration branch with sub-branch PRs" (OpenHands) to "everyone pushes to one branch and resolves their own conflicts" (Anthropic compiler, Cursor research). Experiments that used a dedicated integrator agent or locks found them to be bottlenecks.

### Cited Findings
- **Worktrees:**
  - Claude Code: `claude --worktree` on a `worktree-<name>` branch. [secondary] [morphllm](https://www.morphllm.com/claude-code-agent-teams)
  - Cursor Agents Window: one worktree per agent, configured in `.cursor/worktrees.json`. [secondary] [developertoolkit.ai](https://developertoolkit.ai/en/cursor-ide/build/agents-window.md)
  - Codex app: built-in worktrees, where git forbids the same branch in two worktrees. [secondary] [verdent](https://verdent.ai/guides/codex-app-first-impressions-2026)
- **Worktrees are not full isolation:**
  - They share the host environment, so a runaway install or migration affects the real system. [secondary] [agentsroom / crystl comparisons](https://agentsroom.dev/blog/best-multi-agent-coding-tools)
  - The Cursor forum reports parallel agents writing to the root repo instead of their worktree. [secondary] [forum.cursor.com](https://forum.cursor.com/tag/worktrees)
- **Concurrent writes are the user's problem:** Anthropic's agent-teams docs say "Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files." [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **Containers plus a git upstream (Anthropic compiler):**
  - **Task claiming:** an agent claims a task by writing a lock file to `current_tasks/`, and git sync makes the losing agent choose another task.
  - **Merging:** after finishing, the agent pulls, merges upstream, pushes and removes its lock. "Conflicts were frequent, but Claude resolved them itself."
  - **When it broke down:** with one monolithic task (compiling the Linux kernel), agents hit the same bug and overwrote each other's fixes.
  - **The fix:** a GCC oracle compiled most of the kernel and left a random subset to Claude's compiler, so agents could chase different bugs.
  - [primary] [Anthropic: C compiler](https://www.anthropic.com/engineering/building-c-compiler)
- **Cursor research harness:**
  - Shared-file locking failed: "Twenty agents ran at the effective throughput of two or three". Agents crashed while holding locks or edited without locking.
  - Optimistic concurrency was more robust, but without hierarchy agents made small, safe changes and churned.
  - The integrator role "created more bottlenecks than it solved" and was removed. "Hundreds of workers push to one branch with minimal conflicts", handling conflicts themselves.
  - [primary] [Cursor: scaling agents](https://cursor.com/blog/scaling-agents)
- **OpenHands rolling integration branch:**
  1. Create `v1-refactor` off main and add scaffolding (an `AGENTS.md` refactor description or `.openhands/microagents/refactor.md`).
  2. Each agent works on `v1-refactor/component-x` and opens a PR into `v1-refactor`.
  3. Agents pull merged changes and "can usually resolve merge conflicts".
  4. A final human-approved PR merges `v1-refactor` into `main`.

  [primary] [OpenHands: Automating massive refactors with parallel agents (2025-10-09)](https://openhands.dev/blog/automating-massive-refactors-with-parallel-agents)
- **Cloud VMs per task:**
  - Devin: one VM per managed Devin. [primary] [Cognition](https://cognition.com/blog/devin-can-now-manage-devins)
  - Jules: one VM per task. [secondary] [rywalker](https://rywalker.com/research/jules)
  - Cursor Cloud Agents: remote VMs. [secondary] [morphllm](https://www.morphllm.com/cursor-background-agents)
  - Codex cloud: OpenAI-hosted containers. [secondary] [developertoolkit.ai](https://developertoolkit.ai/en/codex/automate/multi-agent-workflows/)
- **GitHub Mission Control:**
  - Each task produces PRs.
  - Parallel tasks in the same repo "can conflict if they touch the same files", so partition the work.
  - Drift signals to review: unexpected files in the diff, changes to shared config, scope creep.
  - [primary] [GitHub blog](https://github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control/)
- **Open-source orchestrators** (Vibe Kanban, Claude Squad, Conductor) all give each agent its own git worktree and branch. See question 5. [secondary] [agentsroom](https://agentsroom.dev/blog/best-multi-agent-coding-tools)

### Inferences
- For application teams, partitioning by **file or module ownership** is the main conflict-avoidance mechanism. Every primary source says this, and none of the products does semantic conflict prevention.
- The "rolling integration branch" pattern (OpenHands) is the most review-friendly merge strategy found. It keeps `main` protected while many small agent PRs land.
- Push-to-one-branch at scale (Cursor, Anthropic) only worked with strong automated verifiers. It was used in research and greenfield settings, not shared production repos.

### Gaps
- No primary source found on stacked PRs or merge queues (e.g. Graphite, GitHub merge queue) being used specifically with multi-agent output.
- Devin's conflict-resolution mechanism is unspecified.

## 3. How are large features decomposed, and how is cross-agent context shared?

### Takeaway
The pattern that recurs is **planner/spec → small self-contained tasks → isolated implementers → verifier/judge/reviewer**, with context passed through files rather than shared memory: AGENTS.md/CLAUDE.md, plan and spec docs, task lists, and lock/progress files. Decomposition by dependency graph (leaf nodes first) and by module or file ownership is the most cited strategy for large codebases.

### Cited Findings
- **Cursor planner/worker/judge (Jan 2026):**
  - **Roles:** Planners continuously explore the codebase and create tasks, and can spawn sub-planners. Workers just finish tasks and push, without coordinating with each other. A judge decides at the end of each cycle whether to continue, and the next cycle "starts fresh" to fight drift.
  - **Model per role:** GPT-5.2 was a better planner than GPT-5.1-Codex, and "Opus 4.5 tended to stop early and take shortcuts".
  - **What mattered most:** the author says prompt design mattered more than the harness or the models.
  - **Projects and scale:**
    - a browser from scratch: over 1M LoC across about 1,000 files in nearly a week
    - a Solid→React migration of Cursor's own codebase: +266K/−193K over 3+ weeks
    - Java LSP: 7.4K commits, 550K LoC
    - Windows 7 emulator: 14.6K commits, 1.2M LoC
    - Excel clone: 12K commits, 1.6M LoC
    - a Rust video renderer about 25x faster, which was merged
  - [primary] [Cursor](https://cursor.com/blog/scaling-agents)
  - Caveat: these are self-reported research demos. The migration "still needs careful review", and the browser's quality has been publicly debated (not verified here).
- **Anthropic compiler:** Decomposition came from the test suite. Agents picked failing tests and claimed them with lock files. The author's key lessons:
  - "the verifier must be nearly perfect" or Claude solves the wrong problem
  - harness output should be terse, with details in logs that agents can grep
  - a `--fast` random test sample, because agents cannot track time
  - CI was added late, after regressions appeared

  [primary] [Anthropic](https://www.anthropic.com/engineering/building-c-compiler)
- **OpenHands:**
  - Split work into tasks of one commit or PR each that a human can validate. A single broad instruction ("port this codebase to Java 25") will stall.
  - Use dependency analysis to find independent parts, starting with leaf nodes.
  - Pair "fixers" (how-to prompts) with "verifiers" (scripts or LLM checks, e.g. compiles on JDK 25 with no deprecation warnings).
  - Such tasks are "80–90% automatable" [vendor claim].
  - [primary] [OpenHands](https://openhands.dev/blog/automating-massive-refactors-with-parallel-agents)
- **Devin coordinator:** Scopes the work, splits it into child tasks, and "reads child trajectories to see what worked … to improve future task breakdowns". [primary] [Cognition](https://cognition.com/blog/devin-can-now-manage-devins)
- **Claude Code Agent Teams:**
  - Lead creates a shared task list with dependencies that auto-unblock.
  - Teammates start from CLAUDE.md, skills and the spawn prompt, not the lead's history.
  - Recommended task size is "self-contained units that produce a clear deliverable, such as a function, a test file, or a review".
  - Optional plan mode: a teammate sends a plan approval request to the lead.
  - [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **superpowers** (obra/superpowers, original release about Oct 2025, about 322k GitHub stars shown in Nov 2026):
  1. Brainstorm, then write a design doc and get it approved.
  2. Create a git worktree and verify a clean test baseline.
  3. Write a plan of 2–5 minute tasks with exact file paths, code and verification steps.
  4. Run subagent-driven development: a fresh subagent per task, then two-stage review (spec compliance, then code quality). Critical issues block progress.
  5. Use TDD throughout.
  6. Finish the branch: merge, PR, keep or discard.

  [primary] [github.com/obra/superpowers](https://github.com/obra/superpowers)
- **Copilot custom agents:** Defined by `agents.md` in the repo, which gives a persona and standing context. GitHub analysed 2,500 agents.md files. [primary] [GitHub blog](https://github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control/)
- **Factory Missions:** Multi-step project planning that splits work into subtasks for parallel Droids. [secondary] [digitalapplied](https://www.digitalapplied.com/blog/factory-ai-multi-agent-coding-platform-review)
- **Independent versus centralised agents:** A Google Research evaluation is cited as finding that independent parallel agents amplified errors 17.2x, versus 4.4x with centralized coordination. [secondary, primary paper not verified] [getunblocked / search summary](https://getunblocked.com/blog/ai-agent-orchestration-platforms-compared/)

### Inferences
- Shared context is almost always file-based: repo instruction files (CLAUDE.md, AGENTS.md, agents.md, `.openhands/microagents`), a plan or spec doc, and a task list. Workers get a fresh context per task. Fresh starts are a deliberate anti-drift measure (Cursor judge cycles, the Anthropic bash loop, superpowers fresh subagents).
- Executable verification (tests, CI, oracles, verifiers) is what makes parallel decomposition work. Without it, every source reports regressions or agents "solving the wrong problem".

### Gaps
- Few public accounts show decomposition of a multi-service production app (as opposed to greenfield demos or migrations), with outcomes measured.

## 4. Human-in-the-loop checkpoints, and reported throughput, cost and quality

### Takeaway
The standard checkpoints are **plan approval** (Jules API, Claude Code plan mode, superpowers design and plan), **live steering and kill switches** (Mission Control, Devin coordinator, Claude agent panel), **CI gates**, and **PR review before merge**. Measured studies show modest throughput gains (about +24% merged PRs) alongside a growing review bottleneck. The headline scale numbers come from vendor research demos.

### Cited Findings
- **Plan gates are often auto-approved by default:**
  - Jules API auto-approves unless `requirePlanApproval: true`. [primary] [Jules API](https://developers.google.com/jules/api)
  - The Jules web plan reportedly auto-approves on a timer. [secondary] [rywalker](https://rywalker.com/research/jules)
  - In Claude Code agent teams, a teammate's plan is approved automatically unless its permission mode was changed after spawn. The lead, not the human, reviews it. Teammate permission prompts bubble up to the lead session. [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **Claude Code hooks as quality gates:** `TeammateIdle`, `TaskCreated` and `TaskCompleted` hooks can exit with code 2 to block completion and send feedback. Agent-to-agent messages cannot grant permissions on the user's behalf. [primary] [Agent teams docs](https://code.claude.com/docs/en/agent-teams)
- **GitHub's review checklist:**
  - Read session logs and changed files, especially shared or risky paths.
  - Check CI, and investigate failing checks rather than just restarting the agent.
  - Batch similar reviews together.
  - Restarting a misaligned run with better direction is "often faster" than letting it finish.
  - [primary] [GitHub blog](https://github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control/)
- **OpenHands checkpoints:**
  - human review of each agent PR into the integration branch
  - CI passing
  - running the code manually
  - reading for agent "shortcuts"
  - final approval into main

  Brennan runs about 5 agents at once (up from 3), limited by review load. [primary] [OpenHands](https://openhands.dev/blog/automating-massive-refactors-with-parallel-agents)
- **Cost and scale data points:**
  - Anthropic compiler: about $20k, 2B input tokens, 2 weeks, 16 agents, 100k LoC. [primary, measured by vendor]
  - Cursor: "trillions of tokens", hundreds of agents, over 1M LoC. [primary, vendor research]
  - Claude Code docs: teams use "significantly more tokens", scaling linearly with teammates. [primary]
- **Microsoft study (2026):** Developers using CLI agents (Claude Code, Copilot CLI) merged about 24% more PRs over 4 months, with a range of +14.5% to +33.7%. The authors note that this measures merged PRs, not quality, security or maintainability. [secondary; TechRepublic returned 403, figures from search snippet] [TechRepublic](https://www.techrepublic.com/article/news-ai-coding-agents-microsoft-pull-requests/)
- **Enterprise study of 802 developers:**
  - Share of PRs getting at least one human review fell from 89% to 68% as AI-authored volume grew.
  - Merge rates stayed flat and reverts declined.
  - AI PRs took about 20% longer to merge after first review.
  - [secondary, primary not located; from search summary] [TechRepublic / search results](https://www.techrepublic.com/article/news-ai-coding-agents-microsoft-pull-requests/)
- **Academic studies of agent PRs:**
  - In 8,106 fix PRs, 65% were merged: Codex 81.6% versus Copilot and Devin at about 42–43%.
  - In about 33k PRs, documentation PRs merge at 84% and CI PRs at 79%, versus bug-fix at 64% and performance at 55%.
  - [secondary summaries of arXiv work] [arXiv 2601.18749](https://arxiv.org/html/2601.18749v1), [emergentmind 2602.00164](https://www.emergentmind.com/papers/2602.00164)
  - A blog's 220k-PR figure (Claude Code 84.3% merge rate) is unverified. [danielvaughan blog](https://codex.danielvaughan.com/2026/08/04/what-220000-pull-requests-reveal-agentic-pr-task-routing-codex-cli-test-coverage-gaps/)
- **Unverified Devin claim:** "4.5x more PRs per message" for Devin Bots. [unverified marketing] [hermes-ai](https://hermes-ai.net/news/cognition-s-devin-bots-now-run-parallel-coding-sessions-hitting-2-000-pull/)

### Inferences
- Human review capacity, not agent count, is the binding constraint in production. That explains the repeated 3–5 agent guidance and the evidence of declining review coverage.
- Default auto-approval of plans in several products means a team that wants real plan gates must configure them explicitly (e.g. `requirePlanApproval`, hooks, branch protection).

### Gaps
- No independent measured case study found of a company running many parallel agents on a large multi-service production codebase, with cost per merged PR and defect rates.
- The primary sources for the Microsoft and 802-developer studies were not retrieved, so their exact authors and dates are unconfirmed.

## 5. Notable open-source orchestrators for running many coding agents

### Takeaway
Most orchestrators are thin layers over the same primitive: **one agent CLI process per git worktree/branch**, with a UI (tmux, kanban or desktop app) for launching, monitoring, diffing and merging. A few, such as superpowers and claude-flow, are methodologies or skill/prompt packs layered on Claude Code rather than process managers.

### Cited Findings
- **Vibe Kanban:**
  - An open-source kanban board that runs Claude Code, Codex, Gemini CLI, OpenCode and others in isolated worktrees.
  - The original company (Bloop) reportedly shut down and the project is community-maintained.
  - It runs agents with `--dangerously-skip-permissions` by default, which is a security concern.
  - [secondary] [agentsroom](https://agentsroom.dev/blog/best-multi-agent-coding-tools), [augmentcode list](https://www.augmentcode.com/tools/open-source-agent-orchestrators)
- **Claude Squad:** Terminal-first, using tmux plus a worktree per agent. Supports many CLIs and is AGPL. [secondary] [dev.to comparison](https://dev.to/agentiknet/9-coding-agent-orchestrators-honestly-compared-dated-sourced-and-one-of-them-is-mine-3kpk)
- **Conductor (Melty Labs):**
  - A macOS app running multiple Claude Code or Codex agents in parallel, each in a worktree.
  - Closed source, with "no programmatic surface".
  - It now advertises "Conductor Cloud".
  - [secondary] [agentsroom](https://agentsroom.dev/blog/best-multi-agent-coding-tools), [conductor.build](https://www.conductor.build/workflows/run-parallel-codexes)
- **superpowers (obra):**
  - A skills-based methodology for Claude Code: brainstorm, worktree, plan, subagent-driven development with per-task spec and quality review, TDD, finish branch.
  - About 322k stars shown on the page (Nov 2026).
  - [primary] [GitHub](https://github.com/obra/superpowers)
- **claude-flow:** No reliable primary description retrieved. One blog said it had npm warnings and was "not yet in a working state". [secondary, weak] [markferree substack](https://markferree.substack.com/p/agent-orchestration-for-the-timid)
- **Others mentioned in comparisons:** Superset, crystl, Nimbalyst, AgentsRoom. These are mostly vendor-authored comparisons, so they may be biased. [secondary] [morphllm Superset vs Cursor](https://www.morphllm.com/comparisons/superset-vs-cursor), [crystl](https://crystl.dev/blog/best-ai-agent-orchestration-tools/)
- **Background on the category:** "The Rise of Coding Agent Orchestrators". [secondary] [Aviator blog](https://www.aviator.co/blog/?p=5522)

### Inferences
- Open-source orchestrators mostly solve launch, isolation and monitoring. Planning, review and merge discipline come either from the methodology layer (superpowers-style skills) or from the human.
- For an orchestrator product, the differentiating space is the parts these tools largely leave to humans: dependency-aware scheduling, verifier-gated merges, and review-load management.

### Gaps
- claude-flow's actual feature set and maturity are unverified.
- No adoption, throughput or quality numbers found for any open-source orchestrator, other than GitHub stars for superpowers.
- The READMEs of Claude Squad, Vibe Kanban and Conductor were not fetched directly. The descriptions come from comparison articles.
