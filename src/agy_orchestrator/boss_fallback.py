"""Quota-aware fallback chain and Web/AI-Studio handoff for the Gemini 4 Argon Boss."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any
import webbrowser

import httpx

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

DEFAULT_BOSS_FALLBACK_CHAIN: tuple[str, ...] = (
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
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


def get_boss_fallback_chain(primary_model: str = "gemini-4-argon-high") -> list[str]:
    """Return the ordered list of Boss models to try in `agy` before falling back to Web."""
    env_chain = os.environ.get("AGY_BOSS_FALLBACK_MODELS", "").strip()
    if env_chain:
        custom = [m.strip() for m in env_chain.split(",") if m.strip()]
    else:
        custom = list(DEFAULT_BOSS_FALLBACK_CHAIN)

    ordered: list[str] = [primary_model]
    for item in custom:
        if item not in ordered:
            ordered.append(item)
    return ordered


async def call_google_ai_studio_web_api(
    prompt: str,
    api_key: str | None = None,
    model: str | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any] | None:
    """Call Google AI Studio / Gemini Web REST API (`GEMINI_API_KEY` / `GOOGLE_API_KEY`) if configured."""
    resolved_key = (
        api_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not resolved_key:
        return None

    target_model = (model or os.environ.get("GEMINI_WEB_API_MODEL") or "gemini-2.5-pro").strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"

    payload = {
        "contents": [{" role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    # Fix role key whitespace
    payload["contents"] = [{"role": "user", "parts": [{"text": prompt}]}]

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(
            url,
            headers={"x-goog-api-key": resolved_key, "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    candidates = data.get("candidates") or []
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    text = "\n".join(str(p.get("text", "")) for p in parts if p.get("text")).strip()
    usage_meta = data.get("usageMetadata") or {}

    return {
        "status": "SUCCESS",
        "source": "google_ai_studio_web_api",
        "model_used": f"web-api/{target_model}",
        "worker_summary": text,
        "antigravity_usage": {
            "input_tokens": int(usage_meta.get("promptTokenCount", 0)),
            "output_tokens": int(usage_meta.get("candidatesTokenCount", 0)),
            "total_tokens": int(usage_meta.get("totalTokenCount", 0)),
        },
    }


async def prepare_web_handoff_bundle(
    prompt: str,
    workspace: Path,
    copy_clipboard: bool = True,
    open_browser: bool = False,
    web_url: str = "https://gemini.google.com/app",
) -> dict[str, Any]:
    """Save the Boss review prompt + diff to a Markdown file, copy to Clipboard, and optionally open Gemini Web."""
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
            "Prompt đã được lưu vào file và copy sẵn vào Clipboard. "
            f"Hãy mở {web_url}, nhấn Ctrl+V để dán cho Boss duyệt trên Web, "
            "sau đó dán kết quả VERDICT vào lại cuộc hội thoại."
        ),
    }


async def run_boss_with_fallback(
    runner: AntigravityWorkerRunner,
    boss_prompt: str,
    workspace: Path,
    primary_model: str = "gemini-4-argon-high",
    conversation_id: str | None = None,
    timeout_sec: int = 300,
    open_browser_on_exhaustion: bool = False,
) -> dict[str, Any]:
    """Execute a Boss review/planning prompt with automatic multi-tier fallback:

    1. Try `primary_model` (`gemini-4-argon-high`) in `agy`.
    2. If weekly quota/rate-limit is hit, try Google AI Studio Web API (`GEMINI_API_KEY`) if set.
    3. Otherwise iterate through fallback Boss models in `agy` (`gemini-4-argon-medium`,
       `claude-opus-5-5-high`, `claude-sonnet-5-5-high`, `gemini-3.8-flash-high`).
    4. If all CLI & API tiers are exhausted, generate a ready-to-paste Web Handoff bundle
       (copied to Clipboard + saved to `.agy-orch/web_handoff/latest_boss_prompt.md`).
    """
    attempted_models: list[str] = []
    chain = get_boss_fallback_chain(primary_model=primary_model)

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

        # After primary Argon model fails due to quota, try Google AI Studio Web API if configured
        if idx == 0:
            try:
                web_api_res = await call_google_ai_studio_web_api(
                    prompt=boss_prompt,
                    timeout_sec=min(timeout_sec, 120),
                )
                if web_api_res is not None:
                    web_api_res["fallback_activated"] = True
                    web_api_res["attempted_models"] = attempted_models
                    return web_api_res
            except Exception:
                pass

    # All automated tiers exhausted -> prepare Web Browser Handoff
    handoff = await prepare_web_handoff_bundle(
        prompt=boss_prompt,
        workspace=workspace,
        copy_clipboard=True,
        open_browser=open_browser_on_exhaustion,
    )
    handoff["attempted_models"] = attempted_models
    return handoff
