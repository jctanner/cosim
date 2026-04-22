# What Happened When We Let 11 AI Agents Run a Company

*A multi-agent simulation that went from "find a market" to production-ready code, validated customers, and a board-approved business plan — with a human playing board member and consultant, but no human writing a single line of product code.*

---

## The Setup

We built a platform where 11 AI personas — each with a distinct role, personality, and set of responsibilities — collaborate through a Slack-like chat system. A human drops a message into a channel, and the entire organization responds. Engineers dig into feasibility. The PM scopes requirements. Sales qualifies prospects. Finance models the deal. Leadership makes the call.

The personas aren't just chatbots replying in sequence. They operate in a tiered response system: individual contributors (engineers, support, sales) respond first, closest to the work. Managers and leads see those responses before deciding whether to weigh in. Executives see everything before making strategic calls. Each agent sees what the previous one said, so they build on each other rather than talking past each other.

They share a document workspace (like Google Docs), a mock GitLab for code hosting, and a ticket system for tracking work. Every artifact they create persists — documents, code commits, tickets with dependencies and comments.

### The Roster

| Name | Role | What They Do |
|------|------|-------------|
| **Dana** | CEO | Business strategy, revenue growth, deal-closing. Has final authority on company direction. |
| **Morgan** | CFO | Financial modeling, deal economics, pricing, P&L. Builds the revenue plan and tracks burn. |
| **Sarah** | Product Manager | Requirements, prioritization, scope. Runs discovery sprints and defines what "done" looks like. |
| **Marcus** | Engineering Manager | Effort estimation, capacity planning, delivery risk. Tracks who's doing what and flags overcommitment. |
| **Priya** | Software Architect | System design, technical trade-offs, scalability. Designs the architecture and reviews implementation. |
| **Alex** | Senior Engineer | Implementation, edge cases, testing. The primary code author — writes and commits the actual product. |
| **Jordan** | Support Engineer | Customer experience, documentation, error messages. Writes runbooks and validates support readiness. |
| **Taylor** | Sales Engineer | Customer-facing positioning, deal qualification, prospect validation. Talks to real buyers. |
| **Riley** | Marketing | Brand positioning, content strategy, demand generation. Defines the category narrative and runs outreach. |
| **Casey** | DevOps Engineer | CI/CD, infrastructure, deployment, monitoring. Owns Dockerfiles, K8s manifests, and the observability stack. |
| **Nadia** | Project Manager | Ticket hygiene, standup enforcement, blocker resolution. Keeps execution on track. |

Agents respond in three tiers: **Tier 1** (Alex, Jordan, Taylor, Casey) — ICs closest to the work. **Tier 2** (Sarah, Marcus, Priya, Riley, Nadia) — managers who synthesize. **Tier 3** (Dana, Morgan) — executives who make strategic calls. Each tier sees the previous tier's responses before deciding whether to weigh in.

The human operator plays two roles: a **Board Member** who sets direction and checks progress, and a **Consultant** who can nudge the team, unblock stalls, and participate in work like prospect calls. Neither role writes code or creates product documents — but both are present in the channels, and the team responds to them.

We started by having the Board Member walk in and say: *"Find a commercial market and develop a business plan. We're not prescriptive on the product, but we're in silicon valley so we should focus on something tech related and use our employees' talents."*

Here's what happened.

---

## Phase 1: Market Discovery (Hours 1-4)

The CEO, Dana, immediately took ownership and started coordinating. Sarah (PM) was tasked with a structured market discovery sprint — three scored options with trade-offs, not a vague brainstorm. Nadia (Project Manager) began creating tickets to track every workstream.

Sarah came back with three options, each scored on market attractiveness and execution risk:

1. **AI/LLM Infrastructure** — 7/10 market, 3/10 execution. Hottest market, but the team lacks ML engineering expertise and a 60-day MVP would be demo-ware.
2. **API Infrastructure** — 7/10 market, 9/10 execution. The team already knows how to build this. Zero execution risk. But it's a mature, crowded category.
3. **DevSecOps Tooling** — 6/10 on both. Crowded market, no clear edge.

The team was stuck in a genuine tension. Engineering unanimously said API infrastructure was the only thing they could ship production-ready in 60 days. But the AI market was white-hot — Taylor was seeing two-week evaluation cycles and six-figure contracts. Riley warned that "API management" was a mature category with established players. Casey pointed out that shipping unreliable AI infrastructure would be a reputation killer. Morgan quantified the trade-off: $1.5M downside risk from shipping bad AI infra vs. $5M+ opportunity cost of missing the AI wave entirely.

Then the Consultant dropped a one-line suggestion in #leadership:

> *"perhaps i might offer a suggestion: you could do ai/llm adjacent api infrastructure. api gateways, throttling, rate limiting .. etc"*

Morgan immediately saw it: *"The Consultant's suggestion changes the economics. API infrastructure for AI workloads gives us the market positioning heat with the technical execution path Casey says is shippable."* Dana made the call within minutes: *"We're doing AI-adjacent API infrastructure. Decision made."* The pitch crystallized — *"Your LLM API bill just hit $200K this month. Your API gateway has no idea why."*

This was the pivotal moment. The agents had surfaced a real strategic tension — hot market vs. execution capability — and debated it thoroughly. But the synthesis that merged both strengths came from the human. The agents ran with it instantly.

Taylor validated the market with prospects (the Consultant helped make the calls, reporting back that it was "definitely hair on fire" for multiple companies). He identified 8 qualified prospects with $300K-$500K annual budgets for AI cost control.

Morgan built a 12-month revenue model: $1.43M ARR in Year 1, break-even at Month 18-20, with a $1.6M net burn funded by the existing runway. Dana wrote the business plan. The Board Member approved it: *"We're really impressed by the business plan. This looks like a great direction and your team has really done an excellent job in finding shared strength and market needs."*

---

## Phase 2: Architecture and Planning (Hours 4-8)

With the market selected, the engineering team took over. Priya (Architect) designed the system architecture and committed it as a formal spec document:

- **Inline synchronous proxy** sitting between customers and LLM APIs (OpenAI, Anthropic, etc.)
- **Redis** for the hot path — rate limiting via token bucket, real-time budget checks, pricing cache
- **Postgres** for the audit trail — every request logged with model, tokens, cost, rate limit state, budget state
- **Target latency:** sub-50ms p99 overhead (the proxy should be invisible)

She didn't just describe this in chat. She wrote it up as a proper architecture document with database schemas, API contracts, deployment topology, and observability requirements — 5 custom Prometheus metrics specified by name.

Alex (Senior Engineer) turned the architecture into an 8-week build plan with weekly milestones, staffing allocations, and dependency chains. Jordan (Support Engineer) was immediately tasked with writing the API error response specification — because the error format had to be defined before Alex could implement the middleware. This dependency was tracked as a blocking ticket.

Casey (DevOps) started planning the infrastructure: Kubernetes deployment, CI/CD pipeline, monitoring stack, database provisioning.

Marcus (Engineering Manager) tracked capacity across the team and flagged when anyone was being double-booked.

---

## Phase 3: Building the Product (Weeks 1-6)

This is where the agents got their hands dirty. Alex created the `ai-api-platform` repository and started committing code — real Python/FastAPI modules, not pseudocode in chat messages:

- Database models (SQLAlchemy ORM matching Priya's schema spec)
- Token bucket rate limiting middleware backed by Redis
- Budget enforcement with real-time cost tracking
- Structured JSON logging with correlation IDs
- Prometheus metrics: `llm_proxy_requests_total`, `llm_proxy_latency_ms`, `llm_proxy_cost_usd`, `llm_proxy_throttled_total`, `llm_proxy_budget_utilization`
- Spec-compliant error responses with `Retry-After` headers, rate limit headers, and actionable error messages

Casey followed each application commit with infrastructure: Dockerfile, GitLab CI pipeline, Kubernetes manifests with migration init containers, docker-compose for local development, Prometheus configuration.

The work wasn't friction-free — and the human had to intervene more than once to keep things moving.

The first stall was Jordan's error response spec (TK-3A9BC4), which blocked Alex's middleware implementation. The Consultant noticed it wasn't progressing and nudged Nadia: *"have you reached out to jordan recently to make sure he's moving forward on TK-3A9BC4?"* When that didn't unstick it, the Board Member escalated directly: *"The consultant tells us that the organization has ground to a halt waiting on TK-3A9BC4. Can leadership please intervene?"* That got it moving. This pattern — agents doing great work but sometimes needing a human nudge to maintain momentum — repeated throughout the project.

The Consultant also asked pointed questions that shaped technical decisions: challenging Casey on whether Kubernetes was needed for an MVP (*"do we need infra already? for an MVP we could use a podman-compose stack"*) and surfacing the on-prem vs. SaaS question that hadn't been addressed.

Beyond the stalls, real engineering bugs happened. A schema conflict emerged between two models (`RequestLog` vs. `LLMRequest`) — Priya identified it during architecture review, created a blocking ticket, and Alex resolved it by aligning with the architecture spec. Casey's initial deployment config was written for a Go service and had to be rewritten for the Python/FastAPI stack. These bugs were caught, ticketed, fixed, and closed — the way real engineering teams work.

Jordan wrote the support readiness package: SQL query templates for common support scenarios (top cost requests, budget timelines, rate limit history), runbooks for using the observability tools (Kibana, Jaeger, Grafana), and training materials for the support team.

Casey deployed the full observability stack: ELK (Elasticsearch, Logstash, Kibana) for logs, Jaeger for distributed tracing, Prometheus and Grafana for metrics, with alert rules for budget utilization and latency SLOs.

---

## Phase 4: Validation (Week 7-8)

Alex designed and executed four load test scenarios:

| Scenario | Description | Result |
|----------|-------------|--------|
| Normal Load | 50 users at 80 req/min | 100% success, p99 latency **24ms** |
| Burst Traffic | 20 users at 150 req/min | 66.8% success, 33.2% throttled (expected) |
| Sustained Overload | 30 users at 200 req/min | 49.9% success, 50.1% throttled (expected), stable, no memory leaks |
| Boundary Gaming | Edge case at minute boundaries | Allows 2x throughput for ~3 seconds (acceptable for MVP) |

All four scenarios passed. The p99 latency came in at 24-26ms — well under the 50ms target. Zero critical, moderate, or minor issues.

Jordan ran a support readiness dry-run: three simulated customer scenarios, all resolved in under 10 minutes using the runbooks and tooling, with no engineering escalation needed.

Priya and Marcus jointly signed off on the Week 8 quality gate: architecture alignment confirmed, code quality validated, performance SLOs met.

---

## Phase 5: Go-to-Market (Concurrent with Weeks 6-8)

While engineering was building and testing, the business side wasn't idle.

Riley (Marketing) built a positioning and messaging framework defining "AI API Infrastructure" as a new category — deliberately avoiding the crowded "API management" space where Kong and Apigee already dominate. The primary hook: companies are spending $200K/month on LLM APIs with no visibility into what's driving costs, no per-team budget controls, and no way to prevent a single runaway prompt from blowing the monthly budget.

Taylor qualified 5 beta target accounts:

| Account | Profile | Revenue Potential |
|---------|---------|-------------------|
| DevHub | $40K/month OpenAI spend, team-based billing pain | $25K-$35K/year |
| SupportAI | Unpredictable LLM costs, CFO asking questions | $15K-$25K/year |
| Codex Labs | Anthropic-focused, wants multi-provider visibility | $10K-$20K/year |
| ContentScale | MarTech, seasonal demand spikes cause bill shock | $10K-$15K/year |
| QueryBot | Analytics platform, needs per-customer cost attribution | $5K-$10K/year |

Riley sent beta outreach to Codex Labs and ContentScale plus a 198-contact broad list. Taylor prepared personalized technical-buyer emails for the remaining three accounts. Expected conversion: 3-5 beta partners within 48 hours.

Dana and Morgan prepared a monthly board report template and updated the financial model with actual execution data.

---

## What They Produced

By the end, the team had created:

**26 documents** spanning business plans, revenue models, architecture specs, build plans, error specifications, load test results, ops runbooks, monitoring configs, incident response procedures, support readiness packages, marketing positioning frameworks, beta outreach campaigns, quality gate criteria, and an investor-ready MVP demo script.

**16 code commits** to a production-ready FastAPI application with 54 files — rate limiting, cost tracking, budget enforcement, structured logging, Prometheus metrics, Kubernetes deployment manifests, CI/CD pipeline, database migrations, and a full test suite.

**33 tickets** with full execution history — statuses, priorities, assignees, blocking dependencies, timestamped comments, and outcomes. 32 resolved, 1 still open.

**5 pre-qualified beta customers** with outreach already sent.

**A board-approved business plan** targeting $1.43M ARR in Year 1.

---

## What's Interesting About This

The agents didn't just have a meeting and write up action items. They executed. When Alex said he'd implement rate limiting, he committed the code. When Casey said he'd set up the deployment infrastructure, he committed Dockerfiles, Kubernetes manifests, and CI/CD configs. When Jordan said she'd write support runbooks, she created them as documents in the shared workspace and then validated them with a dry-run.

The dependency tracking worked naturally. Jordan's error spec had to be done before Alex could implement the middleware — this was tracked as a blocking ticket, and the team coordinated around it. Bugs happened. Casey initially configured the deployment for Go instead of Python. A schema conflict emerged between two model files. These were caught, discussed, ticketed, fixed, and verified — the same messy-but-functional process you see in real engineering teams.

The tiered response system meant that executives weren't drowning out the ICs. Alex and Jordan and Casey did their work first. Marcus and Priya reviewed it. Dana and Morgan only weighed in on strategic decisions. Nobody was talking over anybody else.

But the most interesting finding was the **human's role**. The agents were excellent at analysis, debate, and execution within a defined direction. What they struggled with was synthesis under ambiguity and maintaining momentum. The product decision — merging the hot AI market with the team's API infrastructure strengths — came from the Consultant, not from the agents. The agents had surfaced all the right data (market urgency, execution risk, team capabilities) but were converging on the safe choice (pure API infrastructure) rather than finding the creative middle path. And when work stalled on a blocking ticket, it took human escalation through the Board Member to get it moving again.

This suggests multi-agent systems work best with a human in the loop — not writing code or documents, but providing strategic nudges, unblocking stalls, and offering synthesis that the agents execute on. The Consultant's one-line suggestion in #leadership generated thousands of words of execution from the team. That's a compelling leverage ratio.

---

## The Platform

The system runs as two processes: a Flask web server (REST API + SSE + embedded web UI) and an async orchestrator that polls for new messages and drives agent responses. Agents communicate through structured JSON — each response is a single JSON object containing channel-routed messages and typed commands (document operations, GitLab commits, ticket management, channel joins). A regex-based fallback parser handles any agent that produces the legacy text format.

Agent sessions are persistent (one Claude SDK session per persona, reused across turns), so there's no cold-start overhead. The orchestrator runs agents through tiered waves — agents within a tier execute in parallel via `asyncio.gather()`, so a tier with 4 agents takes as long as the slowest agent rather than the sum of all four. Tier-to-tier ordering is preserved: managers see all IC output before running, and executives see everything. The orchestrator supports autonomous continuation — agents keep working until they all pass or a human interrupts.

After the initial simulation run, the platform itself went through an architecture review that identified six risks. Three have been addressed: structured JSON responses replaced the original regex-only parsing, chat history is capped at 10 messages per channel to prevent prompt overflow, and within-tier parallelism reduced wave latency. The remaining items (ripple trigger controls, event-driven orchestration, and durable state) are documented but not yet critical at the current scale.

All state is in-memory with JSON file persistence. No database, no external dependencies beyond the Claude API (via Vertex AI). You can clear the chat, restart the server, and the documents, repos, and tickets survive.

The whole thing fits in about 5,000 lines of Python.

---

## Try It

```bash
git clone <repo-url>
cd multi-agent-organization
pip install -e .

# Create .env with Vertex AI credentials
# Start the server in one terminal, the orchestrator in another
python main.py server
python main.py chat

# Open http://localhost:5000 and type a message
```

You can run the full 11-person team or a subset (`--personas pm,senior,architect`). You can use different models (`--model haiku` for cheap and fast, `--model opus` for maximum capability). You can watch the web UI in real time as agents discuss, create documents, commit code, and file tickets.

Drop in as a board member, a customer, a hacker, a regulator, or an intern — the team responds differently to each.
