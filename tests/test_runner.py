"""Unit tests for the headless Antigravity worker runner and quota ledger."""

import json
from pathlib import Path

from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.runner import (
    AntigravityWorkerRunner,
    WorkerRunResult,
    WorkerTaskSpec,
    build_worker_prompt,
    parse_agy_json_stdout,
    truncate_for_manager,
)


def test_parse_agy_json_stdout_handles_leading_mode_warning() -> None:
    sample = (
        "warning: --mode auto is not available for this account; continuing in the default mode.\n"
        '{"conversation_id":"conv-123","status":"SUCCESS","response":"Done!","duration_seconds":2.5,'
        '"num_turns":1,"usage":{"total_tokens":9500}}'
    )
    parsed, warnings = parse_agy_json_stdout(sample)
    assert parsed["conversation_id"] == "conv-123"
    assert parsed["status"] == "SUCCESS"
    assert parsed["usage"]["total_tokens"] == 9500
    assert len(warnings) == 1
    assert "--mode auto" in warnings[0]


def test_truncate_for_manager_bounds_long_worker_responses(tmp_path: Path) -> None:
    huge_text = "A" * 5000
    full_file = tmp_path / "run_01.md"
    short_text, was_truncated = truncate_for_manager(huge_text, max_chars=800, full_output_path=full_file)
    assert was_truncated is True
    assert len(short_text) < 900
    assert "truncated" in short_text


def test_build_worker_prompt_includes_deliverables_and_concise_rules() -> None:
    prompt = build_worker_prompt(
        "Implement user login helper",
        output_files=["src/auth.py", "tests/test_auth.py"],
        concise_report=True,
    )
    assert "`src/auth.py`" in prompt
    assert "[REPORTING RULES FOR MANAGER]" in prompt


def test_build_cli_args_and_quota_ledger(tmp_path: Path) -> None:
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("echo", encoding="utf-8")

    settings = OrchestratorSettings(agy_bin=fake_agy, default_workspace=tmp_path)
    runner = AntigravityWorkerRunner(settings=settings)

    spec = WorkerTaskSpec(
        prompt="Fix typo",
        workspace_dir=tmp_path,
        tier="trivial",
        conversation_id="abc-999",
        effort="low",
    )
    args = runner.build_cli_args(spec, resolved_model="gemini-3.8-flash-low", wrapped_prompt="wrapped")
    assert "--model" in args
    assert "gemini-3.8-flash-low" in args
    assert "--conversation" in args
    assert "abc-999" in args
    assert "--mode" in args
    assert "accept-edits" in args

    # Record two synthetic runs in the ledger and verify offloading ratio calculation
    sample_run = WorkerRunResult(
        run_id="run_test_1",
        status="SUCCESS",
        conversation_id="abc-999",
        model_used="gemini-3.8-flash-low",
        tier="trivial",
        workspace_dir=str(tmp_path),
        duration_seconds=3.1,
        num_turns=2,
        antigravity_usage={"input_tokens": 14000, "output_tokens": 500, "total_tokens": 14500},
        estimated_manager_tokens=120,
        git_changes_summary="M src/app.py",
        worker_summary="Updated src/app.py",
    )
    runner._append_ledger(tmp_path, sample_run)

    stats = runner.get_quota_stats(tmp_path)
    assert stats["total_runs"] == 1
    assert stats["antigravity_total_tokens"] == 14500
    assert stats["estimated_manager_tokens"] == 120
    assert stats["offload_percentage"] > 99.0
    assert stats["by_model"]["gemini-3.8-flash-low"]["runs"] == 1
    assert json.dumps(sample_run.to_compact_dict())
