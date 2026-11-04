"""Safe installer and uninstaller for ChatGPT Desktop / Codex (`~/.codex/config.toml` & skills)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil
import sys
import tomllib
from typing import Any

from agy_orchestrator.opencode_installer import MANAGED_SKILL_DIRS, bundled_skills_dir

BLOCK_START = "# >>> agy-orchestrator >>>"
BLOCK_END = "# <<< agy-orchestrator <<<"


def _toml_single_quote(value: str) -> str:
    """Format a string as a TOML literal string when safe, falling back to escaped quotes."""
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_codex_mcp_block(
    python_executable: Path | None = None,
    agy_bin: Path | None = None,
    default_workspace: Path | None = None,
    server_name: str = "antigravity_workers",
    tool_timeout_sec: int = 600,
) -> str:
    """Render a self-contained TOML block registering the MCP server in `~/.codex/config.toml`."""
    py_exe = (python_executable or Path(sys.executable)).resolve()
    lines = [
        BLOCK_START,
        f"[mcp_servers.{server_name}]",
        f"command = {_toml_single_quote(str(py_exe))}",
        'args = ["-m", "agy_orchestrator.mcp_server"]',
        "startup_timeout_sec = 30",
        f"tool_timeout_sec = {int(tool_timeout_sec)}",
        "",
        f"[mcp_servers.{server_name}.env]",
        'PYTHONIOENCODING = "utf-8"',
    ]
    if agy_bin is not None:
        lines.append(f"AGY_BIN = {_toml_single_quote(str(agy_bin.resolve()))}")
    if default_workspace is not None:
        lines.append(
            f"AGY_DEFAULT_WORKSPACE = {_toml_single_quote(str(default_workspace.resolve()))}"
        )
    lines.append(BLOCK_END)
    return "\n".join(lines)


def upsert_toml_block(existing_toml: str, new_block: str) -> str:
    """Insert or replace the `agy-orchestrator` block without altering any other TOML lines."""
    if existing_toml.strip():
        tomllib.loads(existing_toml)

    pattern = re.compile(
        rf"{re.escape(BLOCK_START)}.*?{re.escape(BLOCK_END)}",
        flags=re.DOTALL,
    )
    if pattern.search(existing_toml):
        updated = pattern.sub(lambda _: new_block, existing_toml)
    else:
        sep = "\n\n" if existing_toml and not existing_toml.endswith("\n\n") else ("" if not existing_toml else "\n")
        if existing_toml and not existing_toml.endswith("\n"):
            sep = "\n\n"
        updated = f"{existing_toml}{sep}{new_block}\n"

    # Verify the merged document is 100% valid TOML before returning
    tomllib.loads(updated)
    return updated


def remove_toml_block(existing_toml: str) -> tuple[str, bool]:
    """Remove the `agy-orchestrator` managed TOML block if present."""
    pattern = re.compile(
        rf"\n*{re.escape(BLOCK_START)}.*?{re.escape(BLOCK_END)}\n*",
        flags=re.DOTALL,
    )
    if not pattern.search(existing_toml):
        return existing_toml, False

    cleaned = pattern.sub("\n", existing_toml).rstrip() + "\n"
    if cleaned.strip():
        tomllib.loads(cleaned)
    return cleaned, True


def bundled_skill_source() -> Path:
    """Locate the bundled `skills/antigravity-manager/SKILL.md` file."""
    return bundled_skills_dir() / "antigravity-manager" / "SKILL.md"


def install_to_codex(
    codex_home: Path | None = None,
    agy_bin: Path | None = None,
    default_workspace: Path | None = None,
    install_skill: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register `antigravity_workers` MCP server and all 6 Multi-Agent skills in `~/.codex`."""
    target_home = (codex_home or (Path.home() / ".codex")).expanduser().resolve()
    config_path = target_home / "config.toml"
    existing_text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""

    block = render_codex_mcp_block(
        python_executable=Path(sys.executable),
        agy_bin=agy_bin,
        default_workspace=default_workspace,
    )
    updated_text = upsert_toml_block(existing_text, block)

    src_skills_dir = bundled_skills_dir()
    skill_dst = target_home / "skills" / "antigravity-manager" / "SKILL.md"
    installed_skills: list[str] = []
    if install_skill:
        for sname in MANAGED_SKILL_DIRS:
            if (src_skills_dir / sname / "SKILL.md").is_file():
                installed_skills.append(sname)

    backup_path: Path | None = None
    if not dry_run:
        target_home.mkdir(parents=True, exist_ok=True)
        if config_path.is_file() and existing_text != updated_text:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = target_home / f"config.toml.{stamp}.bak"
            shutil.copy2(config_path, backup_path)
        config_path.write_text(updated_text, encoding="utf-8")

        for sname in installed_skills:
            dst_file = target_home / "skills" / sname / "SKILL.md"
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_skills_dir / sname / "SKILL.md", dst_file)

    return {
        "dry_run": dry_run,
        "config_path": str(config_path),
        "backup_path": str(backup_path) if backup_path else None,
        "skill_installed": bool(installed_skills),
        "skill_path": str(skill_dst) if install_skill else None,
        "installed_skills": installed_skills,
        "toml_block": block,
    }


def uninstall_from_codex(
    codex_home: Path | None = None,
    remove_skill: bool = True,
) -> dict[str, Any]:
    """Remove `antigravity_workers` MCP block and managed skills from `~/.codex`."""
    target_home = (codex_home or (Path.home() / ".codex")).expanduser().resolve()
    config_path = target_home / "config.toml"
    removed_block = False

    if config_path.is_file():
        existing_text = config_path.read_text(encoding="utf-8")
        cleaned_text, removed_block = remove_toml_block(existing_text)
        if removed_block:
            config_path.write_text(cleaned_text, encoding="utf-8")

    removed_skills: list[str] = []
    if remove_skill:
        for sname in MANAGED_SKILL_DIRS:
            sdir = target_home / "skills" / sname
            if sdir.is_dir():
                shutil.rmtree(sdir)
                removed_skills.append(sname)

    return {
        "config_path": str(config_path),
        "removed_mcp_block": removed_block,
        "removed_skill": bool(removed_skills),
        "removed_skills": removed_skills,
    }
