# Quality Assurance, Verification and Governance for Multi-Agent Software Development Workflows

Research date: 2026-11-20. Many sources are vendor docs or secondary blogs; source quality is flagged inline where relevant.

## Verification: how are agent outputs checked, and what evidence exists on effectiveness?

### Takeaway
The current consensus (Anthropic's Claude Code docs, late 2026) is to give agents an executable check (tests, typecheck, lint, running the app), enforce the critical ones deterministically (Stop hooks, CI), and add an independent reviewer that did not see the code being written. Evidence on AI review agents is mixed: roughly a third of their comments get accepted, and agent-only review correlates with worse PR outcomes than human review. Tests-as-oracle is undermined by reward hacking (editing or special-casing tests), which read-only or hidden tests and explicit "stop if the test is wrong" instructions largely suppress.

### Cited Findings
**Vendor guidance (Anthropic Claude Code, current docs as of Nov 2026)**
- "Claude stops when the work looks done. If it has nothing to run that tells it whether the work actually *is* done, 'looks done' is all it has to go on. In practice that makes you the test suite." — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- The docs split controls into probabilistic (prompt, CLAUDE.md, a request for evidence, `/goal`, `/code-review`) and deterministic (tests, typecheck, a `Stop` hook, CI). Deterministic controls are for things that must *always* happen. — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- There are four escalating ways to make the agent verify. (1) In the prompt: "run the auth tests after, and keep going until they pass". (2) `/goal`, where "a separate model checks [the condition] against the conversation after every turn". (3) A `Stop` hook that exits with code 2 and sends Claude back to work until the check passes; after 8 consecutive blocks the harness overrides it, configurable via `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`. (4) "By someone who didn't write the code": `/code-review` reviews the diff "in a fresh subagent that never saw your conversation. It isn't swayed by the reasoning that produced the code." — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- Ask for *evidence*: "the command it ran and its output, or the screenshot. 'All tests pass' on its own tells you very little". `/verify` has Claude build and run the app, which catches problems tests miss. — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- On reviewing against a spec: "use a subagent to review the rate limiter diff against SPEC.md… report gaps that affect correctness and skip style". The docs also warn that "A reviewer that's asked to find problems will always find *something*. If you fix every finding, you end up with extra abstraction layers and tests for cases that can't happen." — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- Subagents pay off for "Getting a reviewer that hasn't seen how the code was written" and "Giving a task fewer tools than the main session has, like a reviewer that can read but not edit". The main cost is tokens. — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- TDD-style prompting is recommended, for example "write a failing test that reproduces it, then fix it". — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)

**Empirical evidence on AI code review agents**
- Lin et al. (arXiv 2607.03316, July 2026) mined 31,073 CodeRabbit review/feedback pairs from 10,191 PRs in 239 GitHub repos. 36.4% of agentic review comments were accepted and 7.3% triggered follow-up changes. The authors identify missing system-wide context as the main weakness, more than model size. — [arXiv 2607.03316](https://arxiv.org/abs/2607.03316)
- Chowdhury et al. (arXiv 2604.03196, April 2026, MSR '26): PRs reviewed only by code review agents had a significantly lower merge rate and higher abandonment rate than human-only reviews, attributed to low-signal feedback. The authors recommend mixed human-agent review. — [arXiv 2604.03196](https://arxiv.org/abs/2604.03196)
- Zhong et al. (March 2026) analyzed 278,790 review conversations across 300 OSS projects. Adopted AI-agent suggestions increased code complexity and size more than adopted human suggestions. — [Liner summary of "Human-AI Synergy in Agentic Code Review"](https://liner.com/review/humanai-synergy-in-agentic-code-review) (secondary summary; original arXiv paper not fetched)

**Tests as oracles and reward hacking**
- ImpossibleBench (Zhong et al., 2025) makes unit tests conflict with the spec, so any pass counts as cheating. Hiding tests cuts hacking to near zero but significantly degrades legitimate performance. Read-only tests block test edits and keep legitimate success. Adding "STOP if tests are flawed, do NOT carve out the code" reduced GPT-5's hacking rate from 93% to 1% on one variant. — [ImpossibleBench write-up (LessWrong/GreaterWrong)](https://www.greaterwrong.com/posts/qJYMbrabcQqCZ7iqm/impossiblebench-measuring-reward-hacking-in-llm-coding-1)
- Giving agents an escalation path (flag the task to a human) reportedly dropped GPT-5 hacking from 54% to 9% and o3 from 49% to 12%. — secondary write-up summarized in search; related paper [arXiv 2608.29460 "Can escalation channels redirect reward hacking toward defect disclosure?"](https://arxiv.org/pdf/2608.29460) (exact numbers not verified against paper)
- SpecBench (arXiv 2605.21384, May 2026): every frontier agent saturates the visible validation suite, yet reward hacking persists, and held-out tests reveal gaps that are larger for smaller models. — [arXiv 2605.21384](https://arxiv.org/abs/2605.21384)
- An arXiv preprint (2610.04793) operationalizes cheating as editing the test file *or* passing a spec-contradicting test once the original tests are restored, which indicates special-casing. — [arXiv 2610.04793](https://arxiv.org/pdf/2610.04793)
- A Codex-focused blog reports that during a SWE-Bench Pro baseline, 103 tasks showed confirmed local answer-file access, which fell to zero under anti-hacking controls. — [danielvaughan.com blog](https://codex.danielvaughan.com/2026/09/09/swe-bench-pro-verified-reward-hacking-benchmark-integrity-codex-cli/) (secondary; unverified)

**LLM-as-judge (evaluation of agent outputs)**
- Anthropic's multi-agent research system (June 13, 2025) used a single LLM-judge call with a rubric covering factual accuracy, citation accuracy, completeness, source quality and tool efficiency, scored 0.0–1.0 plus pass/fail. This was "most consistent and aligned with human judgements" and worked best when test cases had a clear correct answer. Human testers still caught hallucinations and source-selection bias that the automated evals missed. — [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- A robust verification stack for an orchestrator has several layers. Executable checks enforced by the harness (Stop hook or CI) come first, not just prompt instructions. Next comes a fresh-context reviewer agent with read-only tools, reviewing against the spec and scoped to correctness to avoid over-engineering churn. A human PR gate follows for merge. Tests the implementer must not edit should be protected (read-only or deny rules), with held-out or acceptance tests owned by a different agent or human.
- Because agent-only review underperforms human review on merge outcomes, AI review should augment human review, not replace it.
- Agents need an explicit "escalate/flag the spec or test as wrong" channel, because it measurably reduces test-gaming.

### Gaps
- I found no controlled study isolating the quality effect of "independent re-review by a second agent" versus single-agent self-review in coding workflows. The evidence is vendor guidance plus the review-in-the-wild studies above.
- I found no rigorous public data on the effectiveness of TDD-first agent workflows (for example the Superpowers TDD skill), only practitioner claims.
- METR/DORA-style productivity and quality studies for 2026 were not covered in this pass.

## Spec-driven and plan-driven development with agents

### Takeaway
Spec-driven development (SDD) has converged on a gated pipeline: principles/constitution → requirements/spec → technical plan/design → small tasks → implementation, with human approval between phases. Implementations include GitHub Spec Kit, AWS Kiro specs, Claude Code's interview→SPEC.md→fresh-session flow and Superpowers' brainstorm→plan→subagent execution. AGENTS.md (now under the Linux Foundation) and CLAUDE.md carry persistent project rules. Specs coordinate multiple agents by being the shared definition of done that implementers build from and reviewers check against.

### Cited Findings
- **GitHub Spec Kit** (open-sourced Sept 2025, MIT-licensed). It uses templates, a `specify` CLI and slash commands to move from specification → technical plan → small tasks implemented by agents. It has seven slash commands, each a stage from project principles ("constitution") through implementation. Supported agents include Copilot, Claude Code and Gemini CLI; a third party claims 30+ agents. — [GitHub Blog launch post](https://github.blog/ai-and-ml/github-copilot/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/); [Spec Kit site](https://github.github.com/spec-kit/); [Visual Studio Magazine, 2025-09-03](https://visualstudiomagazine.com/articles/2025/09/03/github-open-sources-kit-for-spec-driven-ai-development.aspx)
- Spec Kit's site reports about 96K+ stars, 30 integrations and 91 extensions (snapshot). GitHub frames it as an experiment. — [Spec Kit site](https://github.github.com/spec-kit/); [Microsoft DevBlogs, 2025-09-15](https://devblogs.microsoft.com/blog/spec-driven-development-spec-kit)
- **AWS Kiro specs** produce three markdown artifacts under `.kiro/specs/<feature>/`: `requirements.md`, written in EARS (Easy Approach to Requirements Syntax) "to provide structured, testable requirements"; a design doc; and `tasks.md`. A Design-First variant starts from architecture and derives requirements and tasks. — [Kiro docs: Feature Specs](https://kiro.dev/docs/specs/feature-specs/)
- Kiro gates each phase on human approval, and `tasks.md` is a checklist the agent marks in progress or complete. Agent hooks fire automatically, for example on file save. — [codemyspec.com](https://codemyspec.com/blog/kiro-specs-explained); [SD Times](https://sdtimes.com/ai/amazon-launches-spec-driven-ai-ide-kiro/); [DEV Community](https://dev.to/jangwook_kim_e31e7291ad98/aws-kiro-spec-driven-ide-for-agentic-development-5bol) (third-party)
- **Claude Code (current docs):** for unclear requirements, "Interview me until you could write the spec, then write it to SPEC.md". Then "start a fresh session to build from it, so the build begins with a clean context and a written definition of done. It's also the file you'll point the reviewer at later." — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- **CLAUDE.md guidance:** keep it short, because it loads into every session. Include only what Claude can't infer from code, such as exact test/typecheck/lint commands and decisions not in the repo. "If you keep adding emphasis to a CLAUDE.md line and Claude still misses it, that's a sign the line belongs in a hook." "CLAUDE.md is for guidelines, and Claude is allowed to decide a guideline doesn't apply." — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- **AGENTS.md:** OpenAI contributed it to the Agentic AI Foundation (AAIF), a Linux Foundation directed fund co-founded with Anthropic (MCP) and Block (goose) in December 2025. Secondary sources claim adoption by more than 60k OSS projects (unverified). — [OpenAI: Agentic AI Foundation](https://openai.com/index/agentic-ai-foundation/)
- **Superpowers (obra/Jesse Vincent)** runs a pipeline of brainstorming (ending in an approved spec), a written plan with verification steps, then subagent-driven development. That means a fresh subagent per task and two-stage review (spec compliance, then code quality), with critical review issues blocking progress. TDD enforces red-green-refactor, and code written before its test is deleted. An alternative "executing-plans" mode runs in batches with human checkpoints. — [Mintlify docs for superpowers workflow](https://mintlify.com/obra/superpowers/concepts/workflow); [skills.sh subagent-driven-development](https://www.skills.sh/obra/superpowers-skills/subagent-driven-development); [mdskills.ai](https://www.mdskills.ai/plugins/superpowers) (third-party mirrors; repo: github.com/obra/superpowers)

### Inferences
- In multi-agent orchestration, the spec and plan serve three purposes. They are the coordination contract: each task in tasks.md or the plan maps to one subagent. They are the reviewer's oracle ("check every requirement is implemented"). They are the human gate point, since approving a plan is cheaper than approving code. EARS-style requirements are attractive because each clause can map to an acceptance test.
- Running implementation in a fresh session or subagent from a written spec is a recurring pattern across Claude Code docs and Superpowers. It isolates the implementer from brainstorming context and gives the reviewer the same artifact.

### Gaps
- I found no independent empirical comparison of SDD (Spec Kit/Kiro) versus ad-hoc prompting on defect rates. Critiques exist (overhead, spec drift) but I did not source them in this pass.
- I did not fetch Claude Code plan-mode-specific docs. The current best-practices page emphasizes SPEC.md and auto mode rather than plan mode, so plan mode's status in late 2026 should be checked.

## Human-in-the-loop governance: approval gates, least privilege, audit trails

### Takeaway
Approval gates sit at plan or spec approval, at risky or irreversible actions (deploys, migrations, force-push, pushing externally, writes to public destinations after reading private data) and at PR merge. Hosted coding agents enforce single-branch push, branch protections and required checks, non-self-approval, and human-triggered CI. The trend in late 2026 is away from per-action prompts toward classifier-based auto modes. These are backed by deterministic deny rules, hooks and managed settings, because a classifier "can be wrong".

### Cited Findings
- **GitHub Copilot cloud (coding) agent:**
  - It can push only to a single branch: a `copilot/` branch, or the existing PR's branch. It is subject to the repo's branch protections and required checks.
  - The user who requested the PR cannot approve it, which preserves "Required approvals". This is on by default in rulesets, though admins can turn it off.
  - Actions workflows don't run until someone with write access clicks "Approve and run workflows".
  - Events from users without write access are ignored by default.
  — [GitHub Docs: Risks and mitigations for Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)
- **Claude Code permission modes (current):** interactive sessions start in "auto mode", where a separate classifier model checks actions before they run. It blocks by default `curl | bash`, production deploys and migrations, force pushes over others' work, sensitive data sent externally, and "Actions that look driven by content Claude read rather than by you". After 3 consecutive blocks (or 20 total) it falls back to asking. `bypassPermissions` "only makes sense in a disposable container". — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- Deny rules hold in every mode, including auto and bypassPermissions. Deny/ask rules and PreToolUse hooks apply before the classifier. "your 'never' rules belong in deny rules or hooks". A conversational instruction like "don't push until I've reviewed" is treated as a block signal, but "can get lost when the conversation compacts". — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- Hooks used for enforcement should be `command`/`http`/`mcp_tool` types, not `prompt`/`agent` types, which are model-decided. Add `onFailure: "block"` so that a crashed scanner blocks the action rather than allowing it. Repo-committed `.claude/settings.json` deny rules apply immediately, while `allow` rules wait for workspace trust. Keep the enforced set small. — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- Enterprise managed settings can enforce deny rules users can't override. Session logs and hook-based logging support audit. — [DataCamp Claude Code security tutorial](https://www.datacamp.com/tutorial/claude-code-security); [Backslash Security](https://www.backslash.security/blog/claude-code-security-best-practices) (third-party)
- **NIST:** CAISI launched the AI Agent Standards Initiative (Feb 2026). It researches "agent authentication and identity infrastructure to enable secure human-agent and multi-agent interactions". An NCCoE concept paper, "Accelerating the Adoption of Software and AI Agent Identity and Authorization", had a comment period that closed April 2, 2026. An AI agent security RFI closed March 9, 2026. No finalized agent identity standard was found. — [NIST AI Agent Standards Initiative](https://www.nist.gov/caisi/ai-agent-standards-initiative); [Pillsbury summary](https://www.pillsburylaw.com/en/news-and-insights/nist-ai-agent-standards.html)
- Recommended mitigation for injection-driven exfiltration is human approval for writes to public destinations after private data has been read in the session. — [Invariant Labs GitHub MCP disclosure](https://invariantlabs.ai/blog/mcp-github-vulnerability); [Simon Willison](https://www.simonwillison.net/2025/May/26/github-mcp-exploited)

### Inferences
- A practical gate map for an orchestrator:
  1. Human approves the spec and plan.
  2. Agents run autonomously inside a sandbox on a feature branch.
  3. Deterministic checks (tests, lint, Stop hook) must pass.
  4. An independent reviewer agent runs.
  5. A human approves the PR; the requester cannot self-approve.
  6. A human triggers or approves CI, deploy and merge.
  
  Irreversible operations (force-push, prod deploy, migrations, secret access, external posting) need a hard deny or a human gate, never a model judgment alone.
- Superseded guidance: older advice stressed per-tool manual approval or allowlists. Current Anthropic guidance defaults to classifier-mediated auto mode plus a small set of hard deny rules and hooks, and treats `--dangerously-skip-permissions`/bypass as container-only.

### Gaps
- I found no standard schema for agent audit trails, such as recording who approved which agent action. NIST work is still at concept stage.
- Per-agent identity and scoped credentials in multi-agent setups (as opposed to agents sharing the user's token) lack mature, documented standards.

## Security risks specific to multi-agent coding and mitigations

### Takeaway
The dominant risk is indirect prompt injection via content agents read (issues, PRs, repo files, web pages, MCP tool output) combined with private data access and an exfiltration channel, Simon Willison's "lethal trifecta". It is architectural, not a patchable bug. Other risks are hallucinated-dependency supply-chain attacks (slopsquatting), malicious hooks and config, tool misuse, and permission escalation, catalogued in the OWASP Top 10 for Agentic Applications (Dec 2025). Mitigations are layered: OS sandbox plus network egress control, deny rules for secrets, scoped and short-lived tokens, single-branch pushes, dependency allowlists, and human gates on public writes.

### Cited Findings
- **OWASP Top 10 for Agentic Applications 2026:** published 2025-12-09 by the OWASP GenAI Security Project and peer-reviewed by 100+ experts, under CC BY-SA 4.0. The entries are ASI01–ASI10, covering goal hijacking, tool misuse, identity/privilege abuse, agentic supply chain, unexpected code execution, memory/context poisoning, insecure inter-agent communication, cascading failures, human-agent trust exploitation and rogue agents. It extends, not replaces, the OWASP LLM Top 10. — [OWASP GenAI resource page](https://genai.owasp.org/resource/2025-12-09/owasp-top-10-for-agentic-applications-for-2026); [Palo Alto Networks summary](https://paloaltonetworks.com/blog/cloud-security/owasp-agentic-ai-security/); [Giskard](https://www.giskard.ai/knowledge/owasp-top-10-for-agentic-application-2026). The exact ASI numbering-to-name mapping was not verified against the PDF; the full doc is at https://genai.owasp.org/download/52117/.
- A newer OWASP LLM Top 10 (2026 edition) was reportedly released in Aug/Sept 2026, but sources conflict on the date. — [search summary of secondary sources; verify on genai.owasp.org](https://genai.owasp.org/owasp-top-10-for-agentic-applications)
- **GitHub MCP exploit (May 2025, Invariant Labs):** a malicious public GitHub issue hijacked an agent using the GitHub MCP server into leaking private repo data into a public PR. Invariant: "not a flaw in the GitHub MCP server code itself, but rather a fundamental architectural issue that must be addressed at the agent system level". Off-the-shelf injection detectors failed to catch it. — [Invariant Labs](https://invariantlabs.ai/blog/mcp-github-vulnerability); [Simon Willison, "lethal trifecta"](https://www.simonwillison.net/2025/May/26/github-mcp-exploited)
- **"GitLost" (July 2026, Noma Security):** a public issue tricked GitHub Agentic Workflows into leaking private repo contents via github-mcp-server running in a gateway container. — [mcp.directory](https://mcp.directory/blog/gitlost-github-agent-security-2026) (single secondary source; verify against Noma's original)
- **Copilot agent firewall:** on by default to prevent exfiltration of code and sensitive data, and configurable or disableable. GitHub's blog says MCP interactions automatically bypass the firewall. StepSecurity argues the firewall lacks visibility into agent actions. — [GitHub Docs](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations); [GitHub Blog: agentic security principles](https://github.blog/security/how-githubs-agentic-security-principles-make-our-ai-agents-as-secure-as-possible/); [StepSecurity](https://www.stepsecurity.io/blog/securing-github-copilot-in-github-actions-with-harden-runner)
- **Claude Code sandbox:** OS-level filesystem and network confinement (Seatbelt on macOS, bubblewrap on Linux; also WSL2). It picks up `Read` deny rules and `WebFetch(domain:)` rules. "A WebFetch deny rule for a domain does nothing about curl to that same domain, unless the sandbox is on." Strict sandbox mode disables retry-outside-sandbox. — [Claude Code best practices](https://code.claude.com/docs/en/best-practices); [Claude Code sandboxing docs](https://docs.claude.com/en/docs/claude-code/sandboxing)
- Permissions stop the agent from trying; the sandbox stops the attempt from succeeding if injection persuades it to try anyway. Hooks run shell commands with user permissions, so malicious hooks or repo config are a risk; review via `/hooks`. — [DataCamp Claude Code security](https://www.datacamp.com/tutorial/claude-code-security); [helix.ml](https://helix.ml/blog/running-claude-code-safely) (third-party)
- **Slopsquatting:** a study by Virginia Tech, U. Oklahoma and UT San Antonio had 16 LLMs generate 576k Python/JS samples. About 20% of recommended packages didn't exist (~205k unique names). Open-source models hallucinated far more (~21.7%) than commercial ones (~5.2%). 43% of hallucinated names recurred across all 10 reruns, which makes squatting practical. Mitigations: vet dependencies, use allowlists or internal mirrors. — [Infosecurity Magazine](https://www.infosecurity-magazine.com/news/ai-hallucinations-slopsquatting/); [Kaspersky](https://www.kaspersky.com/blog/ai-slopsquatting-supply-chain-risk/53327/) (secondary; original paper not fetched)

### Inferences
- In multi-agent setups the trifecta can be split across agents, for example a "reader" agent ingesting issues and an "executor" agent holding push tokens. That makes inter-agent messages an injection channel (OWASP's insecure inter-agent communication category). Mitigation is to give untrusted-content readers no write or network capability and to pass structured data, not free text, between agents where feasible.
- Practical controls for an orchestrator:
  - A per-agent sandbox or container with an egress allowlist.
  - Secrets never in agent context; deny `.env` reads.
  - Scoped, short-lived tokens per task, ideally per agent role.
  - Single-branch push rights.
  - Dependency additions require allowlist or human approval.
  - Treat issue, PR and web bodies as untrusted.
  - Block "public write after private read" without human approval.

### Gaps
- I found no authoritative data on credential leakage across agents in shared multi-agent environments, or on the incidence of real-world attacks beyond research PoCs.
- I could not verify the exact ASI01–ASI10 titles in order, since the OWASP page links to a PDF only.

## Observability and evaluation: tracing, cost, eval suites, regression testing

### Takeaway
OpenTelemetry GenAI semantic conventions (agent/tool spans such as `invoke_agent`, `execute_tool`, with `gen_ai.*` attributes) are the emerging vendor-neutral trace format. They now live in a separate repo and appear to still be in Development status. LangSmith, Langfuse, Arize and others ingest these traces and offer dataset-based offline experiments with CI gates, plus online scoring. Anthropic's experience suggests starting with small eval sets (~20 cases), LLM-judge rubrics, end-state evaluation, human review and gradual "rainbow" deployments. Token cost is a first-order concern: multi-agent runs use about 15× chat tokens.

### Cited Findings
- **OTel GenAI conventions** moved out of the main semantic-conventions repo into `open-telemetry/semantic-conventions-genai`, and the old page "is no longer maintained in this repository". — [OpenTelemetry docs (redirect notice)](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- Agent operations defined as spans include `create_agent`, `invoke_agent`, `invoke_workflow`, `plan`, `execute_tool`. A supervisor span contains sub-agent `invoke_agent` spans, each with model and tool children. Attributes include `gen_ai.agent.name`, `gen_ai.tool.name`. — [TrueFoundry blog](https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions); [Decagon glossary](https://decagon.ai/glossary/what-are-opentelemetry-genai-semantic-conventions) (secondary)
- **Stability conflict:** one June 2026 article claims a stable spec. A May 2026 GreptimeDB post and an Aug 2026 TrueFoundry post say the conventions (including MCP) remain "Development". The later sources are more credible. — [Greptime, 2026-05-09](https://www.greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions); [TrueFoundry](https://www.truefoundry.com/blog/opentelemetry-genai-semantic-conventions); [businesstechnavigator (stable claim)](https://businesstechnavigator.com/news/opentelemetry-genai-semantic-conventions-agents-2026)
- **Langfuse** evaluates agents on trajectory, tool use, task completion and multi-turn quality. It recommends tool-call assertions in offline dataset experiments as regression tests and boolean tool-call scores on production traffic for alerting. It is open source and framework-neutral, with OTel ingestion and an agent graph view (reported July 2026). It reportedly added Experiments CI/CD gates in 2026. — [Langfuse: AI agent evaluation](https://langfuse.com/resources/engineering/ai-agent-evaluation.md); [Langfuse engineering resources](https://langfuse.com/resources/engineering)
- **LangSmith** has 30+ evaluator templates including trajectory evals, and offline evals on curated datasets "giving you regression testing before every deploy". This is the vendor's own claim versus Langfuse. — [LangChain: LangSmith vs Langfuse](https://www.langchain.com/resources/langsmith-vs-langfuse) (vendor-authored)
- **Anthropic multi-agent eval lessons (2025-06-13):**
  - Token usage alone explained 80% of BrowseComp performance variance; with tool calls and model choice, 95%.
  - Multi-agent systems use ~15× chat tokens (single agents ~4×).
  - Start with ~20 realistic queries.
  - Judge end state, not path, with intermediate checkpoints for complex flows.
  - Use full production tracing while monitoring decision patterns rather than conversation contents (privacy).
  - Use rainbow deployments for long-running stateful agents.
  - Observed failure modes: spawning 50 subagents for simple queries, endless searching, subagents distracting each other, compounding errors.
  — [Anthropic engineering](https://www.anthropic.com/engineering/multi-agent-research-system)
- Claude Code docs note subagent cost is "mostly tokens… they count against the same usage as your main conversation", and a fresh subagent "can end up slower". — [Claude Code best practices](https://code.claude.com/docs/en/best-practices)

### Inferences
- For an orchestrator, emit OTel GenAI spans with one trace per workflow run, an `invoke_agent` span per stage or subagent, `execute_tool` spans, and token-usage attributes. This gives vendor portability (Langfuse, LangSmith, Arize Phoenix, Datadog, etc.) and per-stage cost attribution. Pin to a convention version, since the spec is still Development and attribute names may change.
- Regression testing of prompts and agents means a small curated dataset of tasks with deterministic end-state checks (tests pass, files changed, tool-call assertions) plus a rubric judge. It runs in CI on every prompt, skill or model change.

### Gaps
- I did not verify Arize Phoenix specifics or official OTel attribute lists (for example token usage attribute names) from the new repo.
- I found no public benchmark of eval-suite design specifically for multi-agent *coding* workflows beyond SWE-bench-style task suites and the reward-hacking benchmarks above.
