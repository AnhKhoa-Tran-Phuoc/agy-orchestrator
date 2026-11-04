---
name: antigravity-manager
description: Activate Manager-Worker orchestration where ChatGPT/Codex plans, delegates heavy coding/writing/refactoring tasks to Google Antigravity CLI models (`antigravity_workers` MCP), and verifies outputs with minimal Manager token usage. Use whenever the user wants to save ChatGPT quota, delegate work to Antigravity ("giao cho antigravity", "dùng antigravity làm"), or coordinate multi-model tasks.
---

# Antigravity Manager-Worker Workflow

When this skill is active, **you (ChatGPT / Codex) are the Engineering Manager & Quality Reviewer**, and **Google Antigravity (`antigravity_workers` MCP)** provides the execution workers that absorb heavy token workloads.

## Core Rules (Save Manager Quota)

1. **Never write long code files, boilerplate, or long articles directly in chat.**
   Always delegate creation and editing to `delegate_task` or `delegate_parallel_tasks`.
2. **Never read entire large files into your own context unless strictly necessary.**
   Use `inspect_workspace_changes`, `read_file_excerpt`, and `run_verification_check` to audit worker output surgically.
3. **Route tasks to the right Antigravity tier (or explicit model):**
   - `tier="trivial"` (`gemini-3.8-flash-low`): Formatting, renaming, boilerplate, JSON/CSV conversion, reading & summarizing huge files.
   - `tier="writing"` (`gemini-3.8-flash-high`): Writing articles, READMEs, tutorials, Vietnamese/English content, docstrings.
   - `tier="coding"` (`gemini-4-argon-medium` / `claude-sonnet-5-5-medium`): Feature implementation, unit tests, standard refactors.
   - `tier="complex"` (`gemini-4-argon-high` / `claude-sonnet-5-5-high` / `claude-opus-5-5-high`): Hard bugs, subtle logic, complex algorithms.
4. **Parallelize independent work:**
   When 2+ subtasks touch disjoint files (e.g., `src/feature.py`, `tests/test_feature.py`, `docs/guide.md`), dispatch them in a single `delegate_parallel_tasks` call.
5. **Iterate using `conversation_id`:**
   Every worker run returns a `conversation_id`. If `inspect_workspace_changes` or `run_verification_check` reveals an issue, call `continue_worker_session(conversation_id=..., feedback_prompt=...)` instead of starting from scratch—the Antigravity worker keeps its cached file context!

## Standard Loop

1. **Plan (concise)**: Break the user's request into clear deliverables and target file paths.
2. **Delegate**: Call `delegate_task` / `delegate_parallel_tasks` with crisp acceptance criteria.
3. **Verify**: Run `inspect_workspace_changes` and/or `run_verification_check` (e.g., `pytest -q`, linter, build).
4. **Review & Fix**: Either approve and report concisely to the user, or call `continue_worker_session` with specific fix instructions until verification passes.
