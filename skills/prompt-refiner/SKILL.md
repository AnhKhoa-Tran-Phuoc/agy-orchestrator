---
name: prompt-refiner
description: "Transform short or conversational Vietnamese/English user requests into structured, codebase-aware Boss Execution Briefs using @worker-prompt-refiner at zero OpenAI token cost."
---

# Stage-0 Prompt Refinement & Codebase Anchoring (`prompt-refiner`)

Use this skill whenever:
1. The user invokes `/prompt-refiner` to polish a raw request before sending it to a Boss, OR
2. A Boss (`boss-gpt`, `boss-gpt-sol`, `boss-argon`) receives a short, conversational, or underspecified user prompt that lacks exact file paths, symbol names, or verification criteria.

## Why Stage-0 Prompt Refinement Saves 50%–70% Boss Quota
When a Boss receives a vague prompt (e.g., *"thêm chức năng X"* or *"fix lỗi Y"*), the Boss normally spends multiple high-cost reasoning turns searching the repo and inferring requirements. By delegating Stage-0 prompt refinement to **`@worker-prompt-refiner`** (`opencode/glm-5.3-flash-free` — **$0 cost**):
- `@worker-prompt-refiner` runs 1–3 fast `glob`/`grep` checks to locate the exact files and symbols in the workspace.
- `@worker-prompt-refiner` rewrites the raw request into a `<160-word` structured **Boss Execution Brief**.
- The Boss receives the exact file coordinates, constraints, recommended worker assignment, and test command on Turn 1.

## Standard BossExecutionBrief Output Template

```markdown
### Boss Execution Brief
- **[GOAL]**: <1–2 sentence precise technical objective>
- **[TARGET FILES & SYMBOLS]**: `<path/to/file.py>` (`ClassName.method_name`), `<tests/test_file.py>`
- **[REQUIREMENTS & CONSTRAINTS]**:
  - <Requirement 1>
  - <Edge case / constraint 2>
- **[RECOMMENDED WORKER ROUTING]**: `<@worker-deepseek | @worker-gemini-flash | @worker-mimo | ...>`
- **[VERIFICATION GATE]**: `<exact pytest / linter command>`
```
