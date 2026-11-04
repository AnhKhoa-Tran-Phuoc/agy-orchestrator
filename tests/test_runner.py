"""Unit tests for the headless Antigravity worker runner, Reflexion loop, Circuit Breaker, and Git/State helpers."""

import json
from pathlib import Path
import pytest

from agy_orchestrator.account_pool import AccountPoolManager
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.models import ModelRegistry
from agy_orchestrator.runner import (
    AntigravityWorkerRunner,
    WorkerRunResult,
    WorkerTaskSpec,
    build_worker_prompt,
    init_or_sync_git_repo,
    load_project_state_digest,
    parse_agy_json_stdout,
    partition_non_overlapping_waves,
    rollback_to_git_checkpoint,
    save_project_state,
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


def test_state_md_persistence_and_auto_injection(tmp_path: Path) -> None:
    saved = save_project_state(tmp_path, "## Milestone 1\n- Auth module complete.")
    assert saved.is_file()
    digest = load_project_state_digest(tmp_path)
    assert "Auth module complete." in digest

    wrapped = build_worker_prompt(
        task_prompt="Add logout endpoint",
        output_files=["src/auth.py"],
        state_digest=digest,
    )
    assert "[PROJECT STATE MEMORY (.agy-orch/STATE.md)]" in wrapped
    assert "Auth module complete." in wrapped


def test_partition_non_overlapping_waves_prevents_file_collisions() -> None:
    specs = [
        WorkerTaskSpec(prompt="Task A", output_files=["src/auth.py", "src/common.py"]),
        WorkerTaskSpec(prompt="Task B", output_files=["src/ui.tsx"]),
        WorkerTaskSpec(prompt="Task C", output_files=["src/common.py", "tests/test_common.py"]),
    ]
    waves = partition_non_overlapping_waves(specs)
    assert len(waves) == 2
    assert [idx for idx, _, _ in waves[0]] == [0, 1]
    assert [idx for idx, _, _ in waves[1]] == [2]
    assert waves[1][0][2] == ["src/common.py"]


@pytest.mark.asyncio
async def test_worker_reflexion_loop_and_circuit_breaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_agy = tmp_path / "agy.cmd"
    fake_agy.write_text("@echo off\n", encoding="utf-8")
    pool = AccountPoolManager(pool_file=tmp_path / "pool.json")
    settings = OrchestratorSettings(agy_bin=fake_agy, default_workspace=tmp_path)
    runner = AntigravityWorkerRunner(settings=settings, registry=ModelRegistry(fake_agy), pool=pool)

    cli_calls = 0

    async def _fake_cli_pass(self, **kwargs):
        nonlocal cli_calls
        cli_calls += 1
        return (
            {
                "status": "SUCCESS",
                "conversation_id": "conv-reflex-1",
                "response": f"Completed pass {cli_calls}",
                "duration_seconds": 1.2,
                "num_turns": 1,
                "usage": {"total_tokens": 500},
            },
            [],
            0,
        )

    verify_calls = 0

    async def _fake_verify_pass_on_second(workspace_dir, command, **kwargs):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 1:
            return {"passed": False, "exit_code": 1, "output_digest": "AssertionError: expected 2 == 3"}
        return {"passed": True, "exit_code": 0, "output_digest": "1 passed"}

    monkeypatch.setattr(AntigravityWorkerRunner, "_execute_single_cli_pass", _fake_cli_pass)
    monkeypatch.setattr("agy_orchestrator.runner.run_verification_check", _fake_verify_pass_on_second)

    spec = WorkerTaskSpec(
        prompt="Implement math",
        workspace_dir=tmp_path,
        verify_command="pytest -q",
        max_reflexion_retries=2,
    )
    res = await runner.run_task(spec)
    assert res.status == "SUCCESS"
    assert res.verification_passed is True
    assert res.reflexion_attempts == 1
    assert res.circuit_breaker_tripped is False

    # Now test Circuit Breaker tripping when identical error repeats consecutively
    async def _fake_verify_always_same_error(workspace_dir, command, **kwargs):
        return {"passed": False, "exit_code": 1, "output_digest": "SyntaxError: invalid syntax at line 10"}

    monkeypatch.setattr("agy_orchestrator.runner.run_verification_check", _fake_verify_always_same_error)
    res_cb = await runner.run_task(spec)
    assert res_cb.status == "CIRCUIT_BREAKER_TRIPPED"
    assert res_cb.circuit_breaker_tripped is True
    assert res_cb.verification_passed is False
    assert any("[CIRCUIT-BREAKER]" in w for w in res_cb.warnings)


@pytest.mark.asyncio
async def test_git_init_and_checkpoint_rollback(tmp_path: Path) -> None:
    ws = tmp_path / "sample_repo"
    ws.mkdir()
    app_py = ws / "app.py"
    app_py.write_text("v = 1\n", encoding="utf-8")

    sync_res = await init_or_sync_git_repo(ws, commit_message="chore: init", push=False)
    assert sync_res["initialized_git"] is True
    assert sync_res["committed"] is True
    checkpoint = sync_res["commit_sha"]
    assert checkpoint

    # Corrupt file and rollback to checkpoint
    app_py.write_text("v = 'BROKEN'\n", encoding="utf-8")
    rb = await rollback_to_git_checkpoint(ws, checkpoint)
    assert rb["rolled_back"] is True
    assert app_py.read_text(encoding="utf-8").strip() == "v = 1"
