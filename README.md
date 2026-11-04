# agy-orchestrator

**Cầu nối MCP Server + CLI giúp ChatGPT Desktop / Codex CLI điều khiển các mô hình trong Google Antigravity (`agy`) theo kiến trúc Manager – Worker (Quản lý – Thợ thực thi).**

Mục tiêu cốt lõi:
- **ChatGPT / Codex (Manager & Reviewer)**: Chỉ lập kế hoạch, chia việc, chọn độ khó (`tier`) hoặc chọn model cụ thể, rồi đọc báo cáo tóm tắt + `git diff` + kết quả kiểm thử để duyệt bài/code (**tiết kiệm tối đa message quota & context window của ChatGPT**).
- **Google Antigravity CLI (`agy` Workers)**: Gánh toàn bộ phần việc ngốn nhiều token như đọc codebase lớn, viết code nhiều file, soạn tài liệu/bài viết dài, sửa format, refactor — sử dụng trực tiếp tài khoản và danh sách model sẵn có trong gói Antigravity của bạn.

---

## Kiến trúc hoạt động

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                 ChatGPT Desktop / OpenAI Codex CLI                       │
│         (Vai trò: Lập kế hoạch • Giao việc • Kiểm tra • Nghiệm thu)      │
└───────────────────┬──────────────────────────────────▲───────────────────┘
                    │ Lệnh ngắn gọn (~100 token)       │ Tóm tắt + git diff +
                    │ + chọn tier / model              │ conversation_id (~250 token)
                    ▼                                  │
┌──────────────────────────────────────────────────────────────────────────┐
│                  agy-orchestrator (MCP Server & CLI)                     │
│  • Smart Tier Router  • Parallel Batch Runner  • Low-Token Inspector     │
└───────────────────┬──────────────────────────────────▲───────────────────┘
                    │ agy --model <id> --mode accept-edits --output-format json -p ...
                    ▼                                  │
┌──────────────────────────────────────────────────────────────────────────┐
│                  Google Antigravity CLI (`agy` Workers)                  │
│  • trivial  ──> gemini-3.8-flash-low     (format, dịch, đọc file lớn)    │
│  • writing  ──> gemini-3.8-flash-high    (viết bài, README, tài liệu)    │
│  • coding   ──> gemini-4-argon-medium    (viết tính năng, unit test)     │
│  • complex  ──> gemini-4-argon-high      (debug khó, thuật toán phức tạp)│
│  (Hoặc chỉ định trực tiếp Claude Sonnet / Claude Opus / GPT-OSS...)      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Tính năng nổi bật

1. **Tự động định tuyến mô hình theo độ khó (`tier`) hoặc chọn đích danh (`model`)**:
   - Tự động quét danh sách model thực tế trong tài khoản `agy models` của bạn.
   - Hỗ trợ tên viết tắt thông minh (ví dụ: `flash-low`, `sonnet-medium`, `opus-high`).
2. **Bảo vệ tuyệt đối Quota của ChatGPT**:
   - Worker ghi trực tiếp code/bài viết xuống đĩa (`--mode accept-edits`).
   - Kết quả trả về cho ChatGPT luôn được giới hạn gọn nhẹ (`max_summary_chars`), lưu bản đầy đủ vào `.agy-orch/runs/<run_id>.md` nếu cần tra cứu.
3. **Tiếp tục phiên làm việc của Worker (`continue_worker_session`)**:
   - Tận dụng `--conversation <id>` của `agy` để yêu cầu Worker sửa lại bài/code ngay trên ngữ cảnh đã đọc (`cache_read_tokens`), ChatGPT không cần gửi lại nội dung cũ.
4. **Chạy song song nhiều Worker (`delegate_parallel_tasks`)**:
   - Giao đồng thời nhiều đầu việc độc lập (ví dụ: 1 worker code backend, 1 worker viết test, 1 worker viết tài liệu tiếng Việt).
5. **Bộ công cụ Kiểm tra ít tốn token cho Manager**:
   - `inspect_workspace_changes`: Xem `git status`, `git diff --stat` và bản diff thu gọn (hoặc danh sách file mới sửa nếu thư mục chưa có Git).
   - `read_file_excerpt`: Đọc trích đoạn theo số dòng (`start_line`..`end_line`) để kiểm tra xác suất.
   - `run_verification_check`: Chạy lệnh test/lint (`pytest`, `npm test`, `ruff`...) và chỉ lấy tóm tắt kết quả.
   - `get_quota_savings_report`: Thống kê tổng số token đã đẩy sang Antigravity và tỷ lệ tiết kiệm quota cho ChatGPT.

---

## Yêu cầu hệ thống

- **Python** >= 3.11
- **Google Antigravity CLI (`agy`)** đã cài đặt và đăng nhập ít nhất 1 lần:
  - Windows PowerShell: `irm https://antigravity.google/cli/install.ps1 | iex`
  - macOS / Linux: `curl -fsSL https://antigravity.google/cli/install.sh | bash`
  - Kiểm tra: `agy models`

---

## Cài đặt nhanh

```powershell
git clone https://github.com/AIVIETNAM-AIO-Koka/agy-orchestrator.git
cd agy-orchestrator

python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
```

### Tích hợp 1 lệnh vào ChatGPT Desktop / Codex CLI

Xem trước cấu hình sẽ thêm vào `~/.codex/config.toml` (không ghi file):

```powershell
.\.venv\Scripts\agy-orch.exe install-codex --dry-run
```

Cài đặt chính thức (tự động tạo bản sao lưu `config.toml.<timestamp>.bak` và cài luôn skill `antigravity-manager` vào `~/.codex/skills/`):

```powershell
.\.venv\Scripts\agy-orch.exe install-codex
```

Sau khi chạy xong, **khởi động lại ChatGPT Desktop / Codex** là có thể ra lệnh ngay!

---

## Cách sử dụng

### 1. Dùng trực tiếp trong ChatGPT Desktop / Codex

Chỉ cần nhắn tự nhiên trong khung chat:

> *"Dùng `antigravity_workers` để làm dự án tại `D:\Projects\my-app`: bạn chỉ đóng vai Quản lý lập kế hoạch và kiểm tra bằng `inspect_workspace_changes` + `run_verification_check`, còn việc viết code và viết tài liệu hãy giao hết cho các model Flash/Argon trong Antigravity."*

### 2. Dùng trực tiếp qua dòng lệnh (`agy-orch`)

```powershell
# Xem danh sách model Antigravity hiện có và bảng phân bổ theo tier
.\.venv\Scripts\agy-orch.exe models

# Giao việc viết bài / tài liệu cho model nhẹ (tier=writing -> Flash High)
.\.venv\Scripts\agy-orch.exe delegate "Viết bài hướng dẫn REST API chi tiết bằng tiếng Việt" --tier writing --output-file docs/rest_guide.md

# Giao việc code tính năng (tier=coding -> Argon Medium / Sonnet Medium)
.\.venv\Scripts\agy-orch.exe delegate "Viết module tính giỏ hàng và unit test" --tier coding --workspace D:\Projects\shop

# Yêu cầu Worker sửa lại trên cùng phiên hội thoại vừa chạy
.\.venv\Scripts\agy-orch.exe resume <conversation_id> "Bổ sung xử lý mã giảm giá hết hạn"

# Xem thống kê số token Antigravity đã gánh thay cho ChatGPT
.\.venv\Scripts\agy-orch.exe stats
```

---

## Danh sách MCP Tools cung cấp cho ChatGPT

| Tool | Chức năng |
| :--- | :--- |
| `list_worker_models` | Liệt kê toàn bộ model trong tài khoản `agy` và gợi ý model mặc định theo từng `tier`. |
| `delegate_task` | Giao 1 đầu việc code/viết/tóm tắt cho Antigravity Worker; trả về báo cáo nén + `conversation_id`. |
| `continue_worker_session` | Gửi nhận xét/yêu cầu sửa lỗi vào phiên `conversation_id` sẵn có của Worker. |
| `delegate_parallel_tasks` | Chạy song song nhiều Worker cho các đầu việc độc lập. |
| `inspect_workspace_changes` | Kiểm tra `git status` + `git diff` giới hạn ký tự (hoặc danh sách file mới sửa). |
| `read_file_excerpt` | Đọc một đoạn dòng chỉ định của file để nghiệm thu nhanh. |
| `run_verification_check` | Chạy lệnh kiểm thử/build và nhận kết quả Pass/Fail gọn nhẹ. |
| `get_quota_savings_report` | Báo cáo lượng token Antigravity đã thực thi và tỷ lệ tiết kiệm quota cho Manager. |

---

## Chạy bộ kiểm thử (Tests)

```powershell
.\.venv\Scripts\pytest.exe -v
```

## Giấy phép

Phát hành theo giấy phép [MIT](LICENSE).
