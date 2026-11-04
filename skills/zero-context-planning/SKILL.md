---
name: zero-context-planning
description: Create bite-sized, zero-context task specifications for worker subagents (adapted from obra/superpowers writing-plans & BMAD-METHOD). Use before delegating work to Gemini 3.8 Flash or Free OpenCode subagents.
---

# Zero-Context Worker Planning (`zero-context-planning`)

Worker subagents (`@worker-deepseek`, `@worker-glm`, `@worker-mimo`, `@worker-gemini-flash`) start with a **fresh context window**. They do NOT see your prior conversation with the user. If you send vague instructions, the worker will guess or hallucinate.

## Core Principle
Every delegated prompt must be a **complete, self-contained engineering specification** that a developer with zero prior context can execute in one shot.

## Mandatory 5-Part Worker Brief Template

Whenever calling a subagent or `delegate_flash_worker` / `delegate_task`, structure the prompt with these 5 sections:

1. **Objective (1–2 sentences)**:
   Exact behavior to implement or document.
2. **Owned Files (`output_files`)**:
   Exact relative or absolute file paths the worker is allowed to create or edit (e.g., `src/services/discount.py`, `tests/test_discount.py`).
3. **Context & Interfaces**:
   - Existing files/functions the worker should read first (e.g., *"Read `src/models/order.py` lines 1–60 for the `Order` dataclass"*).
   - Exact function/class signatures, types, and edge cases to handle.
4. **Constraints (DRY / YAGNI)**:
   - Do not modify files outside `Owned Files`.
   - Do not add unrequested dependencies.
5. **Expected Summary Format**:
   Instruct the worker: *"Write changes directly to disk. Reply only with a concise bullet summary (max 6 bullets) of files changed and key decisions—do not paste full file contents."*
