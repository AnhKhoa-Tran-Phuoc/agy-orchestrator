"""Installer and uninstaller for the OpenCode Multi-Agent System (`~/.config/opencode` or project `.opencode`)."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from typing import Any

MANAGED_AGENT_FILES: tuple[str, ...] = (
    "boss-gpt.md",
    "boss-gpt-sol.md",
    "boss-argon.md",
    "worker-gemini-flash.md",
    "worker-deepseek.md",
    "worker-glm.md",
    "worker-mimo.md",
    "worker-nemotron.md",
)


def bundled_opencode_agents_dir() -> Path:
    """Return the path to the bundled `opencode_agents/` directory."""
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "opencode_agents"


def default_opencode_global_dir() -> Path:
    """Return the default OpenCode global configuration directory (`~/.config/opencode`)."""
    return Path.home() / ".config" / "opencode"


def build_opencode_mcp_entry(
    python_executable: Path | None = None,
    agy_bin: Path | None = None,
    default_workspace: Path | None = None,
) -> dict[str, Any]:
    """Construct the OpenCode v2 `mcp.servers.antigravity_workers` configuration object."""
    py_exe = str((python_executable or Path(sys.executable)).resolve())
    env: dict[str, str] = {
        "PYTHONIOENCODING": "utf-8",
    }
    if agy_bin is not None:
        env["AGY_BIN"] = str(agy_bin.resolve())
    if default_workspace is not None:
        env["AGY_DEFAULT_WORKSPACE"] = str(default_workspace.resolve())

    return {
        "type": "local",
        "command": [py_exe, "-m", "agy_orchestrator.mcp_server"],
        "environment": env,
    }


def merge_opencode_config(
    existing_json_text: str,
    mcp_entry: dict[str, Any],
    server_name: str = "antigravity_workers",
) -> dict[str, Any]:
    """Merge the `antigravity_workers` MCP server into an `opencode.json` object without losing keys."""
    if existing_json_text.strip():
        data = json.loads(existing_json_text)
        if not isinstance(data, dict):
            raise ValueError("Existing opencode.json root must be a JSON object.")
    else:
        data = {}

    mcp_section = data.setdefault("mcp", {})
    if not isinstance(mcp_section, dict):
        mcp_section = {}
        data["mcp"] = mcp_section

    servers = mcp_section.setdefault("servers", {})
    if not isinstance(servers, dict):
        servers = {}
        mcp_section["servers"] = servers

    servers[server_name] = mcp_entry
    return data


def install_to_opencode(
    target_dir: Path | None = None,
    is_project: bool = False,
    agy_bin: Path | None = None,
    default_workspace: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Install the Multi-Agent team (`boss-*`, `worker-*`) and `antigravity_workers` MCP into OpenCode.

    - When `is_project=False` (default), installs globally into `~/.config/opencode/opencode.json`
      and `~/.config/opencode/agents/*.md`.
    - When `is_project=True`, installs into `<target_dir>/opencode.json` and `<target_dir>/.opencode/agents/*.md`.
    """
    base_dir = (target_dir or (Path.cwd() if is_project else default_opencode_global_dir())).expanduser().resolve()
    config_path = base_dir / "opencode.json"
    agents_dst_dir = (base_dir / ".opencode" / "agents") if is_project else (base_dir / "agents")

    existing_text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    mcp_entry = build_opencode_mcp_entry(
        python_executable=Path(sys.executable),
        agy_bin=agy_bin,
        default_workspace=default_workspace,
    )
    merged_config = merge_opencode_config(existing_text, mcp_entry)
    rendered_json = json.dumps(merged_config, indent=2, ensure_ascii=False) + "\n"

    src_agents_dir = bundled_opencode_agents_dir()
    installed_agents: list[str] = []
    for fname in MANAGED_AGENT_FILES:
        if (src_agents_dir / fname).is_file():
            installed_agents.append(fname)

    backup_path: Path | None = None
    if not dry_run:
        base_dir.mkdir(parents=True, exist_ok=True)
        agents_dst_dir.mkdir(parents=True, exist_ok=True)

        if config_path.is_file() and existing_text != rendered_json:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = base_dir / f"opencode.json.{stamp}.bak"
            shutil.copy2(config_path, backup_path)

        config_path.write_text(rendered_json, encoding="utf-8")

        for fname in installed_agents:
            shutil.copy2(src_agents_dir / fname, agents_dst_dir / fname)

    return {
        "dry_run": dry_run,
        "scope": "project" if is_project else "global",
        "config_path": str(config_path),
        "backup_path": str(backup_path) if backup_path else None,
        "agents_dir": str(agents_dst_dir),
        "installed_agents": [f.removesuffix(".md") for f in installed_agents],
        "opencode_json": merged_config,
    }


def uninstall_from_opencode(
    target_dir: Path | None = None,
    is_project: bool = False,
    server_name: str = "antigravity_workers",
) -> dict[str, Any]:
    """Remove managed OpenCode agents and `antigravity_workers` MCP entry."""
    base_dir = (target_dir or (Path.cwd() if is_project else default_opencode_global_dir())).expanduser().resolve()
    config_path = base_dir / "opencode.json"
    agents_dst_dir = (base_dir / ".opencode" / "agents") if is_project else (base_dir / "agents")

    removed_mcp = False
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers = data.get("mcp", {}).get("servers", {})
            if isinstance(servers, dict) and server_name in servers:
                del servers[server_name]
                removed_mcp = True
                config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass

    removed_agents: list[str] = []
    if agents_dst_dir.is_dir():
        for fname in MANAGED_AGENT_FILES:
            f = agents_dst_dir / fname
            if f.is_file():
                f.unlink()
                removed_agents.append(fname.removesuffix(".md"))

    return {
        "config_path": str(config_path),
        "removed_mcp_server": removed_mcp,
        "removed_agents": removed_agents,
    }
