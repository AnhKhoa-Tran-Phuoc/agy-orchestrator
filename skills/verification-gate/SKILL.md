---
name: verification-gate
description: Enforce empirical verification before claiming any task is complete (adapted from obra/superpowers verification-before-completion). Use before final sign-off, git commit, or reporting success to the user.
---

# Verification Gate Before Completion (`verification-gate`)

## Core Iron Law
**Evidence before claims, always.** Never say a bug is fixed, a feature works, or tests pass unless you have fresh command output proving it in the current turn.

## The 5-Step Gate Function

Before reporting completion to the user (or creating a Git commit):

1. **IDENTIFY**: What exact shell command proves the deliverable works? (e.g., `pytest -q`, `python -m py_compile ...`, `npm test`, `ruff check`).
2. **RUN**: Execute the command via MCP tool `run_verification_check(command=..., workspace_dir=...)` or delegate execution to `@worker-nemotron`.
3. **READ**: Check `exit_code`, `passed`, and `output_digest`.
4. **VERIFY**:
   - If `passed == False` or `exit_code != 0`: **STOP**. Do not claim success. Enter `systematic-worker-debugging` and dispatch a worker to fix the failure.
5. **CLAIM**: Only after `passed == True` with fresh output, present the final summary and verification evidence to the user.

## Red Flags (Automatic Stop Triggers)
- Using hedging phrases like *"should work now"*, *"probably passes"*, or *"looks good"* without running the check.
- Relying on a test run from BEFORE the latest worker edit.
