"""Installer and uninstaller for Google Antigravity IDE & CLI (`~/.gemini/config` or project `.agents`)."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from agy_orchestrator.opencode_installer import MANAGED_SKILL_DIRS, bundled_skills_dir

MANAGED_SERVER_NAME = "antigravity_workers"
MANAGED_PLUGIN_NAME = "agy-orchestrator"

ANTIGRAVITY_RULES_MD = """# Antigravity Multi-Agent Orchestrator Rules

You are Antigravity, acting as the Lead Architect & Boss.
You have access to the `antigravity_workers` MCP server tools:
- `delegate_task`: Delegate heavy coding, refactoring, or implementation to headless workers (`gemini-3.8-flash-high`) without polluting your context window.
- `delegate_flash_worker`: Quickly run tasks on Gemini 3.8 Flash worker tier.
- `argon_boss_review`: Request deep review or architectural planning from Gemini 4 Argon.
- `continue_worker_session`: Resume an existing worker session by `conversation_id`.
- `delegate_parallel_tasks`: Run multiple independent tasks concurrently across disjoint files.
- `inspect_workspace` & `verify_workspace`: Inspect recent diffs or run verification tests with low token usage.

## Orchestration Guidelines:
1. For complex, multi-file code generation (e.g. implementing loaders, normalizers, large modules):
   - Formulate a clear specification.
   - Dispatch to `delegate_flash_worker` or `delegate_task(model='gemini-3.8-flash-high', timeout_sec=900)`.
   - The worker directly modifies files on disk in the workspace and returns a compact digest.
2. If a worker task times out or produces partial output, use `continue_worker_session(conversation_id=...)` to resume without re-explaining context.
3. Keep the user updated with clear, concise status reports.
"""


def default_antigravity_config_dir() -> Path:
    """Return the default Antigravity global configuration directory (`~/.gemini/config`)."""
    return Path.home() / ".gemini" / "config"


def build_antigravity_mcp_entry(
    python_executable: Path | None = None,
    agy_bin: Path | None = None,
    default_workspace: Path | None = None,
) -> dict[str, Any]:
    """Construct the Antigravity `mcpServers.antigravity_workers` configuration object."""
    py_exe = str((python_executable or Path(sys.executable)).resolve())
    env: dict[str, str] = {
        "PYTHONIOENCODING": "utf-8",
    }
    if agy_bin is not None:
        env["AGY_BIN"] = str(agy_bin.resolve())
    if default_workspace is not None:
        env["AGY_DEFAULT_WORKSPACE"] = str(default_workspace.resolve())

    return {
        "command": py_exe,
        "args": ["-m", "agy_orchestrator.mcp_server"],
        "env": env,
    }


def merge_antigravity_mcp_config(
    existing_json_text: str,
    mcp_entry: dict[str, Any],
    server_name: str = MANAGED_SERVER_NAME,
) -> dict[str, Any]:
    """Merge the `antigravity_workers` MCP server into an Antigravity `mcp_config.json` object."""
    if existing_json_text.strip():
        data = json.loads(existing_json_text)
        if not isinstance(data, dict):
            raise ValueError("Existing mcp_config.json root must be a JSON object.")
    else:
        data = {}

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers

    servers[server_name] = mcp_entry
    return data


def remove_antigravity_mcp_config(
    existing_json_text: str,
    server_name: str = MANAGED_SERVER_NAME,
) -> tuple[dict[str, Any], bool]:
    """Remove `antigravity_workers` from an Antigravity `mcp_config.json` object."""
    if not existing_json_text.strip():
        return {}, False

    data = json.loads(existing_json_text)
    if not isinstance(data, dict):
        return {}, False

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or server_name not in servers:
        return data, False

    servers.pop(server_name, None)
    return data, True


def install_to_antigravity(
    target_dir: Path | None = None,
    is_project: bool = False,
    python_executable: Path | None = None,
    agy_bin: Path | None = None,
    default_workspace: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Install Antigravity MCP server, skills, and rules into global config or project `.agents`."""
    base_dir = (target_dir or (Path.cwd() if is_project else default_antigravity_config_dir())).resolve()

    if is_project:
        agents_dir = base_dir / ".agents"
        mcp_config_path = agents_dir / "mcp_config.json"
        skills_dir = agents_dir / "skills"
        rules_dir = agents_dir / "rules"
        plugin_dir = None
    else:
        agents_dir = base_dir
        mcp_config_path = base_dir / "mcp_config.json"
        plugin_dir = base_dir / "plugins" / MANAGED_PLUGIN_NAME
        skills_dir = plugin_dir / "skills"
        rules_dir = plugin_dir / "rules"

    mcp_entry = build_antigravity_mcp_entry(
        python_executable=python_executable,
        agy_bin=agy_bin,
        default_workspace=default_workspace or (base_dir if is_project else None),
    )

    existing_mcp_text = ""
    backup_path: Path | None = None
    if mcp_config_path.exists():
        existing_mcp_text = mcp_config_path.read_text(encoding="utf-8")
        if not dry_run and existing_mcp_text.strip():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = mcp_config_path.with_name(f"{mcp_config_path.name}.{timestamp}.bak")
            backup_path.write_text(existing_mcp_text, encoding="utf-8")

    updated_mcp_data = merge_antigravity_mcp_config(existing_mcp_text, mcp_entry)

    installed_skills: list[str] = []
    src_skills = bundled_skills_dir()

    if not dry_run:
        mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_config_path.write_text(json.dumps(updated_mcp_data, indent=2) + "\n", encoding="utf-8")

        if plugin_dir is not None:
            plugin_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "name": MANAGED_PLUGIN_NAME,
                "description": "Multi-agent orchestrator connecting Antigravity to Gemini 3.8 Flash workers and Chrome CDP Web Boss pool.",
                "version": "0.1.0",
            }
            (plugin_dir / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            (plugin_dir / "mcp_config.json").write_text(json.dumps(updated_mcp_data, indent=2) + "\n", encoding="utf-8")

        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "AGENTS.md").write_text(ANTIGRAVITY_RULES_MD, encoding="utf-8")

        skills_dir.mkdir(parents=True, exist_ok=True)
        if src_skills.exists():
            for skill_name in MANAGED_SKILL_DIRS:
                skill_src = src_skills / skill_name
                if skill_src.is_dir():
                    skill_dst = skills_dir / skill_name
                    if skill_dst.exists():
                        shutil.rmtree(skill_dst)
                    shutil.copytree(skill_src, skill_dst)
                    installed_skills.append(skill_name)
    else:
        installed_skills = list(MANAGED_SKILL_DIRS)

    return {
        "dry_run": dry_run,
        "scope": "project" if is_project else "global",
        "mcp_config_path": str(mcp_config_path),
        "backup_path": str(backup_path) if backup_path else None,
        "plugin_dir": str(plugin_dir) if plugin_dir else None,
        "skills_dir": str(skills_dir),
        "rules_file": str(rules_dir / "AGENTS.md"),
        "installed_skills": installed_skills,
        "mcp_server": MANAGED_SERVER_NAME,
    }


def uninstall_from_antigravity(
    target_dir: Path | None = None,
    is_project: bool = False,
) -> dict[str, Any]:
    """Safely remove Antigravity MCP server, plugin, and installed skills."""
    base_dir = (target_dir or (Path.cwd() if is_project else default_antigravity_config_dir())).resolve()

    if is_project:
        agents_dir = base_dir / ".agents"
        mcp_config_path = agents_dir / "mcp_config.json"
        skills_dir = agents_dir / "skills"
        rules_dir = agents_dir / "rules"
        plugin_dir = None
    else:
        mcp_config_path = base_dir / "mcp_config.json"
        plugin_dir = base_dir / "plugins" / MANAGED_PLUGIN_NAME
        skills_dir = plugin_dir / "skills"
        rules_dir = plugin_dir / "rules"

    removed_mcp = False
    if mcp_config_path.exists():
        existing_text = mcp_config_path.read_text(encoding="utf-8")
        updated_data, changed = remove_antigravity_mcp_config(existing_text)
        if changed:
            mcp_config_path.write_text(json.dumps(updated_data, indent=2) + "\n", encoding="utf-8")
            removed_mcp = True

    removed_plugin = False
    if plugin_dir is not None and plugin_dir.exists():
        shutil.rmtree(plugin_dir)
        removed_plugin = True

    removed_skills: list[str] = []
    if is_project and skills_dir.exists():
        for skill_name in MANAGED_SKILL_DIRS:
            target_skill = skills_dir / skill_name
            if target_skill.exists():
                shutil.rmtree(target_skill)
                removed_skills.append(skill_name)

    return {
        "scope": "project" if is_project else "global",
        "mcp_config_path": str(mcp_config_path),
        "removed_mcp": removed_mcp,
        "removed_plugin": removed_plugin,
        "removed_skills": removed_skills,
    }
