"""Unit tests for Antigravity MCP configuration, plugin, rules, and skills installer."""

from pathlib import Path
import json

from agy_orchestrator.antigravity_installer import (
    install_to_antigravity,
    uninstall_from_antigravity,
    merge_antigravity_mcp_config,
    remove_antigravity_mcp_config,
    MANAGED_SERVER_NAME,
    MANAGED_PLUGIN_NAME,
)
from agy_orchestrator.opencode_installer import MANAGED_SKILL_DIRS


def test_merge_and_remove_antigravity_mcp_config() -> None:
    initial = '{"mcpServers": {"existing": {"command": "node"}}}'
    mcp_entry = {"command": "python", "args": ["-m", "test"]}
    merged = merge_antigravity_mcp_config(initial, mcp_entry, server_name="test_server")
    assert "existing" in merged["mcpServers"]
    assert "test_server" in merged["mcpServers"]

    cleaned, changed = remove_antigravity_mcp_config(json.dumps(merged), server_name="test_server")
    assert changed is True
    assert "test_server" not in cleaned["mcpServers"]
    assert "existing" in cleaned["mcpServers"]


def test_install_and_uninstall_antigravity_global_and_project(tmp_path: Path) -> None:
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("echo", encoding="utf-8")

    # 1. Test Global Install
    global_dir = tmp_path / "gemini_config"
    global_dir.mkdir()
    existing_mcp = global_dir / "mcp_config.json"
    existing_mcp.write_text('{"mcpServers": {"demo": {"command": "demo"}}}\n', encoding="utf-8")

    # Dry-run
    dry = install_to_antigravity(target_dir=global_dir, is_project=False, agy_bin=fake_agy, dry_run=True)
    assert dry["dry_run"] is True
    assert not (global_dir / "plugins").exists()

    # Apply global
    applied = install_to_antigravity(target_dir=global_dir, is_project=False, agy_bin=fake_agy, dry_run=False)
    assert applied["scope"] == "global"
    assert applied["backup_path"] is not None
    assert Path(applied["backup_path"]).is_file()
    assert (global_dir / "plugins" / MANAGED_PLUGIN_NAME / "plugin.json").is_file()
    assert (global_dir / "plugins" / MANAGED_PLUGIN_NAME / "rules" / "AGENTS.md").is_file()
    assert len(applied["installed_skills"]) == len(MANAGED_SKILL_DIRS)

    # Verify mcp_config.json contains both existing and antigravity_workers
    data = json.loads(existing_mcp.read_text(encoding="utf-8"))
    assert "demo" in data["mcpServers"]
    assert MANAGED_SERVER_NAME in data["mcpServers"]

    # Global Uninstall
    removed = uninstall_from_antigravity(target_dir=global_dir, is_project=False)
    assert removed["removed_mcp"] is True
    assert removed["removed_plugin"] is True
    data_after = json.loads(existing_mcp.read_text(encoding="utf-8"))
    assert MANAGED_SERVER_NAME not in data_after["mcpServers"]
    assert "demo" in data_after["mcpServers"]

    # 2. Test Project Install
    proj_dir = tmp_path / "my_project"
    proj_dir.mkdir()

    proj_applied = install_to_antigravity(target_dir=proj_dir, is_project=True, agy_bin=fake_agy, dry_run=False)
    assert proj_applied["scope"] == "project"
    assert (proj_dir / ".agents" / "mcp_config.json").is_file()
    assert (proj_dir / ".agents" / "rules" / "AGENTS.md").is_file()
    for skill_name in MANAGED_SKILL_DIRS:
        assert (proj_dir / ".agents" / "skills" / skill_name / "SKILL.md").is_file()

    # Project Uninstall
    proj_removed = uninstall_from_antigravity(target_dir=proj_dir, is_project=True)
    assert proj_removed["removed_mcp"] is True
    assert len(proj_removed["removed_skills"]) == len(MANAGED_SKILL_DIRS)
