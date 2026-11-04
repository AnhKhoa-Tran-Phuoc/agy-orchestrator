---
name: systematic-worker-debugging
description: Scientific root-cause debugging workflow across Scout, Boss, and Coder agents (adapted from obra/superpowers systematic-debugging). Use whenever tests fail or an unexpected error occurs.
---

# Systematic Multi-Agent Debugging (`systematic-worker-debugging`)

When a test fails or a runtime error appears, **never let workers guess or apply random shotgun patches**. Follow this strict 4-phase loop:

## Phase 1: Scout & Reproduce (`@worker-nemotron` or `run_verification_check`)
1. Run the failing test or reproduction command via `run_verification_check` or `@worker-nemotron`.
2. Capture the exact error type, traceback file/line numbers, and failing input.
3. Use `read_file_excerpt` around the implicated lines (do not load the entire codebase).

## Phase 2: Root-Cause Hypothesis (Boss GPT / Gemini 4 Argon)
1. Formulate a specific root-cause hypothesis explaining *why* the state or data at `file:line` is wrong.
2. For complex multi-module bugs, call `argon_boss_review(task_context=..., review_type="plan", file_paths=[...])` so **Gemini 4 Argon High** traces the data flow and pinpoints the root cause.

## Phase 3: Regression Test + Minimal Fix (`@worker-gemini-flash` or `@worker-deepseek`)
Send a targeted fix brief to the worker requiring:
1. A regression test that reproduces the exact bug.
2. The minimal code change that fixes the root cause without breaking adjacent behavior.
*(If the bug was introduced by an active Antigravity worker session, use `continue_worker_session` with its `conversation_id`.)*

## Phase 4: Verification Gate
Run `run_verification_check` to confirm both the new regression test and the existing test suite pass with `exit_code == 0`.

