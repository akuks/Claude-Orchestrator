# Multi-Agent Orchestration Architectures and Frameworks for LLM-Based Software Development

Research date: 2026-11-20. Every source is dated where possible. The guidance has shifted a lot between late 2024 and 2026. For example, Cognition took a strong anti-multi-agent position in June 2025 and partly reversed it in April 2026.

## Q1: What are the dominant patterns, and how does each work for software development?

### Takeaway
The patterns are converging on a small set:
- **Orchestrator–worker / lead agent with subagents:** the default for decomposable work.
- **Sequential pipelines / stage gates:** encoded in code or as workflow agents.
- **Evaluator/critic (generator–reviewer) loops:** the most broadly endorsed multi-agent pattern for coding.
- **Handoffs/swarms:** suited to routing and conversation, less so to code writing.
- **Shared-state / blackboard coordination:** implemented as task lists, files and session state.

The 2023-era "software company" role simulations (MetaGPT, ChatDev) were influential, but production systems now favour one writer plus many readers and reviewers, not many parallel writers.

### Cited Findings

**Anthropic's foundational taxonomy (Dec 2024)**
- Anthropic separates **workflows**, where LLMs and tools follow predefined code paths, from **agents**, where LLMs direct their own process and tool use. — [Anthropic, Building effective agents, 2024-12-19](https://www.anthropic.com/engineering/building-effective-agents)
- It defines five workflow patterns — [same source](https://www.anthropic.com/engineering/building-effective-agents):
  - **Prompt chaining:** sequential calls with programmatic gates between steps.
  - **Routing:** classify an input, then dispatch it to a specialized follow-up.
  - **Parallelization:** either *sectioning* (independent subtasks) or *voting* (the same task run several times). Coding example: several prompts each review code for vulnerabilities.
  - **Orchestrator–workers:** a central LLM breaks the task down dynamically, delegates, and synthesizes. Coding example: "products that make complex changes to multiple files," where the edits depend on the task.
  - **Evaluator–optimizer:** a generator plus an evaluator, run in a feedback loop.
- Autonomous agents are "LLMs using tools in a loop" based on environment feedback, with stopping conditions such as a maximum iteration count. Coding example: resolving SWE-bench issues that need multi-file edits. Code suits agents because "solutions are verifiable through automated tests." — [same source](https://www.anthropic.com/engineering/building-effective-agents)

**Orchestrator–worker / lead agent with subagents**
- Anthropic's Research system uses an Opus 4 lead agent that plans and saves the plan to memory, then spawns 3–5 Sonnet 4 subagents that search in parallel. A separate CitationAgent runs at the end. The lead synthesizes results and decides whether to loop. — [Anthropic, How we built our multi-agent research system, 2025-06-13](https://www.anthropic.com/engineering/multi-agent-research-system)
- The same post says multi-agent is a poor fit for "most coding tasks," which "involve fewer truly parallelizable tasks than research." It also says agents "are not yet great at coordinating and delegating to other agents in real time." — [same source](https://www.anthropic.com/engineering/multi-agent-research-system)
- Cognition's April 2026 "Managers of agents" pattern: a manager Devin splits a larger task, spawns child Devins, and coordinates them through an internal MCP. The author calls the shape "map-reduce-and-manage." — [Cognition, Multi-Agents: What's Actually Working, 2026-04-22](https://cognition.com/blog/multi-agents-working)
- Devin Bots (reported 2026-11-17) are persistent manager agents that keep project memory, split work, and hand pieces to individual Devin instances, for projects lasting days or weeks. This comes from secondary news coverage and is unverified against the primary source. — [aiweekly](https://aiweekly.co/alerts/cognition-launches-devin-bots-to-oversee-teams-of-devin-agents); [emergent.sh](https://emergent.sh/news/cognition-ships-devin-bots-supervise-coding)

**Hierarchical teams / peer teams with a shared task list (blackboard-like)**
- Claude Code agent teams (experimental, off by default) have four parts — [Claude Code docs, Agent teams](https://code.claude.com/docs/en/agent-teams):
  - a **team lead** session;
  - **teammates**, which are separate Claude Code instances;
  - a **shared task list** with states pending, in progress and completed, plus dependencies and blocking. Claiming uses file locking, and teammates self-claim the next unblocked task;
  - a **mailbox** of JSON inbox files for direct teammate-to-teammate messaging.
- Agent teams have no nesting: "teammates cannot spawn their own teammates." — [same source](https://code.claude.com/docs/en/agent-teams)
- Recommended uses for agent teams: research and review, new modules where each teammate owns a separate piece, debugging with competing hypotheses (an adversarial "scientific debate"), and cross-layer work where frontend, backend and tests each have an owner. — [same source](https://code.claude.com/docs/en/agent-teams)
- Not recommended for agent teams: "sequential tasks, same-file edits, or work with many dependencies." The docs also advise: "Two teammates editing the same file leads to overwrites. Break the work so each teammate owns a different set of files." — [same source](https://code.claude.com/docs/en/agent-teams)

**Sequential pipelines / stage gates**
- OpenAI's code-driven patterns — [OpenAI Agents SDK docs, Multi-agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/):
  - chain agents, for example research → outline → write → critique → revise;
  - classify with structured outputs to pick the next agent;
  - run an evaluator loop with `while`;
  - run work in parallel with `asyncio.gather`.
- Google ADK provides deterministic workflow agents: SequentialAgent for ordered pipelines, ParallelAgent for fan-out, and LoopAgent for iteration. — [Google Developers Blog, Developer's guide to multi-agent patterns in ADK](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/); [Google codelab](https://codelabs.developers.google.com/devsite/codelabs/build-agents-with-adk-orchestrating-agent-workflows)
- Anthropic's long-running harness is a two-stage gate (2025-11-26) — [Anthropic, Effective harnesses for long-running agents, 2025-11-26](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents):
  - An **initializer agent** runs in the first session. It writes a feature list in JSON with a `passes` flag per feature, an `init.sh`, a `claude-progress.txt` log, and an initial git commit.
  - A **coding agent** runs every later session. It does one feature at a time, commits, and updates the progress file.

**Role-based "software company" simulations**
- MetaGPT encodes Standardized Operating Procedures (SOPs) into role prompts covering product manager, architect, project manager, engineer and QA. On its SoftwareDev benchmark it reported:

  | Metric | MetaGPT | ChatDev |
  |---|---|---|
  | Executability | 3.75 | 2.25 |
  | Runtime | 541 s | 762 s |
  | Tokens | 31,255 | 19,292 |

  — [MetaGPT, arXiv 2308.00352](https://arxiv.org/pdf/2308.00352). These numbers were reported via search snippet. The paper is from 2023, revised 2024.
- ChatDev assigns agents to roles across the waterfall lifecycle (design, coding, testing), and they talk through "chat chains." This description comes via the MetaGPT and E2EDev papers. — [MetaGPT](https://arxiv.org/pdf/2308.00352); [E2EDev, arXiv 2510.14509](https://arxiv.org/pdf/2510.14509)
- MapCoder runs four agents in sequence: recall examples, plan, code, debug. A survey reports HumanEval 93.9%, MBPP 83.1%, APPS 22.0% and CodeContests 28.5%. These figures come from a secondary survey and have not been checked against the MapCoder paper. — [Survey of Vibe Coding, arXiv 2510.12399](https://arxiv.org/pdf/2510.12399)
- HyperAgent (Sep 2024, revised Sep 2025) has four agents: Planner, Navigator, Code Editor and Executor. It claims state-of-the-art results on SWE-bench and strong results on RepoExec and Defects4J. The abstract gives no exact numbers. — [HyperAgent, arXiv 2409.16299](https://arxiv.org/abs/2409.16299)
- The 2025 E2EDev benchmark re-ran MapCoder, ChatDev and MetaGPT across six LLM backbones. I did not retrieve its results. — [E2EDev](https://arxiv.org/pdf/2510.14509)

**Debate/critic and review loops**
- Cognition (Apr 2026) reports that Devin and Devin Review iterate against each other so that "most bugs are resolved before a human opens the PR." Details — [Cognition 2026-04-22](https://cognition.com/blog/multi-agents-working):
  - The reviewer **starts with clean context** and sees only the diff.
  - A communication layer filters which review findings the coder acts on.
  - Devin Review averages about 2 bugs per PR, about 58% of them severe.
  - Drawback: many review cycles can take a while.
- Cognition's "Smart Friend" pattern: a cheaper primary model calls a stronger model as a tool, sharing a forked copy of its full context. It fails when the primary model is too weak to know when to escalate. — [same source](https://cognition.com/blog/multi-agents-working)
- In Google ADK, the generator–critic pattern is a LoopAgent containing a generator and a critic. The critic writes `output_key="feedback"` to session state. — [Google Developers Blog](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)

**Handoffs / swarms**
- In the OpenAI Agents SDK, a handoff makes the specialist "the active agent for the rest of the turn." With agents-as-tools, the manager keeps control and calls `Agent.as_tool()`. — [OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/multi_agent/)
- In LangGraph, a swarm's handoff tools return `Command(goto=agent_name, graph=Command.PARENT, update=...)`, which combines control flow and a state update. The supervisor pattern comes from `langgraph-supervisor` (`create_supervisor`) and the swarm from `langgraph-swarm`. — [LangChain docs, multi-agent](https://docs.langchain.com/oss/python/multi-agent); [langgraph-swarm reference](https://reference.langchain.com/python/langgraph-swarm)
- Cognition (Apr 2026) says parallel-writer swarms still lack meaningful adoption and calls unstructured swarms of negotiating agents "mostly a distraction." — [Cognition 2026-04-22](https://cognition.com/blog/multi-agents-working)

### Inferences
- For software development the patterns map roughly as follows:

  | Pattern | Fit for software development |
  |---|---|
  | Orchestrator–worker | Read-heavy exploration, research and parallel review. Also parallel implementation, but only with strict file ownership |
  | Pipeline / stage gates | Spec → plan → implement → test → review lifecycles, and long-running multi-session builds |
  | Critic loop | Code review and test-driven repair |
  | Handoffs | Front-door triage, not code construction |
  | Role simulations | Mostly a research lineage. Their surviving contribution is SOP-style structured artifacts (PRD, design doc, task list) passed between stages |

- A consistent rule appears across Anthropic, Cognition and Claude Code: parallelize reading, reviewing and investigation freely, but serialize writes or partition them by file or module.

### Gaps
- I found no independent 2026 head-to-head benchmark comparing these patterns on realistic app-building tasks. Vendor numbers such as Anthropic's +90.2% are for research, not coding.
- AgentVerse was not researched in detail, and no source was retrieved for it.
- Blackboard architectures as a named academic pattern for LLM coding agents: I found no specific primary source. The closest equivalents are the Claude Code shared task list and ADK `session.state`.

## Q2: How do current frameworks and vendor offerings implement these patterns as of 2026?

### Takeaway
Every major vendor now ships two kinds of orchestration side by side:
- **Deterministic/code-driven workflow primitives:** sequential, parallel, loop and graph.
- **LLM-driven delegation primitives:** subagents/agents-as-tools and handoffs.

MCP has become the standard for agent-to-tool access, and A2A for cross-vendor agent-to-agent communication. Microsoft consolidated AutoGen and Semantic Kernel into Agent Framework 1.0 (April 2026).

### Cited Findings

**Anthropic: Claude Code subagents and the Claude Agent SDK**
- Subagents are Markdown files with YAML frontmatter, stored in `.claude/agents/`, `~/.claude/agents/`, plugins, or managed settings. — [Claude Code docs, Subagents](https://code.claude.com/docs/en/sub-agents)
- Frontmatter fields include `tools`/`disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `hooks`, `memory` (user/project/local), `isolation: worktree` (a temporary git worktree) and `background`. — [same source](https://code.claude.com/docs/en/sub-agents)
- Delegation is either automatic, based on the `description` field, or explicit, via @-mention or `claude --agent`. — [same source](https://code.claude.com/docs/en/sub-agents)
- Built-in subagents: Explore (read-only, with thoroughness levels), Plan (read-only) and general-purpose. — [same source](https://code.claude.com/docs/en/sub-agents)
- Each subagent gets a fresh context: its system prompt, the delegation message, CLAUDE.md, and a git status snapshot. "Only the final summary returns." Forks are the exception and inherit the parent conversation. — [same source](https://code.claude.com/docs/en/sub-agents)
- Limits: subagents can nest up to 3 levels by default (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`) and run up to 20 concurrently by default. They can be resumed through `SendMessage`. — [same source](https://code.claude.com/docs/en/sub-agents)
- **This has changed since mid-2025.** In June 2025 Cognition wrote that Claude Code subagents never ran in parallel with the main agent and usually only answered questions without writing code. — [Cognition, Don't build multi-agents, 2025-06-12](https://cognition.com/blog/dont-build-multi-agents). The current docs describe background/concurrent subagents, worktree isolation and agent teams. — [Claude Code docs](https://code.claude.com/docs/en/sub-agents)
- Agent teams also offer quality-gate hooks: `TeammateIdle`, `TaskCreated` and `TaskCompleted`. In each, exit code 2 blocks the action and returns feedback. Agent teams do not run in non-interactive `-p` or Agent SDK sessions. — [Claude Code docs, Agent teams](https://code.claude.com/docs/en/agent-teams)
- Anthropic's 2024 guidance names the Claude Agent SDK as one of several frameworks. It warns that abstractions "obscure the underlying prompts and responses" and recommends starting with direct API calls. — [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

**OpenAI Agents SDK**
- Orchestration can be LLM-driven or code-driven, and the two can be mixed. Code-driven orchestration is described as "more deterministic and predictable" in speed, cost and performance. — [OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/multi_agent/)
- Agents-as-tools fits when one agent should own the final answer and enforce shared guardrails. Handoffs fit when the specialist should respond directly. The two can be combined, for example triage → handoff → a specialist that uses agents-as-tools. — [same source](https://openai.github.io/openai-agents-python/multi_agent/)
- The page I fetched did not cover handoff internals (transfer tool naming, input filters).

**LangGraph / LangChain**
- Documented patterns: Subagents (supervisor), Handoffs, Skills, Router, and Custom workflow (a LangGraph graph mixing deterministic and agentic nodes). — [LangChain docs, Multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent)
- Quantified trade-offs from the same docs:
  - Subagents cost one extra call per request.
  - Stateful patterns (Handoffs, Skills) save 40–50% of calls on repeat requests.
  - In the multi-domain scenario, Subagents used the fewest tokens (~9K vs ~14K+ for handoffs), because each subagent sees only its own context.
  - Handoffs cannot consult domains in parallel.
  — [same source](https://docs.langchain.com/oss/python/langchain/multi-agent)
- Agent nodes return `Command` objects that combine routing and state updates. — [LangChain docs](https://docs.langchain.com/oss/python/multi-agent); [LangChain blog on Command](https://www.langchain.com/blog/command-a-new-tool-for-multi-agent-architectures-in-langgraph)

**Microsoft Agent Framework (successor to AutoGen and Semantic Kernel)**
- It has five built-in orchestrations — [Microsoft Learn, Workflow orchestrations, updated 2026-08-25](https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/orchestrations/overview):
  - **Sequential**
  - **Concurrent**
  - **Handoff**
  - **Group Chat**, a shared conversation
  - **Magentic**, where a manager agent dynamically coordinates specialists. It comes from AutoGen's Magentic-One.

  All of them support human-in-the-loop through approval-required tools.
- Agent Framework 1.0 reached GA around 2026-04-02/03 for .NET and Python. It adds a graph-based workflow engine and supports MCP and A2A. AutoGen and Semantic Kernel receive bug and security fixes but no new features. Sources are secondary and differ on the exact date and on whether AutoGen is "archived." — [Visual Studio Magazine, 2026-04-06](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx); [VentureBeat](https://venturebeat.com/ai/microsoft-retires-autogen-and-debuts-agent-framework-to-unify-and-govern)

**CrewAI**
- **Crews** are role-based agent teams with a sequential or hierarchical *process*. **Flows** are the event-driven layer that "defines the steps, the logic, and how data moves." The recommended pattern is Flows for overall orchestration with Crews for complex subtasks. — [CrewAI docs, Introduction](https://docs.crewai.com/en/introduction)
- The hierarchical process auto-creates a manager agent, or accepts one via `manager_agent`/`manager_llm`, to delegate and validate. — [CrewAI FAQ](https://docs.crewai.com/en/enterprise/resources/frequently-asked-questions)
- Third parties report that the manager–worker process is unreliable in practice. — [Towards Data Science](https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/)

**Google ADK and A2A**
- ADK composes agents into a `sub_agents` hierarchy, with workflow agents SequentialAgent, ParallelAgent and LoopAgent. — [Google Developers Blog](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)
- Agents share data through `session.state`: `output_key` saves an agent's final response under a key, and downstream agents read it. — [Google Developers Blog](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/); [codelab](https://codelabs.developers.google.com/devsite/codelabs/build-agents-with-adk-orchestrating-agent-workflows)
- A community guide warns that parallel agents must use distinct `output_key` values, and recommends `max_iterations` on loops (secondary source).
- I could not retrieve the official ADK multi-agent page, so `transfer_to_agent`/AgentTool details are from my background knowledge only and are not cited here.
- A2A history: Google donated it to the Linux Foundation in June 2025. — [Techstrong.ai](https://techstrong.ai/articles/google-donates-a2a-protocol-to-linux-foundation/)
- A2A mechanics: agents publish an Agent Card (JSON at `/.well-known/agent-card.json` listing skills, endpoint and authentication) and exchange stateful tasks. — [specification.website](https://www.specification.website/spec/agent-readiness/a2a-agent-cards/)
- Reported but unverified: v1 in January 2026 and governance under the LF Agentic AI Foundation alongside MCP. These claims come only from secondary sources. — [metavert.io](https://metavert.io/a2a-protocol); [jacar.es](https://jacar.es/en/agent-to-agent-protocols-the-next-open-layer/)

**MCP's role**
- MCP is the agent-to-tool layer and A2A the agent-to-agent layer. Sources describe them as complementary. — [metavert.io, MCP vs A2A](https://metavert.io/compare/model-context-protocol-vs-a2a)
- Concrete uses in multi-agent systems:
  - Claude Code subagents can have their own scoped `mcpServers`. — [Claude Code docs](https://code.claude.com/docs/en/sub-agents)
  - Cognition's manager Devin coordinates child Devins over an internal MCP. — [Cognition 2026-04-22](https://cognition.com/blog/multi-agents-working)
  - Anthropic's harness gives the coding agent the Puppeteer MCP server for end-to-end testing. — [Anthropic harness post](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- Devin Desktop (June 2026) coordinates third-party agents (Codex, Claude Agent, OpenCode) through the ACP standard. This is from a secondary source. — [washinmura summary](https://aeo.washinmura.jp/ai/cognition-ai-inc/en/news/2026-06-09-cognition-%E3%82%A8%E3%83%B3%E3%82%B8%E3%83%8B%E3%82%A2%E3%83%AA%E3%83%B3%E3%82%B0%E3%83%81%E3%83%BC%E3%83%A0%E5%90%91%E3%81%91%E3%81%AB%E3%82%A8%E3%83%BC%E3%82%B8%E3%82%A7%E3%83%B3%E3%83%88-%E3%82%B3%E3%83%9E%E3%83%B3%E3%83%89%E3%82%BB%E3%83%B3%E3%82%BF%E3%83%BC)

### Inferences
- The frameworks differ mainly in where state lives:
  - **LangGraph:** a typed graph state plus `Command`.
  - **ADK:** `session.state` keys.
  - **Claude Code:** the filesystem, git, a task-list directory and JSON mailboxes.
  - **OpenAI:** the conversation passed through handoffs.
  - **Microsoft Agent Framework:** workflow executors and edges with checkpoints. I did not fetch detail on these.
- For a software-development orchestrator, the Claude Code model of filesystem/git as shared state, worktree isolation and a dependency-aware task list maps most directly onto real repositories.

### Gaps
- Microsoft Agent Framework workflow internals (executors, edges, checkpointing) were not fetched.
- OpenAI handoff internals (input filters, history passing) were not fetched.
- A2A v1 and AAIF governance claims are unverified against Linux Foundation primary sources.
- I did not verify whether OpenAI or Google ship coding-specific multi-agent products in 2026 that are comparable to Claude Code agent teams.

## Q3: How do these systems pass context between agents, and what do vendors recommend about context engineering?

### Takeaway
The consensus mechanism is that each worker gets an isolated fresh context, plus an explicit, detailed task brief, and returns a condensed summary. Durable state goes to external artifacts (files, git, task lists, memory) rather than being relayed through messages. The main dissent is Cognition (2025): share full traces, because actions carry implicit decisions. Cognition's 2026 resolution is to keep writes single-threaded and use clean context deliberately, for reviewers.

### Cited Findings

**Condensed summaries and clean contexts**
- Subagents return condensed results, "often 1,000–2,000 tokens," after using tens of thousands of tokens exploring. — [Anthropic, Effective context engineering for AI agents, 2025-09-29](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Claude Code subagents get only their system prompt, the delegation message, CLAUDE.md, skills and git status. Only the final summary returns. The docs say: use subagents when "the work is self-contained and can return a summary." Use the main conversation when "several phases share significant context." — [Claude Code docs, Subagents](https://code.claude.com/docs/en/sub-agents)
- Agent teams: teammates load CLAUDE.md, MCP and skills, plus the spawn prompt, but "the lead's conversation history does not carry over." The docs recommend putting task-specific details in the spawn prompt. — [Claude Code docs, Agent teams](https://code.claude.com/docs/en/agent-teams)

**Artifacts and filesystem instead of relays**
- Subagents can "store outputs in external systems and pass lightweight references back," which avoids losing information by copying it through the coordinator. The lead saves its plan to Memory because context beyond 200K tokens is truncated. — [Anthropic multi-agent research, 2025-06-13](https://www.anthropic.com/engineering/multi-agent-research-system)
- **Delegation briefs:** each subagent needs an objective, an output format, tool guidance and task boundaries. Vague briefs caused duplicated work. — [same source](https://www.anthropic.com/engineering/multi-agent-research-system)
- **Cross-session handoff artifacts for coding:** a feature list in JSON (chosen because models are less likely to overwrite JSON inappropriately than Markdown), `claude-progress.txt`, `init.sh` and descriptive git commits. — [Anthropic, Effective harnesses, 2025-11-26](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

**Context-engineering techniques**
- **Compaction:** summarize the conversation while keeping architectural decisions, unresolved bugs and implementation details, then continue with the summary plus the 5 most recently accessed files. Clearing old tool results is the lightest form. — [Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **Structured note-taking:** examples are a NOTES.md file, to-do lists, and a memory tool (public beta with Sonnet 4.5). — [same source](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **Just-in-time retrieval:** keep file paths and queries, and load content on demand with glob/grep. Hybrid approaches are recommended. — [same source](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **Context rot:** recall degrades as context fills, so context is a finite resource. — [same source](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

**State passing in other frameworks**
- ADK: `output_key` → `session.state`. — [Google Developers Blog](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)
- LangGraph: tool calls update state variables, and `Command` carries state updates across graphs. Subagents are stateless per call. Skills stay in the history. — [LangChain docs](https://docs.langchain.com/oss/python/langchain/multi-agent)

**The Cognition dissent and its 2026 revision**
- Cognition (June 2025) set out two principles: (1) "Share context, and share full agent traces, not just individual messages"; (2) "Actions carry implicit decisions." In their Flappy Bird example, two subagents produced stylistically incompatible pieces: a Mario-like background and a mismatched bird. They recommended a single-threaded linear agent plus a context-compression model. — [Cognition, Don't build multi-agents, 2025-06-12](https://cognition.com/blog/dont-build-multi-agents)
- Cognition (April 2026) partly reversed this. Clean context is now deliberately used for reviewers, the Smart Friend gets a forked full context, and managers fail when they "assume shared state with children" or give overly prescriptive instructions. — [Cognition, 2026-04-22](https://cognition.com/blog/multi-agents-working)

### Inferences
- A practical synthesis for a software orchestrator:
  - Pass **specs and decisions** (design docs, interface contracts, style guides, CLAUDE.md) to every worker up front, which addresses the "implicit decisions" problem.
  - Pass **pointers** (file paths, branch/worktree names, task IDs), not file contents.
  - Get back **short structured summaries**.
  - Persist progress in **git plus a machine-readable task/feature list**.
- The 2025 Cognition and Anthropic positions are less contradictory than they look. Both say context isolation is harmful when workers make interdependent *write* decisions and beneficial for read-only exploration and independent review.

### Gaps
- I found no quantitative study comparing full-trace sharing with summary handoff for coding agents.
- Anthropic's memory tool details and current status (GA or beta in 2026) were not verified.

## Q4: What do practitioners recommend about choosing a pattern for a complex application?

### Takeaway
Start with a single agent or a deterministic workflow. Add agents only where work is genuinely parallel or context-heavy, and prefer code-driven orchestration of the outer lifecycle. Decompose along boundaries that minimize shared write decisions (separate files/modules/layers with agreed interfaces). Reserve multi-agent for exploration, review/verification and independent modules. Token cost (about 15× chat) must be justified by task value.

### Cited Findings

**Start simple and add agents only where they pay off**
- "Find the simplest solution possible, and only increase complexity when needed". Many tasks need only a single optimized LLM call. Use workflows for well-defined tasks and agents when the steps cannot be predicted. — [Anthropic, Building effective agents, 2024-12-19](https://www.anthropic.com/engineering/building-effective-agents)
- Agents use about 4× the tokens of chat, and multi-agent systems about 15×. Token usage alone explained about 80% of performance variance on BrowseComp. Multi-agent "requires tasks where the value of the task is high enough." — [Anthropic multi-agent research, 2025-06-13](https://www.anthropic.com/engineering/multi-agent-research-system)
- Scale effort to complexity: 1 agent with 3–10 tool calls for simple tasks, 10+ subagents for complex research. — [same source](https://www.anthropic.com/engineering/multi-agent-research-system)
- LangChain: "a single agent with the right tools and prompt can often match" a multi-agent system. It suggests multi-agent when — [LangChain docs](https://docs.langchain.com/oss/python/langchain/multi-agent):
  - a single agent has too many tools;
  - specialized long prompts are needed;
  - sequential capability unlocking is needed;
  - teams need independent ownership;
  - subtasks can run concurrently.
- OpenAI: code-driven orchestration is more predictable in speed, cost and performance. LLM-driven orchestration suits open-ended tasks. — [OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/multi_agent/)

**Decomposition boundaries for software**
- The Claude Code agent teams docs recommend:
  - 3–5 teammates and 5–6 tasks per teammate;
  - self-contained tasks "that produce a clear deliverable, such as a function, a test file, or a review";
  - each teammate owning a different set of files;
  - starting with research/review before parallel implementation;
  - layer-based splits (frontend/backend/tests).

  The docs note that "Three focused teammates often outperform five scattered ones." — [Claude Code docs, Agent teams](https://code.claude.com/docs/en/agent-teams)
- Claude Code subagent docs: keep the main conversation for iterative back-and-forth, phases that share context, quick targeted changes, and latency-sensitive work. — [Claude Code docs, Subagents](https://code.claude.com/docs/en/sub-agents)
- Cognition (2026): "writes stay single-threaded." Multi-agent works best when extra agents contribute intelligence (review, escalation, planning) rather than actions. — [Cognition 2026-04-22](https://cognition.com/blog/multi-agents-working)

**Long-running builds and verification**
- For long-running full-stack builds, Anthropic recommends:
  - an incremental, one-feature-at-a-time loop with an explicit feature list as a "goal contract";
  - end-to-end verification with browser automation.

  Observed failures without this structure: one-shotting too much, declaring victory early, and marking features done without end-to-end tests. Anthropic leaves open whether specialized testing, QA or cleanup agents would beat one general coding agent. — [Anthropic harness, 2025-11-26](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- Lean on verifiability: code "solutions are verifiable through automated tests," so test feedback should drive evaluator loops. Tool and interface design (for example, absolute file paths) mattered more than the overall prompt. — [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Cognition noted that large public agent demos (a 200k-LOC browser, a 100k-LOC C compiler) succeeded partly because they had simple, verifiable success criteria that most real software lacks. — [Cognition 2026-04-22](https://cognition.com/blog/multi-agents-working)

### Inferences
- A defensible default architecture for a complex app as of late 2026:
  1. **Deterministic outer pipeline with stage gates:** spec/PRD → architecture and interface contracts → task graph → implementation → test → review → integrate. Gates are tests or human approval.
  2. **Inside implementation:** an orchestrator that dispatches tasks from a dependency-aware task list. Parallelize only tasks with disjoint file ownership, each in its own git worktree or branch, and serialize anything touching shared files.
  3. **Read-only subagents** for codebase exploration and research, returning short summaries.
  4. **A clean-context reviewer/critic loop** against the diff and test results before merge.
  5. **Persisted state** in git, a JSON feature/task list and a progress log, so any stage can resume after a context reset.
- Role-simulation ("software company") designs are best treated as a source of artifact templates (PRD, design doc, test plan), not as a runtime chat topology.

### Gaps
- Hamel Husain and Simon Willison write-ups on multi-agent coding were not retrieved in this pass, so no citations from them are included.
- I found no rigorous 2026 empirical data on optimal parallelism (number of concurrent writers) for real codebases. Vendor guidance (3–5 teammates) is heuristic.
