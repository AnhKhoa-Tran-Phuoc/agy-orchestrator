---
name: optimal-worker-routing
description: "Benchmark-backed specialization and 3-tier quota failover matrix for routing tasks across all 7 Workers (`worker-prompt-refiner`, `worker-gemini-flash`, `worker-deepseek`, `worker-glm`, `worker-mimo`, `worker-nemotron`, `worker-nemotron-super`)."
---

# Optimal Worker Routing & 3-Tier Failover Matrix (7 Specialized Workers)

Never use a single worker for everything. Route each subtask to the worker whose underlying model architecture is strongest at that exact task while costing **0 OpenAI tokens**, and automatically cascade down the **3-Tier Failover Chain** if any OpenCode Free model hits a `429` rate limit or daily cap.

---

## 1. The 7-Worker Specialization & Automatic Failover Table

Each `opencode/*-free` model uses an **independent quota bucket**. When a Primary Worker hits a rate limit (`429`), timeout, or daily free cap, immediately switch to its **Tier-2 Free Sibling** (`\$0`), and if OpenCode Free is globally throttled, escalate to **Tier-3 `delegate_flash_worker`** (`gemini-3.8-flash` on `agy` CLI backed by the 3-Account Gemini Web CDP rotation pool `acc-1` → `acc-2` → `acc-3`):

| Task Domain | Tier 1: Primary Worker (`\$0`) | Tier 2: Free Sibling Backup (`\$0`, Independent Bucket) | Tier 3: 3-Account Pool Escalation |
| :--- | :--- | :--- | :--- |
| **Stage-0 Prompt Refinement** | **`@worker-prompt-refiner`** (`glm-5.3-flash-free`) | **`@worker-nemotron`** (`nemotron-3.5-lightning-free`) | `delegate_flash_worker(flash_level="low")` |
| **Fast Recon & Test Runner** | **`@worker-nemotron`** (`nemotron-3.5-lightning-free`) | **`@worker-glm`** (`glm-5.3-flash-free`) | `delegate_flash_worker(flash_level="low")` |
| **Pre-Review, Debug & Vision** | **`@worker-nemotron-super`** (`nemotron-3.5-super-vl-free`) | **`@worker-deepseek`** (`deepseek-v4.1-flash-free`) | `argon_boss_review` / `delegate_flash_worker("high")` |
| **Pure Code & Unit Tests** | **`@worker-deepseek`** (`deepseek-v4.1-flash-free`) | **`@worker-nemotron-super`** (`nemotron-3.5-super-vl-free`) | `delegate_flash_worker(flash_level="medium")` |
| **Frontend UI/UX & Configs** | **`@worker-mimo`** (`mimo-v2.6-flash-free`) | **`@worker-deepseek`** (`deepseek-v4.1-flash-free`) | `delegate_flash_worker(flash_level="medium")` |
| **Bilingual Docs & State** | **`@worker-glm`** (`glm-5.3-flash-free`) | **`@worker-mimo`** (`mimo-v2.6-flash-free`) | `delegate_flash_worker(flash_level="low")` |
| **Heavy Multi-File (1M ctx)** | **`@worker-gemini-flash`** / `delegate_flash_worker` (`agy` CLI) | **3-Account Gemini Web CDP** (`acc-1` → `acc-2` → `acc-3`) | `@worker-deepseek` + `@worker-nemotron-super` split |

---

## 2. Optimal Zero-Waste Workflow Pipeline

0. **Prompt Refinement (\$0)**: `@worker-prompt-refiner` anchors file paths and outputs a `<160-word` Boss Execution Brief.
1. **Reconnaissance (\$0)**: `@worker-nemotron` locates relevant files & symbols.
2. **Architecture Plan (Low Quota)**: `boss-gpt` (`effort=medium/high`) or `argon_boss_review(review_type='plan')` writes a short numbered spec.
3. **Parallel Execution (\$0 Free first, Flash for heavy multi-file)**:
   - Backend module + unit tests → `@worker-deepseek` (Fallback: `@worker-nemotron-super` → `delegate_flash_worker`)
   - Frontend component / config → `@worker-mimo` (Fallback: `@worker-deepseek` → `delegate_flash_worker`)
   - Cross-file integration → `delegate_flash_worker(flash_level='high')` (Auto-swaps across 3 Google accounts)
   - Documentation / user guide → `@worker-glm` (Fallback: `@worker-mimo` → `delegate_flash_worker`)
4. **Verification & Pre-Review (\$0)**:
   - `@worker-nemotron` runs `pytest` / linter.
   - `@worker-nemotron-super` performs Stage-0 code review & visual check.
5. **Final Sign-off**: `boss-argon` (3 Web accounts) + `boss-gpt` final approval.

