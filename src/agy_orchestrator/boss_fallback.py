"""Quota-aware fallback chain and Gemini Web UI handoff for Google One AI Pro accounts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Literal
import webbrowser

from agy_orchestrator.runner import AntigravityWorkerRunner, WorkerRunResult, WorkerTaskSpec

QUOTA_ERROR_KEYWORDS: tuple[str, ...] = (
    "quota",
    "rate limit",
    "ratelimit",
    "resource_exhausted",
    "resource exhausted",
    "429",
    "too many requests",
    "weekly limit",
    "limit reached",
    "limit exceeded",
    "exhausted",
    "not available for this account",
)

ARGON_FAMILY_CHAIN: tuple[str, ...] = (
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
    "gemini-4-argon-low",
)

SECONDARY_BOSS_CHAIN: tuple[str, ...] = (
    "claude-opus-5-5-high",
    "claude-sonnet-5-5-high",
    "gemini-3.8-flash-high",
)


def is_quota_exhausted(status: str, response_text: str, warnings: list[str] | None = None) -> bool:
    """Detect whether an Antigravity CLI run failed or degraded due to weekly/rate quota limits."""
    combined = (response_text + "\n" + "\n".join(warnings or [])).lower()
    has_keyword = any(kw in combined for kw in QUOTA_ERROR_KEYWORDS)

    if status.upper() != "SUCCESS":
        return True
    if has_keyword and len(response_text.strip()) < 450:
        return True
    return False


def get_boss_fallback_chain(
    primary_model: str = "gemini-4-argon-high",
    include_secondary_models: bool = True,
) -> list[str]:
    """Return the ordered list of Boss models to try in `agy`."""
    env_chain = os.environ.get("AGY_BOSS_FALLBACK_MODELS", "").strip()
    if env_chain:
        base = [m.strip() for m in env_chain.split(",") if m.strip()]
    else:
        base = list(ARGON_FAMILY_CHAIN)
        if include_secondary_models:
            base.extend(SECONDARY_BOSS_CHAIN)

    ordered: list[str] = [primary_model]
    for item in base:
        if item not in ordered:
            ordered.append(item)
    return ordered


async def prepare_web_handoff_bundle(
    prompt: str,
    workspace: Path,
    copy_clipboard: bool = True,
    open_browser: bool = False,
    web_url: str = "https://gemini.google.com/app",
) -> dict[str, Any]:
    """Save the Boss review prompt + diff to a Markdown file, copy to Clipboard, and open Gemini Web."""
    handoff_dir = workspace / ".agy-orch" / "web_handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    latest_file = handoff_dir / "latest_boss_prompt.md"
    archive_file = handoff_dir / f"boss_prompt_{stamp}.md"

    latest_file.write_text(prompt, encoding="utf-8")
    archive_file.write_text(prompt, encoding="utf-8")

    copied = False
    if copy_clipboard and os.name == "nt":
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(input=prompt.encode("utf-8")), timeout=8)
            copied = proc.returncode == 0
        except Exception:
            copied = False

    browser_opened = False
    if open_browser:
        try:
            browser_opened = bool(webbrowser.open(web_url))
        except Exception:
            browser_opened = False

    return {
        "status": "WEB_HANDOFF_READY",
        "handoff_file": str(latest_file),
        "copied_to_clipboard": copied,
        "browser_opened": browser_opened,
        "web_url": web_url,
        "instructions": (
            "Đã hết quota Argon tuần trên Antigravity. Toàn bộ nội dung cần duyệt + git diff đã được "
            f"copy sẵn vào Clipboard và mở trang {web_url} (tài khoản Google Pro của bạn). "
            "Bạn chỉ cần nhấn Ctrl+V trên Gemini Web rồi dán kết quả VERDICT vào lại cuộc hội thoại."
        ),
    }


async def run_boss_with_fallback(
    runner: AntigravityWorkerRunner,
    boss_prompt: str,
    workspace: Path,
    primary_model: str = "gemini-4-argon-high",
    on_argon_exhausted: Literal["web_handoff", "secondary_models"] = "web_handoff",
    conversation_id: str | None = None,
    timeout_sec: int = 300,
    open_browser_on_exhaustion: bool = True,
) -> dict[str, Any]:
    """Execute a Boss review/planning prompt tailored for a Google One AI Pro account:

    1. Try `gemini-4-argon-high` -> `gemini-4-argon-medium` -> `gemini-4-argon-low` in `agy`.
    2. When Argon weekly quota is exhausted:
       - If `on_argon_exhausted="web_handoff"` (default): Immediately copy the prompt + diff
         to the Windows Clipboard and open `https://gemini.google.com/app` so the user can
         use their Google Pro Web quota with a single `Ctrl+V`.
       - If `on_argon_exhausted="secondary_models"`: Automatically continue with
         `claude-opus-5-5-high` -> `claude-sonnet-5-5-high` -> `gemini-3.8-flash-high` in `agy`
         before falling back to Web Handoff.
    """
    attempted_models: list[str] = []
    include_secondary = on_argon_exhausted == "secondary_models"
    chain = get_boss_fallback_chain(
        primary_model=primary_model,
        include_secondary_models=include_secondary,
    )

    for idx, candidate_model in enumerate(chain):
        attempted_models.append(candidate_model)
        spec = WorkerTaskSpec(
            prompt=boss_prompt,
            workspace_dir=workspace,
            tier="complex",
            model=candidate_model,
            mode="plan",
            conversation_id=conversation_id if idx == 0 else None,
            concise_report=False,
            timeout_sec=timeout_sec,
            max_summary_chars=3200,
        )
        res: WorkerRunResult = await runner.run_task(spec)
        if not is_quota_exhausted(res.status, res.worker_summary, res.warnings):
            payload = res.to_compact_dict()
            if idx > 0:
                payload["fallback_activated"] = True
                payload["attempted_models"] = attempted_models
            return payload

    handoff = await prepare_web_handoff_bundle(
        prompt=boss_prompt,
        workspace=workspace,
        copy_clipboard=True,
        open_browser=open_browser_on_exhaustion,
    )
    handoff["attempted_models"] = attempted_models
    return handoff
