---
name: optimal-worker-routing
description: "Benchmark-backed specialization matrix for routing tasks across the 6 Workers (`worker-gemini-flash`, `worker-deepseek`, `worker-glm`, `worker-mimo`, `worker-nemotron`, `worker-nemotron-super`) to maximize quality and minimize quota."
---

# Optimal Worker Routing Matrix (6 Specialized Workers)

Never use a single worker for everything. Route each subtask to the worker whose underlying model architecture is strongest at that exact task while costing **0 OpenAI tokens**.

---

## 1. The 6-Worker Specialization Table

| Worker Agent | Underlying Model | Cost | Primary Superpower (When to Pick) |
| :--- | :--- | :---: | :--- |
| **`@worker-nemotron`** | `opencode/nemotron-3.5-lightning-free` | **\$0 (Free)** | **Fast Reconnaissance & Test Runner**: Instant `grep`/`glob` across repo, running `pytest`/`ruff`/`tsc`, summarizing error logs in <5 seconds. Always run FIRST before planning! |
| **`@worker-nemotron-super`** | `opencode/nemotron-3.5-super-vl-free` | **\$0 (Free)** | **Stage-0 Pre-Reviewer, Deep Debugger & Vision (VL)**: Inspecting UI screenshots/diagrams, root-cause analysis of complex stack traces, and `[P0]`–`[P3]` pre-review before Boss sign-off. |
| **`@worker-deepseek`** | `opencode/deepseek-v4.1-flash-free` | **\$0 (Free)** | **Pure Code & Unit Tests**: Writing isolated Python/TS/JS/Go/Rust modules, data structures, algorithms, bug fixes, and comprehensive `pytest`/`vitest` test suites. |
| **`@worker-mimo`** | `opencode/mimo-v2.6-flash-free` | **\$0 (Free)** | **Frontend UI/UX & Configs**: HTML/CSS/React/Tailwind components, UI layouts, boilerplate scaffolding, and structured config files (`JSON`, `YAML`, `TOML`, `Dockerfile`). |
| **`@worker-glm`** | `opencode/glm-5.3-flash-free` | **\$0 (Free)** | **English/Vietnamese Prose & Docs**: Writing `README.md`, architecture docs, tutorials, bilingual comments/docstrings, and maintaining `.agy-orch/STATE.md`. |
| **`@worker-gemini-flash`** *(or MCP `delegate_flash_worker`)* | `gemini-3.8-flash-high` / `medium` / `low` *(3-acc auto-swap)* | **AGY Quota** *(3 accs)* | **Heavy Multi-File Engineering (1M Context)**: Cross-cutting refactors touching 4+ files, complex system integration, or when a Free OpenCode worker needs an escalation upgrade. |

---

## 2. Optimal Zero-Waste Workflow Pipeline

1. **Reconnaissance (\$0)**: `@worker-nemotron` locates relevant files & symbols.
2. **Architecture Plan (Low Quota)**: `boss-gpt` (`effort=medium/high`) or `argon_boss_review(review_type='plan')` writes a short numbered spec.
3. **Parallel Execution (\$0 Free first, Flash for heavy multi-file)**:
   - Backend module + unit tests → `@worker-deepseek`
   - Frontend component / config → `@worker-mimo`
   - Cross-file integration → `delegate_flash_worker(flash_level='high')`
   - Documentation / user guide → `@worker-glm`
4. **Verification & Pre-Review (\$0)**:
   - `@worker-nemotron` runs `pytest` / linter.
   - `@worker-nemotron-super` performs Stage-0 code review & visual check.
5. **Final Sign-off**: `boss-argon` (3 Web accounts) + `boss-gpt` final approval.

