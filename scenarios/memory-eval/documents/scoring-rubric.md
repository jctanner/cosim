# Memory Evaluation Scoring Rubric
## Pre-registered before test execution

### Purpose
This rubric defines scoring criteria BEFORE any tests are run, to prevent post-hoc rationalization of results.

---

## Scoring Categories (per recall question)

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

| Strategy | Short (5-6 turns) | Deep (20+ turns) | Entity | Instruction | Interference |
|----------|-------------------|-------------------|--------|-------------|-------------|
| Alpha (none) | 0-1 | 0-1 | 0-1 | 0-1 | 0-1 |
| Beta (FIFO-20) | 4-5 | 0-2 | 3-4 | 0-2 | 3-5 |
| Gamma (summary) | 4-5 | 2-4 | 2-4 | 3-5 | 2-4 |
| Delta (entity) | 4-5 | 2-4 | 4-5 | 2-4 | 3-5 |

These are hypotheses, not targets. The point is to document expectations BEFORE observing results.

---

## Aggregation Rules

- Each test category will have multiple planted facts (minimum 3 per category)
- Final score per agent per category = mean of individual fact scores
- A strategy is "better" only if it scores ≥1.5 points higher on average (to account for noise)
- Any unexpected result (e.g., Alpha scoring 5 on deep recall) should be investigated as a potential methodological error before being accepted

---

## Confound Checklist

Before attributing a difference to memory strategy, rule out:
- [ ] Was the test message identical across all agents?
- [ ] Was the turn count identical?
- [ ] Was sufficient time given between messages for memory processing?
- [ ] Could the model have inferred the answer from the question itself? (information leakage)
- [ ] Is the sample size sufficient (≥3 facts per category)?

---

*This rubric is pre-registered. Do not modify after tests begin.*
