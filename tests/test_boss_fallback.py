"""Unit tests for Gemini 4 Argon Boss quota detection, fallback chain, and web handoff."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agy_orchestrator.boss_fallback import (
    get_boss_fallback_chain,
    is_quota_exhausted,
    prepare_web_handoff_bundle,
    run_boss_with_fallback,
)
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.runner import AntigravityWorkerRunner, WorkerRunResult


def test_is_quota_exhausted_detects_weekly_limit_and_429() -> None:
    assert is_quota_exhausted("ERROR", "Resource exhausted: weekly quota limit reached", []) is True
    assert is_quota_exhausted("SUCCESS", "Error 429: Rate limit exceeded for gemini-4-argon-high", []) is True
    assert is_quota_exhausted("SUCCESS", "VERDICT: APPROVED\nAll checks passed.", []) is False


def test_get_boss_fallback_chain_starts_with_primary_and_deduplicates() -> None:
    chain = get_boss_fallback_chain("gemini-4-argon-high")
    assert chain[0] == "gemini-4-argon-high"
    assert "gemini-4-argon-medium" in chain
    assert "claude-opus-5-5-high" in chain


@pytest.mark.asyncio
async def test_run_boss_with_fallback_switches_when_argon_high_hits_quota(tmp_path: Path) -> None:
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("echo", encoding="utf-8")
    settings = OrchestratorSettings(agy_bin=fake_agy, default_workspace=tmp_path)
    runner = AntigravityWorkerRunner(settings=settings)

    quota_fail = WorkerRunResult(
        run_id="r1",
        status="ERROR",
        conversation_id=None,
        model_used="gemini-4-argon-high",
        tier="complex",
        workspace_dir=str(tmp_path),
        duration_seconds=1.0,
        num_turns=0,
        antigravity_usage={},
        estimated_manager_tokens=20,
        git_changes_summary="",
        worker_summary="Weekly quota exhausted for gemini-4-argon-high",
    )
    fallback_ok = WorkerRunResult(
        run_id="r2",
        status="SUCCESS",
        conversation_id="conv-fallback",
        model_used="gemini-4-argon-medium",
        tier="complex",
        workspace_dir=str(tmp_path),
        duration_seconds=2.5,
        num_turns=1,
        antigravity_usage={"total_tokens": 5000},
        estimated_manager_tokens=80,
        git_changes_summary="",
        worker_summary="VERDICT: APPROVED\nNo findings.",
    )

    runner.run_task = AsyncMock(side_effect=[quota_fail, fallback_ok])  # type: ignore[method-assign]

    res = await run_boss_with_fallback(
        runner=runner,
        boss_prompt="Review diff",
        workspace=tmp_path,
        primary_model="gemini-4-argon-high",
    )
    assert res["status"] == "SUCCESS"
    assert res["model_used"] == "gemini-4-argon-medium"
    assert res["fallback_activated"] is True
    assert res["attempted_models"] == ["gemini-4-argon-high", "gemini-4-argon-medium"]


@pytest.mark.asyncio
async def test_prepare_web_handoff_bundle_writes_markdown_file(tmp_path: Path) -> None:
    bundle = await prepare_web_handoff_bundle(
        prompt="# Boss Review Request\nDiff here",
        workspace=tmp_path,
        copy_clipboard=False,
        open_browser=False,
    )
    assert bundle["status"] == "WEB_HANDOFF_READY"
    saved = Path(bundle["handoff_file"])
    assert saved.is_file()
    assert "Boss Review Request" in saved.read_text(encoding="utf-8")
