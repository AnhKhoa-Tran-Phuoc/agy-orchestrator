# agy-orchestrator

[![CI](https://github.com/AnhKhoa-Tran-Phuoc/agy-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/AnhKhoa-Tran-Phuoc/agy-orchestrator/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP v2](https://img.shields.io/badge/MCP-2.0%2B-green.svg)](https://modelcontextprotocol.io/)
[![OpenCode v2](https://img.shields.io/badge/OpenCode-v2.2%2B-purple.svg)](https://opencode.ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Quota-Aware Multi-Agent Orchestration System for OpenCode, ChatGPT Desktop, and Codex CLI — combining OpenAI GPT & Gemini 4 Argon Bosses with Google Antigravity (`agy`) & Zero-Cost OpenCode Workers.**

`agy-orchestrator` is a production-grade Model Context Protocol (MCP) server, CLI toolkit, and multi-agent configuration suite designed to **offload 85%–95% of token consumption** away from single-account Manager models onto specialized Worker agents, multi-account Antigravity CLI pools, and automated Chrome DevTools Protocol (CDP) Gemini Web sessions.

---

## Key Capabilities

- **Strict Manager–Worker Separation**:
  - **Boss Tier (Managers & Code Reviewers)**: `openai/gpt-6-astra` (capped at `high` reasoning effort), `openai/gpt-6.1-sol`, and `gemini-4-argon-high` focus exclusively on architecture planning, task decomposition, bounded `git diff` inspection, and final `[P0]`–`[P3]` code sign-off (`edit: deny`).
  - **Worker Tier (Execution Engine)**: All heavy code generation, multi-file refactoring, test writing, codebase reconnaissance, and documentation authoring are delegated to **Gemini 3.8 Flash** (via `agy` CLI) and **5 specialized zero-cost OpenCode subagents**.
- **Hands-Free Gemini Web CDP Bridge (`gemini_web_cdp.py`)**:
  - When local Antigravity CLI weekly Argon quota is exhausted on the primary account, `boss-argon` automatically connects to isolated Google Chrome profiles over Chrome DevTools Protocol (`127.0.0.1:9222..9224`), inputs the architectural/review prompt into `https://gemini.google.com/app`, waits for streaming completion, and extracts the response **100% hands-free**.
- **Multi-Account 5-Hour & Weekly Quota Rotation (`account_pool.py`)**:
  - Tracks rolling **5-hour (`5h`)** and **7-day (`weekly`)** token and run windows per Google account (`acc-1`, `acc-2`, `acc-3`, ...).
  - Decouples **Argon Boss routing** (`acc-1` CLI + Web → `acc-2` Web → `acc-3` Web) from **Gemini 3.8 Flash Worker routing** (`acc-1` CLI → `acc-2` CLI → `acc-3` CLI), automatically placing exhausted accounts into 5-hour or 7-day cooldowns and rotating to the next healthy account at an `85%` soft threshold.
- **9 Battle-Tested Multi-Agent & Quota-Protection Skills (`skills/`)**:
  - Synthesized from leading open-source agent frameworks (`obra/superpowers`, `oh-my-opencode`, `BMAD-METHOD`, `MetaGPT`, and OpenAI Codex `review-agent`), plus custom single-account protection protocols (`openai-quota-shield`, `context-compaction-guard`, `optimal-worker-routing`).

---

## System Architecture

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         BOSS TIER (MANAGERS & REVIEWERS)                          │
│         Architecture Planning • Task Dispatch • Diff Audit • Merge Sign-Off       │
├───────────────────────────┬───────────────────────────┬───────────────────────────┤
│ 1. boss-gpt (Primary)     │ 2. boss-gpt-sol (Primary) │ 3. boss-argon (All Modes) │
│ openai/gpt-6-astra        │ openai/gpt-6.1-sol        │ Gemini 4 Argon            │
│ (MAX Effort = high)       │ (MAX Effort = high)       │ • Tier 1: agy CLI (acc-1) │
│ • edit: deny              │ • edit: deny              │ • Tier 2: Chrome CDP Web  │
│ • Zero Raw-File Ingestion │ • Final Gatekeeper        │   Auto-Rotation Pool      │
│                           │                           │   (acc-1:9222→acc-2:9223  │
│                           │                           │    →acc-3:9224)           │
└─────────────┬─────────────┴─────────────┬─────────────┴─────────────┬─────────────┘
              │                           │                           │
              │     Zero-Context Briefs • Parallel Subagent & MCP Dispatch
              ▼                           ▼                           ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         WORKER TIER (EXECUTION ENGINE)                            │
├─────────────────────────────────────────┬─────────────────────────────────────────┤
│ Group A: Antigravity CLI (agy)          │ Group B: Zero-Cost OpenCode Workers     │
│ • @worker-gemini-flash                  │ • @worker-deepseek (Code & Unit Tests)  │
│   (gemini-3.8-flash-high/medium/low)    │   opencode/deepseek-v4.1-flash-free     │
│   1M-context multi-file engineering     │ • @worker-nemotron-super (VL & Audit)   │
│   with 5h/weekly 3-account auto-swap    │   opencode/nemotron-3.5-super-vl-free   │
│                                         │ • @worker-nemotron (Fast Recon & Test)  │
│                                         │   opencode/nemotron-3.5-lightning-free  │
│                                         │ • @worker-mimo (Frontend UI & Configs)  │
│                                         │   opencode/mimo-v2.6-flash-free         │
│                                         │ • @worker-glm (Technical Docs & State)  │
│                                         │   opencode/glm-5.3-flash-free           │
└─────────────────────────────────────────┴─────────────────────────────────────────┘
```

---

## The 10 OpenCode Agents (`opencode_agents/`)

| Agent ID | Mode | Underlying Model | Primary Responsibility |
| :--- | :---: | :--- | :--- |
| **`boss-gpt`** | `primary` | `openai/gpt-6-astra` *(max `high`)* | Chief Engineering Manager; decomposes goals, dispatches workers, enforces verification gates (`edit: deny`). |
| **`boss-gpt-sol`** | `primary` | `openai/gpt-6.1-sol` *(max `high`)* | Chief Architect & Final Reviewer; audits diffs and signs off after Stage-0/Stage-1 checks (`edit: deny`). |
| **`boss-argon`** | `all` | `gemini-4-argon-high` *(CLI + Web CDP)* | Senior Architect & Defect-First Reviewer (`[P0]`–`[P3]`); auto-rotates across CLI and Chrome CDP Web accounts. |
| **`worker-prompt-refiner`** | `all` | `opencode/glm-5.3-flash-free` | Stage-0 Prompt Optimizer & Context Scout; transforms raw Vietnamese/English prompts into codebase-aware Boss Execution Briefs (**Free**). |
| **`worker-gemini-flash`** | `subagent` | `gemini-3.8-flash-high/medium/low` | Heavy multi-file implementation, cross-cutting refactors, and 1M-context codebase operations via `agy`. |
| **`worker-deepseek`** | `subagent` | `opencode/deepseek-v4.1-flash-free` | Pure algorithmic coding (Python/TS/JS/Go/Rust), bug fixes, and `pytest`/`vitest` unit test generation (**Free**). |
| **`worker-nemotron-super`** | `subagent` | `opencode/nemotron-3.5-super-vl-free` | Stage-0 Pre-Reviewer, deep root-cause stack-trace debugger, and multimodal UI/screenshot inspector (**Free**). |
| **`worker-nemotron`** | `subagent` | `opencode/nemotron-3.5-lightning-free` | Ultra-fast codebase scout (`grep`/`glob`) and test/linter runner returning `<150-word` digests (**Free**). |
| **`worker-mimo`** | `subagent` | `opencode/mimo-v2.6-flash-free` | Frontend UI/UX components (HTML/CSS/React/Tailwind), project scaffolding, and `JSON`/`YAML`/`TOML` configs (**Free**). |
| **`worker-glm`** | `subagent` | `opencode/glm-5.3-flash-free` | Technical documentation (`README.md`, API guides, bilingual English/Vietnamese docs) and `.agy-orch/STATE.md` (**Free**). |

---

## Bundled Multi-Agent & Quota-Protection Skills (`skills/`)

| Skill Name | Reference Lineage | Purpose |
| :--- | :--- | :--- |
| **`antigravity-manager`** | `oh-my-opencode`, `MetaGPT` | Master orchestration matrix between Bosses and specialized Workers. |
| **`prompt-refiner`** | Custom Prompt Engineering | Stage-0 prompt optimization that anchors file paths & symbols via `@worker-prompt-refiner` at zero OpenAI token cost. |
| **`quota-monitor`** | Custom Quota Engineering | Real-time per-model 5-hour (`5h`) and 7-day (`weekly`) quota telemetry dashboard (`agy-orch quota`). |
| **`openai-quota-shield`** | Custom Quota Engineering | Caps OpenAI reasoning effort at `high`, bans raw file reads into Manager context, and enforces Stage-0 free pre-review. |
| **`context-compaction-guard`** | Custom Context Engineering | Externalizes session state to `.agy-orch/STATE.md` via free workers and triggers `/compact` checkpoints every 6–8 turns. |
| **`optimal-worker-routing`** | Benchmark Routing Matrix | Assigns every subtask to the highest-performing model across the 7-Worker lineup at minimum token cost. |
| **`zero-context-planning`** | `obra/superpowers`, `BMAD-METHOD` | Mandatory 5-part self-contained specification template for fresh-context worker subagents. |
| **`parallel-agent-dispatch`** | `obra/superpowers` | Enforces disjoint file ownership when running concurrent worker batches. |
| **`two-stage-code-review`** | `obra/superpowers`, OpenAI Codex | Two-stage audit: Spec Compliance check followed by `[P0]`–`[P3]` defect classification. |
| **`verification-gate`** | `obra/superpowers` | 5-step empirical gate (**IDENTIFY → RUN → READ → VERIFY → CLAIM**) requiring fresh test output before completion. |
| **`systematic-worker-debugging`** | `obra/superpowers` | 4-phase root-cause reproduction, diagnosis, regression-test authoring, and minimal fix loop. |

---

## Installation & Quick Start

### 1. Clone & Install Package

```powershell
git clone https://github.com/AnhKhoa-Tran-Phuoc/agy-orchestrator.git
cd agy-orchestrator

python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

### 2. Install into OpenCode (Global or Per-Project)

```powershell
# Install globally into ~/.config/opencode (9 agents + 9 skills + antigravity_workers MCP)
.\.venv\Scripts\agy-orch.exe install-opencode

# Reload OpenCode daemon
opencode reload
```

*(Optional)* Install into ChatGPT Desktop / Codex CLI (`~/.codex`):

```powershell
.\.venv\Scripts\agy-orch.exe install-codex
```

### 3. Configure Multi-Account Google Pool (One-Time Sign-In)

Register and sign in to your Google accounts (`acc-1` with CLI+Web Argon, `acc-2..N` with Web Argon + CLI Flash):

```powershell
# Open isolated Chrome CDP profiles for one-time Google sign-in
.\.venv\Scripts\agy-orch.exe account-login acc-1   # Port 9222 (Primary Google Pro)
.\.venv\Scripts\agy-orch.exe account-login acc-2   # Port 9223 (Secondary Account 2)
.\.venv\Scripts\agy-orch.exe account-login acc-3   # Port 9224 (Secondary Account 3)

# Verify 5-hour & weekly rolling quota status across all accounts
.\.venv\Scripts\agy-orch.exe account-status

# Test hands-free Gemini Web auto-input and output extraction
.\.venv\Scripts\agy-orch.exe web-ask "Provide a one-sentence health check confirmation."
```

---

## MCP Tools Reference (`antigravity_workers`)

| Tool Name | Category | Description |
| :--- | :---: | :--- |
| `argon_boss_review` | Boss / Review | Consult **Gemini 4 Argon** (`plan` mode) for architecture breakdown or `[P0]`–`[P3]` code audit; auto-falls back to Chrome CDP Web on quota exhaustion. |
| `ask_gemini_web_auto` | Boss / Web CDP | Hands-free Playwright CDP bridge that inputs prompts into `gemini.google.com/app` and extracts the streamed response. |
| `prepare_web_boss_handoff` | Boss / Handoff | Packages prompt + diff snapshot to `.agy-orch/web_handoff/` and copies to clipboard. |
| `delegate_flash_worker` | Worker Execution | Delegate multi-file coding, refactoring, or long writing to `gemini-3.8-flash-high`/`medium`/`low` (`accept-edits`). |
| `delegate_task` | Worker Execution | Delegate a task to any Antigravity model by task `tier` (`trivial`, `writing`, `coding`, `complex`) or explicit `model`. |
| `continue_worker_session` | Worker Execution | Resume a prior worker session by `conversation_id` to apply review feedback with full prior context. |
| `delegate_parallel_tasks` | Worker Execution | Run multiple independent Antigravity worker tasks concurrently (`max_concurrency` bounded). |
| `inspect_workspace_changes` | Low-Token Audit | Inspect `git status`, `git diff --stat`, and bounded unified diff patches. |
| `read_file_excerpt` | Low-Token Audit | Read a bounded, line-numbered excerpt (`start_line`..`end_line`) of a file. |
| `run_verification_check` | Verification | Run a test suite or linter inside the workspace and return pass/fail status with a compact log digest. |
| `get_account_pool_status` | Account Pool | Inspect 5-hour (`pct_5h`) and 7-day (`pct_weekly`) quota usage, cooldowns, and active account pointers. |
| `register_google_account` | Account Pool | Add or update a Google account slot (`acc-1`, `acc-2`, ...) in the rotation pool. |
| `swap_active_account` | Account Pool | Proactively rotate the active account to the next healthy account in the pool. |
| `get_quota_savings_report` | Telemetry | Cumulative token offloading ledger report (`antigravity_total_tokens` vs `estimated_manager_tokens`). |
| `list_worker_models` | Discovery | Discover all models available in the local `agy` installation and view tier mappings. |

---

## Running Tests

```powershell
.\.venv\Scripts\pytest.exe -v
```

---

## License

Released under the [MIT License](LICENSE).
