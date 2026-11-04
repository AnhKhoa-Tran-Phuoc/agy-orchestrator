---
name: context-compaction-guard
description: "Prevents multi-turn token bloat on the Manager's OpenAI account by externalizing session state to `.agy-orch/STATE.md` via free workers and triggering `/compact` at optimal checkpoints."
---

# Context Compaction Guard (Multi-Turn Memory Offloading)

Every turn in an LLM conversation re-sends the entire prior message history. By Turn 10, a 4,000-token context costs 40,000 cumulative input tokens. Use this skill to keep the OpenAI Manager's active context window flat across long projects.

---

## 1. Externalize State to Disk (`.agy-orch/STATE.md`)

Instead of relying on long chat history to remember completed tasks, architecture decisions, and worker `conversation_id`s:

1. After each completed milestone (or every 4–5 tasks), instruct **`@worker-glm`** (`opencode/glm-5.3-flash-free` — 0 cost) to update `.agy-orch/STATE.md` with:
   - **Completed Milestones & Files**
   - **Active Worker `conversation_id`s**
   - **Pending Tasks & Next Immediate Step**
2. Keep `.agy-orch/STATE.md` under **60 lines** so it can be read in a single `read_file_excerpt` call whenever needed.

---

## 2. Automatic Compaction Checkpoints (`/compact`)

When any of the following conditions is met:
- **6–8 Manager turns** have elapsed in the current session, OR
- A major feature phase has just passed verification and been committed to Git:

**Action**:
1. Have `@worker-glm` save the latest checkpoint in `.agy-orch/STATE.md`.
2. Advise the user: *"Đã lưu trạng thái vào `.agy-orch/STATE.md`. Bạn có thể gõ `/compact` ngay bây giờ để giải phóng bộ nhớ hội thoại và tiết kiệm tối đa quota OpenAI cho bước tiếp theo."*

---

## 3. Offload Long Architecture Debates to `boss-argon`

When a task requires a lengthy architectural comparison or deep multi-file diff audit:
- Call MCP `argon_boss_review(review_type="plan" | "code_review")` or `ask_gemini_web_auto`.
- `boss-argon` runs on Antigravity CLI (`acc-1`) and automatically rotates across **3 Google Web accounts (`acc-1`, `acc-2`, `acc-3`)**, returning only a compact digest to your OpenAI session.
