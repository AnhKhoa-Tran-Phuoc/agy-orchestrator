"""Tests verifying MCP server wiring, tool registration, and prompt templates."""

from pathlib import Path

import pytest

from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.mcp_server import create_mcp_server


@pytest.mark.asyncio
async def test_mcp_server_registers_all_manager_tools_and_prompts(tmp_path: Path) -> None:
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("echo", encoding="utf-8")

    settings = OrchestratorSettings(agy_bin=fake_agy, default_workspace=tmp_path)
    server = create_mcp_server(settings=settings)

    tools = await server.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = {
        "list_worker_models",
        "argon_boss_review",
        "ask_gemini_web_auto",
        "prepare_web_boss_handoff",
        "delegate_flash_worker",
        "delegate_task",
        "continue_worker_session",
        "delegate_parallel_tasks",
        "inspect_workspace_changes",
        "read_file_excerpt",
        "run_verification_check",
        "get_quota_savings_report",
        "get_account_pool_status",
        "get_per_model_quota_report",
        "register_google_account",
        "swap_active_account",
    }
    assert expected_tools.issubset(tool_names)

    prompts = await server.list_prompts()
    prompt_names = {p.name for p in prompts}
    assert "manager_mode" in prompt_names
