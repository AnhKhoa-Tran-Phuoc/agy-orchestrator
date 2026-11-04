---
name: openai-quota-shield
description: "Strict token-saving protocol for single-account OpenAI Managers (`boss-gpt`, `boss-gpt-sol`): caps reasoning effort at `high` (never `xhigh`/`max`), bans raw file reads into Manager context, and offloads pre-review and reconnaissance to 0-cost Free models."
---

# OpenAI Quota Shield (Single-Account Protection Protocol)

Use this skill whenever operating as an OpenAI-backed Manager (`openai/gpt-6-astra`, `openai/gpt-6.1-sol`, `openai/gpt-6.1-terra`) where the user has **only 1 OpenAI account** and must minimize both input context accumulation and reasoning output tokens.

---

## 1. Reasoning Effort Cap (`MAX = high`)

- **NEVER select `xhigh` or `max` reasoning effort** on OpenAI models (`xhigh`/`max` consumes 3x–6x more internal reasoning tokens per turn).
- **Default to `medium` effort** for task decomposition, subagent dispatch, and status checks.
- **Use `high` effort ONLY** for initial system architecture or final `[P0]`/`[P1]` security/correctness sign-off.
- Keep visible Manager responses **under 180 words** (concise bullet points + tool/subagent calls).

---

## 2. Zero Raw-File Ingestion Rule (Saves ~90% Input Tokens)

In multi-turn conversations, every file read into the Manager's context is re-billed on every subsequent turn.

- **FORBIDDEN**: Calling `read` on full files (`>60 lines`) or running verbose shell commands directly from `boss-gpt`.
- **REQUIRED**:
  1. Dispatch **`@worker-nemotron`** (`opencode/nemotron-3.5-lightning-free` — 0 cost) to search the codebase (`grep`/`glob`/`read`) and return a **<150-word digest** of exact file paths, line numbers, and function signatures.
  2. When spot-checking a specific function, call MCP `read_file_excerpt(file_path, start_line, end_line, max_chars=1800)` or `inspect_workspace_changes(max_chars=2000)`.

---

## 3. Free Stage-0 Pre-Review Pipeline

Never spend OpenAI tokens reviewing buggy first-draft worker code:

1. Worker (`@worker-deepseek`, `@worker-gemini-flash`, `@worker-mimo`, or `@worker-glm`) writes the code/docs on disk.
2. **`@worker-nemotron`** runs the test suite (`pytest`, `ruff`, `npm test`) at 0 OpenAI cost.
3. **`@worker-nemotron-super`** (`opencode/nemotron-3.5-super-vl-free` — 0 cost) or **`boss-argon`** (`argon_boss_review`, backed by 3 rotating Google Web accounts) performs Stage-0/Stage-1 code review (`[P0]`–`[P3]`).
4. Only after tests pass and Stage-0/Stage-1 review is clean does `boss-gpt` perform the final lightweight sign-off.

