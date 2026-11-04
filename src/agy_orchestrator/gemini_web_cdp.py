"""Automated browser bridge for Gemini Web (`https://gemini.google.com/app`) via Chrome DevTools Protocol (CDP)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import urllib.request

from agy_orchestrator.account_pool import AccountPoolManager, classify_quota_window_error

INPUT_SELECTORS = (
    'rich-textarea div[contenteditable="true"], '
    'div.ql-editor[contenteditable="true"], '
    'div[role="textbox"][contenteditable="true"], '
    'textarea[aria-label*="prompt" i]'
)

SEND_BUTTON_SELECTORS = (
    'button.send-button:not([disabled]), '
    'button[aria-label*="Send message" i]:not([disabled]), '
    'button[aria-label*="Send" i]:not([disabled]), '
    'button[aria-label*="Gửi" i]:not([disabled])'
)

STOP_BUTTON_SELECTORS = (
    'button[aria-label*="Stop" i], '
    'button[aria-label*="Dừng" i], '
    'button.stop-button'
)

RESPONSE_SELECTORS = (
    "model-response message-content, "
    "model-response .model-response-text, "
    "message-content.model-response-text, "
    "model-response"
)


def default_chrome_profile_dir() -> Path:
    """Return the persistent Chrome profile directory used for Gemini Web automation."""
    env_dir = os.environ.get("AGY_CHROME_PROFILE_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return (Path.home() / ".agy-orch" / "chrome_profile").resolve()


def resolve_browser_binary(explicit_path: str | Path | None = None) -> Path:
    """Locate Google Chrome or Microsoft Edge on the host machine."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())

    env_bin = os.environ.get("AGY_CHROME_BIN")
    if env_bin:
        candidates.append(Path(env_bin).expanduser())

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
                Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ]
        )
    else:
        for name in ("google-chrome", "chromium", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "Could not locate Google Chrome or Microsoft Edge. Set AGY_CHROME_BIN to your browser executable path."
    )


def is_cdp_port_open(cdp_port: int = 9222) -> bool:
    """Return True if a browser is already listening on `http://127.0.0.1:{cdp_port}`."""
    url = f"http://127.0.0.1:{cdp_port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return bool(data.get("webSocketDebuggerUrl"))
    except Exception:
        return False


async def ensure_cdp_browser_running(
    cdp_port: int = 9222,
    profile_dir: Path | None = None,
    start_url: str = "https://gemini.google.com/app",
) -> str:
    """Ensure a Chrome/Edge window with `--remote-debugging-port` is running and return its endpoint URL."""
    endpoint = f"http://127.0.0.1:{cdp_port}"
    if is_cdp_port_open(cdp_port):
        return endpoint

    browser_bin = resolve_browser_binary()
    user_data_dir = (profile_dir or default_chrome_profile_dir()).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(browser_bin),
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    for _ in range(25):
        await asyncio.sleep(0.4)
        if is_cdp_port_open(cdp_port):
            return endpoint

    raise RuntimeError(
        f"Launched {browser_bin.name} but CDP port {cdp_port} did not become ready within 10 seconds."
    )


async def _query_single_cdp_instance(
    prompt: str,
    cdp_port: int,
    profile_dir: Path | None,
    account_id: str,
    timeout_sec: int = 180,
    wait_for_login_sec: int = 45,
    web_url: str = "https://gemini.google.com/app",
) -> dict[str, Any]:
    """Drive a single Chrome CDP profile/port to query Gemini Web."""
    from playwright.async_api import async_playwright

    started = time.perf_counter()
    endpoint = await ensure_cdp_browser_running(
        cdp_port=cdp_port,
        profile_dir=profile_dir,
        start_url=web_url,
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(endpoint)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()

        page = None
        for existing_page in context.pages:
            if "gemini.google.com" in (existing_page.url or ""):
                page = existing_page
                break

        if page is None:
            page = await context.new_page()
            await page.goto(web_url, wait_until="domcontentloaded", timeout=30000)
        else:
            await page.bring_to_front()

        # Wait for the chat input box (allowing the user time to complete sign-in if first run)
        input_box = page.locator(INPUT_SELECTORS).first
        try:
            await input_box.wait_for(state="visible", timeout=max(5, wait_for_login_sec) * 1000)
        except Exception:
            return {
                "status": "LOGIN_REQUIRED",
                "account_id": account_id,
                "cdp_port": cdp_port,
                "model_used": "gemini-web-pro",
                "web_url": page.url,
                "worker_summary": (
                    f"Cửa sổ Chrome Gemini Web cho tài khoản `{account_id}` (port {cdp_port}) đã mở nhưng chưa đăng nhập. "
                    "Bạn chỉ cần đăng nhập 1 lần duy nhất trên cửa sổ Chrome đó (session được lưu vĩnh viễn), "
                    "sau đó chạy lại lệnh là hệ thống sẽ tự động nhập và lấy kết quả 100%."
                ),
            }

        read_dom_state_js = """() => {
            const nodes = document.querySelectorAll(
                'model-response message-content, model-response .model-response-text, message-content, model-response, .markdown'
            );
            const count = nodes.length;
            const lastText = count > 0 ? (nodes[count - 1].innerText || '').trim() : '';
            const stopBtn = document.querySelector(
                'button[aria-label*="Stop" i], button[aria-label*="Dừng" i], button.stop-button'
            );
            const isGenerating = !!(stopBtn && stopBtn.offsetParent !== null);
            return { count, lastText, isGenerating };
        }"""

        init_state = await page.evaluate(read_dom_state_js)
        initial_count = int(init_state.get("count", 0))
        initial_last_text = str(init_state.get("lastText", ""))

        # Dismiss any first-time welcome modal / Angular CDK overlay backdrop if present
        try:
            await page.evaluate(
                """() => {
                    const modalBtns = document.querySelectorAll('.cdk-overlay-container button');
                    modalBtns.forEach(b => { try { b.click(); } catch (e) {} });
                    const backdrops = document.querySelectorAll('.cdk-overlay-backdrop-showing, .cdk-overlay-pane');
                    backdrops.forEach(el => { try { el.remove(); } catch (e) {} });
                }"""
            )
            await asyncio.sleep(0.3)
        except Exception:
            pass

        # Fill the contenteditable rich-textarea directly (fires input/change events in Angular)
        try:
            await input_box.fill(prompt, timeout=8000)
        except Exception:
            await input_box.click(force=True)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.insert_text(prompt)

        await asyncio.sleep(0.6)

        # Click Send button via JS or press Enter
        sent = await page.evaluate(
            """() => {
                const btn = document.querySelector(
                    'button.send-button:not([disabled]), button[aria-label*="Send" i]:not([disabled]), button[aria-label*="Gửi" i]:not([disabled])'
                );
                if (btn) { btn.click(); return true; }
                return false;
            }"""
        )
        if not sent:
            await input_box.press("Enter")

        # Poll for the new model response to appear and finish streaming
        deadline = time.perf_counter() + max(15, timeout_sec)
        last_text = ""
        stable_ticks = 0

        while time.perf_counter() < deadline:
            await asyncio.sleep(1.0)
            try:
                cur_state = await page.evaluate(read_dom_state_js)
            except Exception:
                continue

            current_count = int(cur_state.get("count", 0))
            current_text = str(cur_state.get("lastText", ""))
            is_generating = bool(cur_state.get("isGenerating", False))

            if current_count == 0 or not current_text:
                continue
            if current_count <= initial_count and current_text == initial_last_text:
                continue

            if current_text == last_text and not is_generating:
                stable_ticks += 1
                if stable_ticks >= 3:
                    elapsed = round(time.perf_counter() - started, 3)
                    return {
                        "status": "SUCCESS",
                        "source": "gemini_web_cdp_automation",
                        "account_id": account_id,
                        "cdp_port": cdp_port,
                        "model_used": "gemini-web-pro",
                        "duration_seconds": elapsed,
                        "worker_summary": current_text,
                        "antigravity_usage": {"total_tokens": max(100, (len(prompt) + len(current_text)) // 4)},
                    }
            else:
                last_text = current_text
                stable_ticks = 0

        elapsed = round(time.perf_counter() - started, 3)
        if last_text:
            return {
                "status": "SUCCESS",
                "source": "gemini_web_cdp_automation_partial",
                "account_id": account_id,
                "cdp_port": cdp_port,
                "model_used": "gemini-web-pro",
                "duration_seconds": elapsed,
                "worker_summary": last_text,
                "antigravity_usage": {"total_tokens": max(100, (len(prompt) + len(last_text)) // 4)},
            }

        return {
            "status": "TIMEOUT",
            "source": "gemini_web_cdp_automation",
            "account_id": account_id,
            "cdp_port": cdp_port,
            "model_used": "gemini-web-pro",
            "duration_seconds": elapsed,
            "worker_summary": f"Timed out after {timeout_sec}s waiting for Gemini Web response.",
        }


async def query_gemini_web_cdp(
    prompt: str,
    cdp_port: int | None = None,
    timeout_sec: int = 180,
    wait_for_login_sec: int = 45,
    web_url: str = "https://gemini.google.com/app",
    pool: AccountPoolManager | None = None,
) -> dict[str, Any]:
    """Automatically input `prompt` into Gemini Web and auto-swap accounts if 5h/weekly quota is hit."""
    pool_mgr = pool or AccountPoolManager()
    attempted_accounts: list[str] = []
    max_attempts = max(1, len(pool_mgr.accounts))

    for attempt in range(max_attempts):
        active_acc = pool_mgr.get_active_web_account()
        if cdp_port is not None and attempt == 0:
            for cand in pool_mgr.accounts.values():
                if cand.cdp_port == cdp_port:
                    active_acc = cand
                    break
        attempted_accounts.append(active_acc.account_id)
        port_to_use = cdp_port if (cdp_port is not None and attempt == 0) else active_acc.cdp_port
        prof_dir = Path(active_acc.chrome_profile_dir) if active_acc.chrome_profile_dir else None

        res = await _query_single_cdp_instance(
            prompt=prompt,
            cdp_port=port_to_use,
            profile_dir=prof_dir,
            account_id=active_acc.account_id,
            timeout_sec=timeout_sec,
            wait_for_login_sec=wait_for_login_sec,
            web_url=web_url,
        )

        est_tokens = int((res.get("antigravity_usage") or {}).get("total_tokens", 0))
        if est_tokens > 0:
            pool_mgr.record_usage(
                tokens=est_tokens,
                model="gemini-web-pro",
                account_id=active_acc.account_id,
            )

        quota_window = classify_quota_window_error(str(res.get("worker_summary", "")))
        if res.get("status") == "SUCCESS" and quota_window is None:
            if attempt > 0:
                res["account_swapped"] = True
                res["attempted_accounts"] = attempted_accounts
            return res

        # If quota error on web or login required and we have more accounts in pool, rotate web account!
        if quota_window is not None:
            pool_mgr.mark_account_exhausted(
                window_type=quota_window,
                account_id=active_acc.account_id,
                channel="web",
            )
        if attempt + 1 < max_attempts:
            pool_mgr.swap_web_account(reason=f"web_{quota_window or res.get('status', 'fail')}")
        else:
            res["attempted_accounts"] = attempted_accounts
            return res

    return res

