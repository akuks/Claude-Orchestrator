# Multi-Agent vs Single-Agent LLM Workflows for Software Development: Evidence and Failure Modes

(Research notes compiled 2026-11-20. Primary sources were preferred. Secondary or aggregator claims are flagged.)

## 1. What do benchmarks show about multi-agent vs single-agent scaffolds (2025–2026), and which top systems use multiple agents?

### Takeaway
On mainstream SWE benchmarks the best controlled evidence (Google/MIT, 260 configurations, compute-matched) finds **every multi-agent variant slightly worse than a single agent on SWE-bench Verified (-2.1% to -14.9%)** and mixed results on Terminal-Bench. Top leaderboard entries in 2026 are frontier models run in simple single-agent loops (e.g. mini-SWE-agent). Where "multi-agent" helps on SWE-bench, it is mostly **parallel sampling plus a selector/verifier** (ensembles), not role-playing agent teams.

### Cited Findings
**Controlled study: "Towards a Science of Scaling Agent Systems" (Kim et al., Google Research / MIT; arXiv 2512.08296, v1 Dec 9 2025, v3 Apr 8 2026)**
- 260 configurations, 5 architectures (single-agent system (SAS) plus Independent, Centralized, Decentralized and Hybrid multi-agent systems (MAS)), 3 LLM families, with tools, prompts and compute standardized (about 4,800 reasoning tokens per trial) — [arXiv abs](https://arxiv.org/abs/2512.08296); [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)
- The 6 benchmarks include **SWE-bench Verified** and **Terminal-Bench**, plus BrowseComp-Plus, Finance-Agent, PlanCraft and WorkBench — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)
- **SWE-bench Verified:** SAS mean 0.522. Every MAS variant is worse: Hybrid -2.1%, Centralized -3.1%, Decentralized -5.4%, Independent -14.9%. The authors attribute this to high single-agent baselines leaving no headroom — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)
- **Terminal-Bench:** SAS mean 0.344. Independent +1.7%, Centralized -19.2%. The authors link this to only 2 tools being available, so orchestration has little to manage — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)
- Range across all tasks: **+80.8%** (Centralized, decomposable Finance-Agent) to **-70.0%** (Independent, sequential PlanCraft). The fitted model picks the best architecture for 87% of held-out configurations — [arXiv abs](https://arxiv.org/abs/2512.08296)
- **Capability saturation:** when single-agent accuracy exceeds about 45%, adding agents has negative returns (interaction β = -0.236, p = 0.004) — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3). Note: v1 reported 180 configurations and different R² values (0.513/0.524). v3 reports 260 configurations and cross-validated R² 0.373–0.413, so cite v3 — [Google Research blog, Jan 28 2026](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/); [arXiv abs](https://arxiv.org/abs/2512.08296)

**CooperBench (Khatua, Zhu et al.; arXiv 2601.13295, v2 Jan 26 2026; reported accepted at COLM 2026, also listed at ICLR 2026)**
- 652 task pairs from 12 open-source repositories (Python, TypeScript, Go, Rust). Two agents each implement a feature in the same codebase — [arXiv HTML](https://arxiv.org/html/2601.13295)
- **"Curse of coordination":** GPT-5 and Claude Sonnet 4.5 agents reach about 25% success when cooperating, up to about 50% below the "Solo" baseline, where one agent does both features. The abstract states an average drop of at least 30% — [arXiv HTML](https://arxiv.org/html/2601.13295); [ICLR listing](https://iclr.cc/virtual/2026/10018462)
- Retention (Coop/Solo): GPT-5 0.64, Claude Sonnet 4.5 0.60, MiniMax M2 0.46, pooled 0.59 — [arXiv HTML](https://arxiv.org/html/2601.13295)
- **More agents is worse:** success falls from 68.6% (2 agents) to 46.5% (3 agents) to 30.0% (4 agents) on a 46-task subset — [arXiv HTML](https://arxiv.org/html/2601.13295)
- A secondary summary says a later version reports human pairs solving 9/10 tasks versus 3/10 for agents. I did not confirm this in the paper text — [search summary of arXiv 2601.13295](https://www.arxiv.org/abs/2601.13295)

**Framework comparison (Yin et al., arXiv 2511.00872, Nov 2025)**
- Seven general agent frameworks (e.g. AgentOrchestra, OpenHands, GPTswarm) were evaluated on software development, vulnerability detection and program repair. The multi-agent AgentOrchestra had the longest trajectories and the most correction attempts, which the authors attribute to coordination overhead. Software development was the most expensive task type — [arXiv](https://arxiv.org/abs/2511.00872)
- A secondary source claims the paper found single-agent systems more effective on code executability and consistency, and MAS better on completeness. I did not verify this in the paper — [search result summary](https://arxiv.org/pdf/2511.00872)

**Leaderboards and top systems**
- Vals.ai's SWE-bench Verified leaderboard runs all models in a standardized **mini-SWE-agent** scaffold, a minimal single-agent bash loop. As of Sep 1 2026 it reports Claude Opus 5 at 97.0%, DeepSeek V4 Pro at 96.4% and GPT-5.6 Sol at 96.2% — [Vals.ai](https://www.vals.ai/benchmarks/swebench). Other aggregators disagree on who is first (e.g. Claude Mythos Preview at 93.9%) — [Codesota](https://www.codesota.com/benchmark/swe-bench-verified-agentic); [Steel.dev](https://leaderboard.steel.dev/leaderboards/swe-bench-verified/). These numbers come from aggregators, so treat them as provisional.
- SWE-bench Verified is near saturation, and OpenAI reportedly stopped evaluating on it. SWE-bench Pro scores are far lower (e.g. Mythos Preview at 45.9%) — [Morph SWE-bench Pro page](https://www.morphllm.com/swe-bench-pro) (aggregator)
- **Ensemble-style "multi-agent" SOTA (2025, now superseded):** TRAE Agent (ByteDance) generated multiple candidate patches, pruned them with regression tests, then selected one via AST-clustering votes and several selector agents voting. It reported 75.2% Pass@1 on SWE-bench Verified (arXiv 2507.23370, Jul 2025), and an earlier version scored 70.6% with Claude 3.7 and an o1 selector — [arXiv](https://arxiv.org/html/2507.23370v1); [Trae blog](https://www.trae.ai/blog/product_update_0528)
- I could not retrieve Terminal-Bench 2.0 leaderboard rows because the table loads dynamically — [tbench.ai](https://www.tbench.ai/leaderboard/terminal-bench/2.0)

### Inferences
- On benchmarks where one agent already scores high (SWE-bench Verified, roughly 52% mean even in the 2025–26 study models, about 95%+ for 2026 frontier models), role-based MAS has no headroom and costs more. The 45% saturation finding predicts this.
- The multi-agent pattern that has historically paid off on SWE-bench is "sample N independent attempts, then verify and select with tests and judges." This is test-time scaling and does not require agents to coordinate.
- Collaborative coding on a shared codebase (CooperBench) is currently a clear net negative compared with one agent doing all the work.

### Gaps
- I did not retrieve current Terminal-Bench 2.0, Multi-SWE-bench, SWE-Lancer or DevBench results that compare multi-agent and single-agent setups. Leaderboard pages rendered without data.
- I found no 2026 controlled study on SWE-bench Pro comparing MAS and SAS.
- HumanEval-era MAS results (MetaGPT, ChatDev, AgentCoder, 2023–24) were not re-checked. They are superseded and saturated benchmarks.

## 2. What do studies find about failure modes?

### Takeaway
The failures are mostly design and specification failures, plus inter-agent misalignment, rather than raw model capability. MAST finds 41–86.7% failure rates in 7 open MAS frameworks. CooperBench shows that coding-specific failures are dominated by overlapping work, divergent architecture and agents mis-modeling their partner's state. Errors compound much more when no central verifier exists (up to 17.2x).

### Cited Findings
**MAST, "Why Do Multi-Agent LLM Systems Fail?" (Cemri, Pan, … Zaharia, Gonzalez, Stoica; UC Berkeley; arXiv 2503.13657, Mar 2025, v3 Oct 2025)**
- Covers 1,600+ annotated traces from 7 frameworks: MetaGPT, ChatDev, HyperAgent, AppWorld, AG2, Magentic-One, OpenManus. The taxonomy has 14 failure modes in 3 categories, and inter-annotator κ = 0.88 — [arXiv abs](https://arxiv.org/abs/2503.13657); [arXiv HTML](https://arxiv.org/html/2503.13657)
- Failure rates of 41% to 86.7% across the 7 state-of-the-art open-source MAS — [arXiv HTML](https://arxiv.org/html/2503.13657)
- Category shares (summed from per-mode percentages): System design issues about 44.2%, inter-agent misalignment about 32.4%, task verification about 23.5% — [arXiv HTML](https://arxiv.org/html/2503.13657)
- Top modes: step repetition 15.7%, reasoning-action mismatch 13.2%, unaware of termination conditions 12.4%, disobeying task specification 11.8%, incorrect verification 9.1%, premature termination 6.2% — [arXiv HTML](https://arxiv.org/html/2503.13657)
- Coding examples: a ChatDev chess program compiled but had rule-violating runtime bugs because verification was only superficial. On ProgramDev, MetaGPT had 60–68% fewer FC1 (system design) and FC2 (inter-agent misalignment) failures than ChatDev but 1.56x more FC3 (task verification) failures — [arXiv HTML](https://arxiv.org/html/2503.13657)

**CooperBench failure analysis (coding-specific, Jan 2026)**
- Symptoms in failed cooperative trajectories: work overlap 33.2%, divergent architecture 29.7%, repetition 14.7%, unresponsiveness 8.7%, unverifiable claims 4.3%, broken commitments 3.7% — [arXiv HTML](https://arxiv.org/html/2601.13295)
- Root causes (50 traces reviewed manually): expectation failures (not modeling the partner's state) 42%, commitment failures 32%, communication breakdowns 26% — [arXiv HTML](https://arxiv.org/html/2601.13295)
- Messaging channels are "jammed with vague, ill-timed, and inaccurate messages." Communication eats up to about 20% of steps. It reduces naive merge conflicts but not task failure (17.64% without communication vs 17.14% with, after merge resolution) — [arXiv HTML](https://arxiv.org/html/2601.13295)

**Error compounding (Kim et al., 2512.08296 v3)**
- Trace-level error amplification relative to SAS (1.0x): Independent 17.2x, Decentralized 7.8x, Hybrid 5.1x, Centralized 4.4x. After controlling for other factors, amplification was not an independently significant predictor — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)

**Practitioner reports**
- Cognition (Jun 12 2025): the subagents in a "Flappy Bird" decomposition build inconsistent parts, because "decision-making ends up being too dispersed and context isn't able to be shared thoroughly enough between the agents" — [Cognition, Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)
- Cognition (Apr 22 2026): parallel writers still conflict on implicit decisions (style, patterns, edge cases). A weak primary model (SWE-1.5) could not tell when to escalate to a "smart friend" model — [Cognition, Multi-Agents: What's Actually Working](https://cognition.com/blog/multi-agents-working)
- Anthropic's C compiler (Feb 2026, 16 parallel agents): on the Linux kernel, all agents hit the same bug in one monolithic task and **overwrote each other's fixes**. New features often broke existing ones until CI enforcement was added — [Anthropic Engineering](https://www.anthropic.com/engineering/building-c-compiler)
- Anthropic's research system (Jun 2025, not coding): it spawned 50 subagents for simple queries, subagents duplicated each other's work or left gaps, and they distracted each other with excessive updates — [Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- In coding the dominant MAS failure is **semantic inconsistency between agents' outputs** (divergent architecture, duplicated work, overwritten fixes), not textual merge conflicts. Better git tooling or chat channels do not fix it.
- Weak verification (FC3) shows up across all frameworks. A "verifier" agent that checks only that code compiles produces false confidence.

### Gaps
- I found no large-scale study measuring role drift over long horizons specifically for coding agents. MAST's "disobey role specification" mode exists, but I did not retrieve its percentage.
- Data on production failure rates from commercial MAS coding products is not public.

## 3. What are the cost and latency trade-offs, and for which task types do gains justify the cost?

### Takeaway
Multi-agent setups typically cost **1.4–15x more tokens** and **2.6–5.8x more latency** in controlled enterprise tests, with small or non-significant gains. They pay off for breadth-first, decomposable, read-heavy work (research +90%, finance +81%). They also pay off for very large, test-oracle-rich builds where wall-clock parallelism matters. They do not pay off for tightly coupled sequential coding.

### Cited Findings
- Anthropic (Jun 2025): agents use about 4x the tokens of chat, and **multi-agent systems about 15x**. The Opus 4 lead with Sonnet 4 subagents beat single-agent Opus 4 by **90.2%** on an internal *research* eval. Token usage explains 80% of BrowseComp variance. Parallelism cut research time by up to 90%. Anthropic states that "most coding tasks involve fewer truly parallelizable tasks than research" — [Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system). Note that these are 2025 vendor-measured numbers on an internal research eval with about 20 initial queries, not a coding eval.
- Kim et al. (v3, 2026): coordination overhead versus SAS was Independent 58%, Decentralized 263%, Centralized 285%, Hybrid 515%. Mean turns were SAS 7.2 versus Hybrid 44.3. **Success per 1K tokens was SAS 67.7 versus MAS 13.6–42.4** — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)
- Nandi et al. (arXiv 2611.12953, Nov 7 2026; enterprise SOP workflows, not coding): every coordinated arm used **1.4–4.7x tokens and 2.6–5.8x latency** versus a single agent. No architecture was statistically separable in aggregate. A first plausible MAS lost to the single agent. Only a targeted MAS redesign significantly beat the baseline (75.6% vs 67.4%), and good skills on a single agent reached 73.1%. The authors conclude "implementation quality matters more than the single- vs multi-agent choice" — [arXiv](https://arxiv.org/abs/2611.12953). A secondary source reports 117.3K vs 24.9K tokens per task and 3.1x latency for one arm — [FourWeekMBA](https://fourweekmba.com/ai-amazon-multi-agent-ai-uses-4-7x-tokens-for-an-8-point-gain/) (unverified)
- Tran & Kiela (arXiv 2604.02460, Apr 2026; multi-hop reasoning, not coding): at equal thinking-token budgets, single agents matched or beat MAS. MAS becomes competitive only when a single agent's context use degrades — [arXiv](https://arxiv.org/abs/2604.02460)
- Salim et al., "Tokenomics" (arXiv 2601.14470): in an LLM multi-agent software development pipeline, one stage accounted for 59.4% of tokens on average — [arXiv PDF](https://arxiv.org/pdf/2601.14470) (detail not verified beyond the search summary)
- Large-build case: 16 parallel Claude agents over about 2 weeks and about 2,000 sessions produced a 100K-line Rust C compiler that builds Linux 6.9 and passes 99% of most compiler test suites. It cost just under **$20,000** (about 2B input and 140M output tokens). Parallelism worked because tests and a GCC oracle let agents work on separate failing files — [Anthropic Engineering](https://www.anthropic.com/engineering/building-c-compiler)
- A widely cited "4–220x more tokens" figure (attributed to a UIUC study) could **not** be traced to a primary source — [search summary](https://openclawwiki.org/blog/multi-agent-vs-single-agent-what-260-configurations-show) (unverified, do not cite as fact)

### Inferences
- Rule of thumb from the evidence: use MAS when (a) the subtasks are genuinely independent or read-only, (b) a single agent is below about 45% on the task, or its context is overflowing, and (c) there is a cheap, near-perfect oracle (tests or a reference implementation). Otherwise a single agent is the efficient default.
- Wall-clock speedups are real for parallelizable work (up to 90% in research), but the token cost scales roughly with agent count.

### Gaps
- I found no controlled measurement of wall-clock speedup for MAS versus SAS on SWE benchmarks.
- I found no published per-task dollar comparison for MAS and SAS on SWE-bench Pro.

## 4. What do critics argue, and how has the debate evolved through 2025–2026?

### Takeaway
The debate has converged. **"One writer, many readers or reviewers"** is broadly accepted. Parallel writers and free-form swarms remain unproven. Cognition moved from "don't build multi-agents" (Jun 2025) to endorsing review, consultation and manager-child delegation around a single writer (Apr 2026). Controlled studies (Kim et al.; Nandi et al.; CooperBench) largely back the skeptics for coupled coding.

### Cited Findings
- Cognition (Walden Yan, Jun 12 2025): context sharing and dispersed decisions make parallel subagents fragile. The post recommends single-threaded linear agents with full trace sharing and context compression — [Cognition](https://cognition.ai/blog/dont-build-multi-agents)
- Anthropic (Jun 2025) argued the opposite for breadth-first research (+90.2%) but conceded coding is a weaker fit today — [Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system)
- Cognition (Apr 22 2026) says multi-agent systems now work when "writes stay single-threaded." Patterns that work: a clean-context generator-verifier review loop (Devin Review catches about 2 bugs per Devin PR, about 58% of them severe), routing to frontier models as a "smart friend," map-reduce-and-manage delegation, and read-only search subagents. Patterns that don't: unstructured swarms, parallel writers, and a weak primary with a smart friend. The post notes that showcase demos (a 100K-LOC C compiler, a 200K-LOC browser) have simple, verifiable success criteria, unlike most real software — [Cognition](https://cognition.com/blog/multi-agents-working)
- Anthropic's C compiler (Feb 2026) shows that parallel writers *can* work given a near-perfect oracle, task locks and git, with no orchestrator — [Anthropic Engineering](https://www.anthropic.com/engineering/building-c-compiler)
- Academic consensus by late 2026: no universal best architecture, and the single agent is the efficient default — [Nandi et al.](https://arxiv.org/abs/2611.12953); [Kim et al.](https://arxiv.org/abs/2512.08296)

### Inferences
- The question has shifted from "single vs multi" to "who holds write authority, and what is the oracle?" Read-only fan-out and independent review are low-risk. Concurrent writes to coupled code are high-risk.

### Gaps
- I found no direct OpenAI engineering post with measured MAS vs SAS coding numbers. A claim that "GPT-5.6 Ultra coordinates four agents by default" appeared only on HackerNoon and is uncorroborated — [HackerNoon](https://hackernoon.com/most-of-your-agent-tasks-dont-need-a-swarm)

## 5. What evidence is there on reliability practices that reduce failures?

### Takeaway
The interventions with measured effects are **adding a high-level verification step**, **clarifying role authority**, **centralized orchestration (as a validation bottleneck)**, **test-based pruning and selection of candidates**, **fresh-context review**, and **limiting agent count**. Prompt-level coordination rules give only marginal gains.

### Cited Findings
- MAST interventions on ChatDev with ProgramDev: a high-level task-objective verification step gave **+15.6%** task success. Giving the CEO final say, so the CPO could not end the conversation prematurely, gave **+9.4%** — [arXiv HTML](https://arxiv.org/html/2503.13657)
- Centralized orchestration limits error amplification to 4.4x, versus 17.2x for independent agents — [arXiv HTML v3](https://arxiv.org/html/2512.08296v3)
- Tests as oracle: TRAE Agent pruned candidates that failed regression tests before selection (75.2% SWE-bench Verified, 2025) — [arXiv](https://arxiv.org/html/2507.23370v1). The C compiler project found the verifier "must be nearly perfect, otherwise Claude will solve the wrong problem." It also used a GCC oracle and delta debugging to split monolithic tasks, and needed CI to stop regressions — [Anthropic Engineering](https://www.anthropic.com/engineering/building-c-compiler)
- Clean-context reviewer agent: about 2 bugs found per PR, 58% severe. The coder and reviewer deliberately share no context beforehand — [Cognition](https://cognition.com/blog/multi-agents-working); [ZenML summary](https://www.zenml.io/llmops-database/multi-agent-systems-in-production-code-generation-and-review-at-scale)
- Structured handoffs: in CooperBench, successful cooperative traces featured a first-turn plan and specific file and line references. Prompt rules (explicit line-range division, no insertion markers, mandatory final status) gave only marginal gains. The authors propose verifiable commitments such as shared signatures and insertion-point contracts — [arXiv HTML](https://arxiv.org/html/2601.13295)
- Limit agent count: CooperBench success fell from 68.6% to 30.0% going from 2 to 4 agents. Anthropic scales effort explicitly (1 agent for simple tasks, 2–4 for comparisons, 10+ only for complex research) after early runs spawned 50 subagents for simple queries — [arXiv HTML](https://arxiv.org/html/2601.13295); [Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system)
- Better tool descriptions: a rewritten tool description cut later agents' task time by 40% — [Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- The most defensible architecture for app development is a single writer agent with an executable oracle (tests or CI), read-only research subagents, and an independent fresh-context reviewer. Parallel writers fit only when the work divides into separate files or modules, each with its own oracle.

### Gaps
- I found no randomized study isolating the effect of "structured handoff schemas" (e.g. JSON contracts between agents) on coding success.
- The verification-agent gains (MAST +15.6%) come from small benchmarks (ProgramDev, 30 tasks) and older models (GPT-4o era), so they may not transfer to 2026 frontier models.
