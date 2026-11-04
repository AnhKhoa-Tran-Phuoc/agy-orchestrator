"""Unit tests for surgical TOML updates and Codex skill installation."""

from pathlib import Path
import tomllib

from agy_orchestrator.codex_installer import (
    install_to_codex,
    uninstall_from_codex,
)

SAMPLE_EXISTING_CODEX_TOML = """\
model = "gpt-6-astra"
model_reasoning_effort = "high"

[mcp_servers.node_repl]
args = []
command = 'C:\\Users\\Demo\\node_repl.exe'

[projects.'c:\\users\\demo\\project']
trust_level = "trusted"
"""


def test_install_and_uninstall_preserves_existing_codex_config_lines(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    cfg_file = codex_dir / "config.toml"
    cfg_file.write_text(SAMPLE_EXISTING_CODEX_TOML, encoding="utf-8")

    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("echo", encoding="utf-8")

    # 1. Dry-run does not modify disk
    dry = install_to_codex(codex_home=codex_dir, agy_bin=fake_agy, dry_run=True)
    assert dry["dry_run"] is True
    assert cfg_file.read_text(encoding="utf-8") == SAMPLE_EXISTING_CODEX_TOML

    # 2. Live install creates backup, preserves untouched lines, and parses as valid TOML
    applied = install_to_codex(codex_home=codex_dir, agy_bin=fake_agy, dry_run=False)
    assert applied["backup_path"] is not None
    assert Path(applied["backup_path"]).is_file()
    assert Path(applied["skill_path"]).is_file()

    merged_text = cfg_file.read_text(encoding="utf-8")
    assert SAMPLE_EXISTING_CODEX_TOML in merged_text
    parsed = tomllib.loads(merged_text)
    assert "antigravity_workers" in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["antigravity_workers"]["tool_timeout_sec"] == 600

    # 3. Re-running install is idempotent (replaces existing block rather than duplicating)
    install_to_codex(codex_home=codex_dir, agy_bin=fake_agy, dry_run=False)
    second_text = cfg_file.read_text(encoding="utf-8")
    assert second_text.count("[mcp_servers.antigravity_workers]") == 1

    # 4. Uninstall cleanly removes managed block and skill while preserving original config
    removed = uninstall_from_codex(codex_home=codex_dir)
    assert removed["removed_mcp_block"] is True
    assert removed["removed_skill"] is True
    after_uninstall = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    assert "antigravity_workers" not in after_uninstall.get("mcp_servers", {})
    assert "node_repl" in after_uninstall["mcp_servers"]
