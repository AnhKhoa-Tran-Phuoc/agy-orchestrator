---
name: quota-monitor
description: "Display a real-time per-model 5-hour and weekly quota dashboard across Antigravity CLI models (`gemini-4-argon-*`, `gemini-3.8-flash-*`), Gemini Web (`gemini-web-pro`), Google accounts (`acc-1..acc-3`), and OpenCode models."
---

# Per-Model & Multi-Account Quota Monitor (`quota-monitor`)

When the user invokes `/quota-monitor` or asks to check model/account quotas:

1. Immediately call the MCP tool **`get_per_model_quota_report()`**.
2. Output the exact `formatted_dashboard` code block returned by the tool inside a ```text fenced block so the user sees the visual progress bars (`[########--]`) for every model and every Google account.
3. Below the table, add a 2-bullet summary highlighting:
   - Which account (`acc-1`, `acc-2`, `acc-3`) is currently active for **CLI Flash** and **Web Argon**.
   - Any model or account that has `<25%` remaining 5-hour or weekly quota (or confirm that all models are healthy).

