# Memory Evaluation Scoring Rubric
## Pre-registered before test execution

### Purpose
This rubric defines the failure mode taxonomy and scoring expectations BEFORE any tests are run, to prevent post-hoc rationalization of results. The primary scoring system is binary (0/1 per criterion, defined in the test protocol). This rubric provides the diagnostic classification layer.

---

## Failure Mode Classification (Diagnostic)

Use these categories to annotate each scored response. They do NOT feed into aggregate scores — they classify the quality of failures and successes for analysis.

### 5 — Full Recall
Agent reproduces the target fact with full accuracy. For numeric values, must be exact. For names, must be correctly spelled. For instructions, must demonstrate compliance.

**Example:** Planted "server IP is 10.0.42.7" → Agent responds "The server IP is 10.0.42.7" ✓

### 4 — Near Recall
Agent gets the core fact right but with minor imprecision (e.g., rounding, slight paraphrase that preserves meaning). Must not change the substance.

**Example:** Planted "budget is $14,250" → Agent says "budget is around $14,000" (close but imprecise)

### 3 — Partial Recall
Agent references the correct topic/context but gets specific details wrong or is vague.

**Example:** Planted "server IP is 10.0.42.7" → Agent says "I believe you mentioned a server IP earlier, something like 10.0.40-something"

### 2 — Confabulation
Agent produces a confident, specific answer that is WRONG. This is worse than admitting ignorance.

**Example:** Planted "server IP is 10.0.42.7" → Agent says "The server IP is 192.168.1.1"

### 1 — Acknowledged Forgetting
Agent explicitly states it doesn't have that information or can't recall. Honest about its limitations.

**Example:** "I don't have that information in our conversation" or "Could you remind me?"

### 0 — No Recall / Irrelevant
Agent ignores the question entirely, changes topic, or gives a generic response with no attempt at recall.

---

## Expected Performance Ranges

These are qualitative hypotheses, not numeric predictions mapped to the /28 binary scale. The point is to document expectations BEFORE observing results.

| Strategy | Short Recall | Deep Recall | Entity Tracking | Instruction | Interference |
|----------|-------------|-------------|-----------------|-------------|-------------|
| Alpha (none) | Fail | Fail | Fail | Fail | Fail |
| Beta (FIFO-20) | Pass | Fail (out of window) | Pass | Unknown | Pass |
| Gamma (summary) | Pass | Partial (depends on summary quality) | Partial (recency bias likely) | Unknown | Partial |
| Delta (entity) | Pass | Partial (depends on entity extraction) | Pass (designed for this) | Unknown | Pass |

---

## Aggregation Rules

- Primary scoring: binary (0/1 per criterion), summed per test and overall (/28)
- A strategy is "better" only if it scores ≥1.5 points higher on the **normalized** composite (to account for noise and unequal test weighting)
- Any unexpected result (e.g., Alpha scoring well on recall) should be investigated as a potential methodological error before being accepted
- Report both raw totals (/28) and normalized scores (each test = 20% of composite)

---

## Confound Checklist

Before attributing a difference to memory strategy, rule out:
- [ ] Was the test message identical across all agents?
- [ ] Was the turn count identical?
- [ ] Was sufficient time given between messages for memory processing?
- [ ] Could the model have inferred the answer from the question itself? (information leakage)
- [ ] Is the sample size sufficient (≥3 facts per category)?
- [ ] Did the agent use tool-mediated retrieval (tickets, docs) instead of memory? If so, annotate as "tool-assisted recall" separately.

---

*This rubric is pre-registered. Do not modify after tests begin.*
