"""Unit tests for OpenCode multi-agent and MCP configuration installer."""

from pathlib import Path

from agy_orchestrator.opencode_installer import (
    MANAGED_AGENT_FILES,
    install_to_opencode,
    uninstall_from_opencode,
)


def test_install_and_uninstall_opencode_global_and_project(tmp_path: Path) -> None:
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("echo", encoding="utf-8")

    oc_global = tmp_path / "opencode_global"
    oc_global.mkdir()
    existing_cfg = oc_global / "opencode.json"
    existing_cfg.write_text('{"theme": "dark"}\n', encoding="utf-8")

    # 1. Dry-run leaves disk untouched
    dry = install_to_opencode(target_dir=oc_global, is_project=False, agy_bin=fake_agy, dry_run=True)
    assert dry["dry_run"] is True
    assert not (oc_global / "agents").exists()

    # 2. Global install merges opencode.json, creates backup, and copies all 8 agents
    applied = install_to_opencode(target_dir=oc_global, is_project=False, agy_bin=fake_agy, dry_run=False)
    assert applied["backup_path"] is not None
    assert Path(applied["backup_path"]).is_file()
    assert len(applied["installed_agents"]) == len(MANAGED_AGENT_FILES)
    assert applied["opencode_json"]["theme"] == "dark"
    assert "antigravity_workers" in applied["opencode_json"]["mcp"]["servers"]

    for fname in MANAGED_AGENT_FILES:
        assert (oc_global / "agents" / fname).is_file()

    # 3. Uninstall removes managed agents and MCP entry while preserving unrelated keys
    removed = uninstall_from_opencode(target_dir=oc_global, is_project=False)
    assert removed["removed_mcp_server"] is True
    assert len(removed["removed_agents"]) == len(MANAGED_AGENT_FILES)
