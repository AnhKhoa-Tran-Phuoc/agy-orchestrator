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


async def query_gemini_web_cdp(
    prompt: str,
    cdp_port: int = 9222,
    timeout_sec: int = 180,
    wait_for_login_sec: int = 45,
    web_url: str = "https://gemini.google.com/app",
) -> dict[str, Any]:
    """Automatically input `prompt` into Gemini Web (`gemini.google.com/app`) and extract the response."""
    from playwright.async_api import async_playwright

    started = time.perf_counter()
    endpoint = await ensure_cdp_browser_running(cdp_port=cdp_port, start_url=web_url)

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
                "model_used": "gemini-web-pro",
                "web_url": page.url,
                "worker_summary": (
                    "Cửa sổ Chrome Gemini Web đã mở nhưng chưa đăng nhập tài khoản Google Pro. "
                    "Bạn chỉ cần đăng nhập 1 lần duy nhất trên cửa sổ Chrome đó (session sẽ được lưu vĩnh viễn), "
                    "sau đó chạy lại lệnh là hệ thống sẽ tự động nhập và lấy kết quả 100%."
                ),
            }

        responses_loc = page.locator(RESPONSE_SELECTORS)
        initial_count = await responses_loc.count()
        initial_last_text = ""
        if initial_count > 0:
            try:
                initial_last_text = (await responses_loc.nth(initial_count - 1).inner_text()).strip()
            except Exception:
                initial_last_text = ""

        # Focus input box, clear any leftover draft, and insert the full prompt
        await input_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.insert_text(prompt)
        await asyncio.sleep(0.4)

        # Click Send button or press Enter
        send_btn = page.locator(SEND_BUTTON_SELECTORS).first
        if await send_btn.count() > 0 and await send_btn.is_visible():
            await send_btn.click()
        else:
            await page.keyboard.press("Enter")

        # Poll for the new model response to appear and finish streaming
        deadline = time.perf_counter() + max(15, timeout_sec)
        last_text = ""
        stable_ticks = 0

        while time.perf_counter() < deadline:
            await asyncio.sleep(1.0)
            current_count = await responses_loc.count()
            if current_count == 0:
                continue

            latest_node = responses_loc.nth(current_count - 1)
            try:
                current_text = (await latest_node.inner_text()).strip()
            except Exception:
                continue

            # Ensure we are looking at a newly generated response, not the old one
            if current_count == initial_count and current_text == initial_last_text:
                continue
            if not current_text:
                continue

            stop_btn = page.locator(STOP_BUTTON_SELECTORS).first
            is_generating = False
            try:
                is_generating = (await stop_btn.count() > 0) and (await stop_btn.is_visible())
            except Exception:
                is_generating = False

            if current_text == last_text and not is_generating:
                stable_ticks += 1
                if stable_ticks >= 3:
                    elapsed = round(time.perf_counter() - started, 3)
                    return {
                        "status": "SUCCESS",
                        "source": "gemini_web_cdp_automation",
                        "model_used": "gemini-web-pro",
                        "duration_seconds": elapsed,
                        "worker_summary": current_text,
                        "antigravity_usage": {"total_tokens": 0},
                    }
            else:
                last_text = current_text
                stable_ticks = 0

        elapsed = round(time.perf_counter() - started, 3)
        if last_text:
            return {
                "status": "SUCCESS",
                "source": "gemini_web_cdp_automation_partial",
                "model_used": "gemini-web-pro",
                "duration_seconds": elapsed,
                "worker_summary": last_text,
                "antigravity_usage": {"total_tokens": 0},
            }

        return {
            "status": "TIMEOUT",
            "source": "gemini_web_cdp_automation",
            "model_used": "gemini-web-pro",
            "duration_seconds": elapsed,
            "worker_summary": f"Timed out after {timeout_sec}s waiting for Gemini Web response.",
        }
