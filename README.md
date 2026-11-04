# agy-orchestrator

**Hệ thống điều phối Multi-Agent (Manager – Worker) cho cả OpenCode và ChatGPT Desktop / Codex CLI, kết hợp các mô hình trong Google Antigravity (`agy`) cùng dàn model Free trên OpenCode.**

Mục tiêu cốt lõi:
- **Tầng Sếp (Managers & Code Reviewers)**:
  - **Các model GPT (`openai/gpt-6-astra`, `openai/gpt-6.1-sol`)** và **Gemini 4 Argon (`gemini-4-argon-high`)**: Chỉ lập kế hoạch kiến trúc, chia việc, đọc `git diff` / kết quả kiểm thử và duyệt code (`edit: deny` — không tự sửa file để tiết kiệm tối đa quota).
- **Tầng Thợ thực thi (Workers)**:
  - **Gemini 3.8 Flash (`gemini-3.8-flash-high` / `medium` / `low` qua Antigravity CLI)**: Gánh các việc code nhiều file, refactor lớn, viết bài/tài liệu dài.
  - **Dàn Model Free trên OpenCode (`deepseek-v4.1-flash-free`, `glm-5.3-flash-free`, `mimo-v2.6-flash-free`, `nemotron-3.5-lightning-free`)**: Chia nhau viết code module, viết unit test, soạn tài liệu tiếng Việt/Anh, tạo UI/boilerplate và chạy lệnh kiểm thử miễn phí 100%.

---

## Kiến trúc Multi-Agent trên OpenCode

```text
╔══════════════════════════════════════════════════════════════════════════════╗
║                TẦNG SẾP (MANAGERS & CODE REVIEWERS) — Chọn bằng phím Tab     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  1. [boss-gpt]     (mode: primary | model: openai/gpt-6-astra)               ║
║  2. [boss-gpt-sol] (mode: primary | model: openai/gpt-6.1-sol)               ║
║     • Sếp Tổng GPT: Phân tích yêu cầu, giao việc cho Thợ, duyệt nghiệm thu   ║
║     • Khóa quyền tự sửa file (edit: deny) để bảo toàn tuyệt đối quota GPT    ║
║                                                                              ║
║  3. [boss-argon]   (mode: all     | core: gemini-4-argon-high qua MCP)       ║
║     • Sếp Kiến Trúc & Duyệt Code Khắt Khe bằng Gemini 4 Argon (High)         ║
║     • Dùng được độc lập (0 tốn quota GPT) hoặc được boss-gpt triệu tập       ║
║       để hội ý kiến trúc & duyệt code chéo (`argon_boss_review`)             ║
╚══════════════════════════════════════╤═══════════════════════════════════════╝
                                       │
              Ra lệnh giao việc (Subagent `@worker-*` & MCP Tools)
                                       │
                                       ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║              TẦNG THỢ THỰC THI (WORKERS) — Gemini 3.8 Flash + Free OpenCode  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  • [@worker-gemini-flash] (`gemini-3.8-flash-high/medium/low` qua MCP):      ║
║    Code tính năng nhiều file, refactor lớn, viết tài liệu dài xuống ổ cứng.  ║
║  • [@worker-deepseek]     (`opencode/deepseek-v4.1-flash-free` — FREE):      ║
║    Viết code Python/TS/JS, sửa bug, viết unit test.                          ║
║  • [@worker-glm]          (`opencode/glm-5.3-flash-free` — FREE):            ║
║    Viết README, tài liệu tiếng Việt/Anh, bài viết, docstring, dịch thuật.    ║
║  • [@worker-mimo]         (`opencode/mimo-v2.6-flash-free` — FREE):          ║
║    Dựng giao diện UI/Frontend, boilerplate, file cấu hình JSON/YAML/TOML.    ║
║  • [@worker-nemotron]     (`opencode/nemotron-3.5-lightning-free` — FREE):   ║
║    Trinh sát codebase siêu tốc, chạy lệnh pytest/linter và báo cáo lỗi.      ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Cài đặt nhanh

```powershell
git clone https://github.com/AIVIETNAM-AIO-Koka/agy-orchestrator.git
cd agy-orchestrator

python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

### 1. Cài đặt hệ thống Multi-Agent vào OpenCode (Global hoặc theo Project)

```powershell
# Cài đặt Global vào ~/.config/opencode (dùng được cho mọi dự án mở trong OpenCode)
.\.venv\Scripts\agy-orch.exe install-opencode

# Hoặc cài đặt riêng cho 1 thư mục dự án (<project>/opencode.json + .opencode/agents/)
.\.venv\Scripts\agy-orch.exe install-opencode --project --target-dir D:\Projects\my-app
```

Sau khi cài xong, nếu đang mở OpenCode CLI/Desktop, chỉ cần chạy `opencode reload` (hoặc bấm phím **`Tab`** trong OpenCode) để chuyển sang agent **`boss-gpt`**, **`boss-gpt-sol`** hoặc **`boss-argon`**.

### 2. Cài đặt vào ChatGPT Desktop / Codex CLI

```powershell
.\.venv\Scripts\agy-orch.exe install-codex
```

---

## Cách sử dụng trên OpenCode

1. **Chế độ Sếp Tổng GPT + Hội đồng duyệt code Gemini 4 Argon**:
   - Bấm `Tab` chọn agent **`boss-gpt`** (hoặc **`boss-gpt-sol`**).
   - Nhập yêu cầu:
     > *"Hãy thiết kế và xây dựng module quản lý đơn hàng kèm unit test và tài liệu hướng dẫn tiếng Việt. Hãy hội ý thiết kế và duyệt code cùng `argon_boss_review`, còn việc viết code và viết tài liệu hãy chia cho `@worker-gemini-flash`, `@worker-deepseek` và `@worker-glm`."*
2. **Chế độ Sếp Gemini 4 Argon (Không tốn 1 token GPT nào)**:
   - Bấm `Tab` chọn agent **`boss-argon`**.
   - Nhập yêu cầu: `boss-argon` sẽ tự gọi `gemini-4-argon-high` lập kế hoạch, chia việc cho dàn `@worker-*` Free + `gemini-3.8-flash-high`, rồi dùng `gemini-4-argon-high` chấm điểm và duyệt code (`VERDICT: APPROVED` / `CHANGES_REQUESTED`).

---

## Danh sách MCP Tools cung cấp bởi `antigravity_workers`

| Tool | Chức năng |
| :--- | :--- |
| `argon_boss_review` | Triệu tập **Gemini 4 Argon (`high`/`medium`)** ở chế độ `plan` để thiết kế kiến trúc (`review_type="plan"`) hoặc duyệt code nghiêm ngặt (`review_type="code_review"`). |
| `delegate_flash_worker` | Giao việc code/viết/refactor trực tiếp cho **Gemini 3.8 Flash (`high`/`medium`/`low`)** ở chế độ `accept-edits`. |
| `delegate_task` | Giao 1 đầu việc cho bất kỳ model nào trong Antigravity theo `tier` hoặc tên `model`. |
| `continue_worker_session` | Gửi nhận xét của Sếp vào phiên `conversation_id` sẵn có của Worker để sửa lỗi ngay trên ngữ cảnh cũ. |
| `delegate_parallel_tasks` | Chạy song song nhiều Worker cho các đầu việc độc lập. |
| `inspect_workspace_changes` | Xem `git status` + `git diff` thu gọn (hoặc danh sách file mới sửa). |
| `read_file_excerpt` | Đọc một đoạn dòng chỉ định (`start_line`..`end_line`) của file để nghiệm thu nhanh. |
| `run_verification_check` | Chạy lệnh kiểm thử/build (`pytest`, `npm test`, `ruff`...) và nhận kết quả Pass/Fail gọn nhẹ. |
| `get_quota_savings_report` | Báo cáo lượng token Antigravity đã thực thi và tỷ lệ tiết kiệm quota cho Sếp. |
| `list_worker_models` | Liệt kê toàn bộ model trong tài khoản `agy`. |

---

## Chạy bộ kiểm thử (Tests)

```powershell
.\.venv\Scripts\pytest.exe -v
```

## Giấy phép

Phát hành theo giấy phép [MIT](LICENSE).
