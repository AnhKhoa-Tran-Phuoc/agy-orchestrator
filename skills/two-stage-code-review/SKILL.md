---
name: two-stage-code-review
description: Perform a two-stage, defect-first code audit combining spec compliance and Gemini 4 Argon review (adapted from obra/superpowers requesting-code-review & OpenAI Codex review-agent). Use after workers finish coding.
---

# Two-Stage Code Review (`two-stage-code-review`)

Never trust a worker's self-reported "Done!" message. Every worker change must pass a **Two-Stage Boss Review** before completion.

## Stage 1: Spec Compliance & Diff Sanity Check (Fast)
1. Call `inspect_workspace_changes(workspace_dir=..., include_patch=True)` (or use `read_file_excerpt` on key ranges).
2. Verify:
   - Did the worker create/modify the exact required files?
   - Were any unrelated files accidentally modified or deleted?
   - Are there obvious placeholders (`TODO`, `pass`, `NotImplementedError`, truncated code)?

## Stage 2: Defect-First Technical Audit (`[P0]`–`[P3]`)
For non-trivial logic, invoke MCP tool `argon_boss_review(review_type="code_review", argon_level="high", ...)` or perform a strict Boss audit using the following priority scale:

- **`[P0]` Blocker**: Crashes, data corruption, security vulnerabilities, broken imports/syntax.
- **`[P1]` Urgent Defect**: Logic bug, unhandled edge case, race condition, or broken requirement that must be fixed before sign-off.
- **`[P2]` Ordinary Defect**: Missing test coverage for a critical branch, poor error handling, type mismatch.
- **`[P3]` Minor Nit**: Naming/formatting issue (only fix if touching the file anyway).

### Format for Findings
`[P1] Imperative finding title — path/to/file.py:line`
Followed by 1–2 sentences explaining the failure scenario and the exact fix required.

## Remediation Loop
- If any `P0`, `P1`, or `P2` findings exist:
  - For an Antigravity worker: call `continue_worker_session(conversation_id=..., feedback_prompt=...)` with the exact `[P0]`–`[P2]` findings.
  - For an OpenCode subagent: dispatch `@worker-deepseek` or `@worker-gemini-flash` with the exact file, line numbers, and fix instructions.
- Re-verify until `No findings` (`VERDICT: APPROVED`).

