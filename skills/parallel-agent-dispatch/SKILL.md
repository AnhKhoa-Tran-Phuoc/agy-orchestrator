---
name: parallel-agent-dispatch
description: Dispatch multiple independent tasks concurrently across Free OpenCode subagents and Gemini 3.8 Flash workers (adapted from obra/superpowers dispatching-parallel-agents). Use when 2+ tasks touch disjoint files.
---

# Parallel Agent Dispatch (`parallel-agent-dispatch`)

When a feature or request contains 2 or more independent subtasks, sequential execution wastes time. Dispatch them in parallel across your worker pool.

## 1. The Disjoint File Ownership Rule (Critical)
Before launching parallel workers, verify that **no two concurrent workers edit the same file**:
- **Valid parallel batch**:
  - Worker 1 (`@worker-gemini-flash` or `@worker-deepseek`): `src/api/routes.py`
  - Worker 2 (`@worker-deepseek`): `tests/test_routes.py`
  - Worker 3 (`@worker-glm`): `docs/api_guide.md`
  - Worker 4 (`@worker-mimo`): `frontend/components/ApiStatus.tsx`
- **Invalid parallel batch**:
  - Worker 1 and Worker 2 both editing `src/api/routes.py` (causes race conditions and lost edits).

## 2. How to Dispatch in Parallel
- **For Antigravity Workers (`gemini-3.8-flash-*`)**:
  Call MCP tool `delegate_parallel_tasks(tasks=[...], max_concurrency=3)` with explicit `output_files` per item.
- **For OpenCode Free Subagents (`@worker-deepseek`, `@worker-glm`, `@worker-mimo`, `@worker-nemotron`)**:
  Emit multiple subagent tool calls in the same assistant turn, giving each worker its own isolated 5-part brief from `zero-context-planning`.

## 3. Post-Batch Integration Check
After all parallel workers return:
1. Run `inspect_workspace_changes` to confirm only expected files were touched.
2. Run `run_verification_check` (or dispatch `@worker-nemotron`) to verify all modules integrate cleanly.
