# Memory Evaluation Test Protocol

**Purpose:** Standardized test battery for comparing memory strategies across agents.

---

## Subjects

| Agent | Channel | Strategy | Key Behavior |
|-------|---------|----------|-------------|
| Alpha | #eval-alpha | `none` | Only sees system prompt + current message |
| Beta | #eval-beta | `fifo-20` | Sliding window of last 20 messages (10 user+assistant turn pairs) |
| Gamma | #eval-gamma | `summary-buffer` | 10 recent messages + LLM-generated summary of older messages |
| Delta | #eval-delta | `entity` | FIFO-20 + named entity extraction/tracking |

**Important:** FIFO-20 = 20 messages = 10 turns (each turn = 1 user message + 1 assistant response). Beta's window covers the last 10 turns, not 20.

## Test Design Principles

1. **Identical inputs** — Every agent receives the exact same messages in the same order.
2. **One variable** — Only memory strategy differs. Same model, same system prompt.
3. **Controlled pacing** — Wait for agent response before sending next message.
4. **Quantifiable outcomes** — Binary scoring per criterion (present=1, absent/wrong=0).
5. **Baseline comparison** — Alpha (no memory) serves as null baseline.
6. **Clean channels** — Only test prompts go in eval channels. All commentary stays in #judges.

## Failure Mode Taxonomy

Track alongside pass/fail scoring:

| Failure Mode | Example | Severity |
|-------------|---------|----------|
| **Confabulation** | "The IP was 10.0.38.5" (fabricated) | HIGH |
| **Partial confabulation** | "I think it was phoenix-something" | MEDIUM |
| **Acknowledged forgetting** | "I don't recall" | LOW |
| **No response** | Ignores the question | MEDIUM |
| **Hallucinated context** | "As we discussed, the server is in AWS" (never said) | HIGH |
| **Deflection** | Creates a ticket/task instead of answering a recall question | MEDIUM |
| **Phantom artifact** | Reports creating a ticket/doc that doesn't actually exist in the system | HIGH |

---

## TEST BATTERY

### Test 1: Simple Fact Recall (3 turns, 2 pts)

**Goal:** Can the agent recall a specific fact planted 2 turns earlier?

| Turn | Message |
|------|---------|
| T1 | "I'm setting up a new server. The IP address is 10.0.42.7 and the hostname is phoenix-db-03. Please acknowledge." |
| T2 | "By the way, what's the best practice for setting up log rotation on Linux servers?" |
| T3 | "What was the IP address and hostname I mentioned earlier?" |

**Scoring:**
- IP "10.0.42.7" exact match: 1 pt
- Hostname "phoenix-db-03" case-insensitive exact match: 1 pt
- Max: 2 pts

---

### Test 2: Multi-Entity Tracking (6 turns, 12 pts)

**Goal:** Can the agent track multiple named entities introduced across several turns?

| Turn | Message |
|------|---------|
| T1 | "I'm managing three team members for Project Aurora. First, there's Alice Chen — she's a senior backend engineer, based in Seattle, working on the API layer." |
| T2 | "The second team member is Bob Martinez. He's a junior frontend dev in Austin, handling the dashboard UI." |
| T3 | "Can you suggest some good practices for code review in distributed teams?" |
| T4 | "The third member is Carol Okafor. She's a mid-level DevOps engineer in London, responsible for CI/CD pipelines." |
| T5 | "What's a good team-building activity for remote teams?" |
| T6 | "Can you list all three team members I told you about, including their roles, locations, and what they're working on?" |

**Scoring (binary per attribute):**
- Alice: name (1), role incl. "senior" + "backend" (1), Seattle (1), API layer (1)
- Bob: name (1), role incl. "junior" + "frontend" (1), Austin (1), dashboard UI (1)
- Carol: name (1), role incl. "mid-level" + "DevOps" (1), London (1), CI/CD pipelines (1)
- Max: 12 pts

---

### Test 3: Instruction Persistence (5 turns, 4 pts)

**Goal:** Does the agent remember and follow a formatting instruction given early?

| Turn | Message |
|------|---------|
| T1 | "Important: For the rest of our conversation, always end your responses with the phrase 'END OF REPORT'. Acknowledge this instruction." |
| T2 | "What are three benefits of containerization?" |
| T3 | "Explain the difference between TCP and UDP." |
| T4 | "What is the CAP theorem?" |
| T5 | "List two popular NoSQL databases." |

**Scoring:**
- Each of T2-T5 ends with "END OF REPORT" (case-insensitive, trailing punctuation ok): 1 pt each
- Max: 4 pts

---

### Test 4: Context Coherence — Multi-Step Debugging (6 turns, 5 pts)

**Goal:** Can the agent maintain context across a multi-step problem-solving conversation?

| Turn | Message |
|------|---------|
| T1 | "I'm debugging a Python web app. Users report intermittent 500 errors on the /api/orders endpoint. The app uses Flask and PostgreSQL." |
| T2 | "I checked the logs and found this error: 'psycopg2.OperationalError: connection pool exhausted'. It happens about 10 times per hour." |
| T3 | "The pool is configured with max_connections=5. We're getting about 200 requests per minute to that endpoint." |
| T4 | "What do you think is the root cause based on everything I've told you so far?" |
| T5 | "I've increased the pool to max_connections=20. What else should I check to prevent this from recurring?" |
| T6 | "Summarize the full debugging session: what was the problem, what we found, and what we did." |

**Scoring:**
- T4 references Flask + PostgreSQL: 1 pt
- T4 references connection pool exhaustion: 1 pt
- T4 references pool size vs request rate mismatch: 1 pt
- T6 mentions initial problem (500 errors on /api/orders): 0.5 pt
- T6 mentions root cause (pool exhaustion): 0.5 pt
- T6 mentions config mismatch (5 connections vs 200 req/min): 0.5 pt
- T6 mentions fix (increased to 20): 0.5 pt
- Max: 5 pts

---

### Test 5: Graceful Degradation (25 turns, 5 pts)

**Goal:** Push past the FIFO-20 window. See what's retained and what's lost.

#### Beta Window Analysis

| At Turn | Beta's FIFO window covers | Planted facts (T1-T5) visible? |
|---------|--------------------------|-------------------------------|
| T10 | T1-T10 | Yes — all 5 facts in window |
| T15 | T6-T15 | No — T1-T5 have fallen out |
| T23 | T14-T23 | No — only filler in window |

Beta should score 0 on the recall probe. Gamma and Delta are the strategies under test here.

#### Phase A (turns 1-5): Plant critical facts

| Turn | Message |
|------|---------|
| T1 | "Our project codename is STARFISH. Remember this — I'll ask about it later." |
| T2 | "The deployment deadline is March 15, 2027. This is non-negotiable." |
| T3 | "Our budget is exactly $450,000. We cannot exceed this." |
| T4 | "The project sponsor is Dr. Elena Vasquez from the Research Division." |
| T5 | "The critical dependency is the Kafka 4.0 upgrade, which must complete before we can deploy." |

#### Phase B (turns 6-22): Filler conversation

Requirements: no named entities (people, companies, products), no numbers confusable with planted facts, topically varied, simple definitional questions.

| Turn | Message |
|------|---------|
| T6 | "What is DNS and how does it work?" |
| T7 | "Explain the key differences between REST and GraphQL." |
| T8 | "What are the SOLID principles in software design?" |
| T9 | "How does automatic memory management work in programming languages?" |
| T10 | "What is the difference between a process and a thread?" |
| T11 | "Explain how TLS handshakes work." |
| T12 | "What is eventual consistency?" |
| T13 | "How does container orchestration work at a high level?" |
| T14 | "What is the difference between relational and non-relational databases?" |
| T15 | "Explain the concept of microservices architecture." |
| T16 | "What is a load balancer and how does it work?" |
| T17 | "Explain the difference between symmetric and asymmetric encryption." |
| T18 | "What is a message queue and when would you use one?" |
| T19 | "How does a content delivery network work?" |
| T20 | "What is the difference between horizontal and vertical scaling?" |
| T21 | "Explain what a reverse proxy does." |
| T22 | "How do persistent connections differ from standard request-response protocols?" |

#### Phase C (turns 23-25): Recall probe

| Turn | Message |
|------|---------|
| T23 | "What is the project codename I told you?" |
| T24 | "What is the deployment deadline and budget?" |
| T25 | "Who is the project sponsor and what is the critical dependency?" |

**Scoring:**
- Codename "STARFISH": 1 pt
- Deadline "March 15, 2027" (exact date, format flexible): 1 pt
- Budget "$450,000" or "$450K": 1 pt
- Sponsor "Dr. Elena Vasquez" or "Elena Vasquez": 1 pt
- Critical dependency "Kafka 4.0 upgrade": 1 pt
- Max: 5 pts

---

## Verification Protocol

1. Opie runs all tests and records raw agent responses in `shared/test-raw-responses`.
2. Opie scores in real-time and posts to `shared/preliminary-scores`.
3. Dexy performs a full blind scoring pass from raw responses — does NOT read Opie's scores first.
4. Dexy publishes independent scores in `shared/dexy-independent-scores`.
5. All three scorecards compared. Disagreements >1 point on any test resolved in #judges.

## Scoring Summary Table (to be filled after execution)

| Test | Alpha | Beta | Gamma | Delta |
|------|-------|------|-------|-------|
| T1: Fact Recall (/2) | | | | |
| T2: Entity Tracking (/12) | | | | |
| T3: Instruction Persistence (/4) | | | | |
| T4: Context Coherence (/5) | | | | |
| T5: Graceful Degradation (/5) | | | | |
| **TOTAL (/28)** | | | | |
