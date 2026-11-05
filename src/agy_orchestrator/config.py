"""Configuration and binary discovery for agy-orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil


def resolve_agy_binary(explicit_path: str | Path | None = None) -> Path:
    """Locate the official Google Antigravity CLI (`agy`) executable.

    Search order:
    1. `explicit_path` argument if provided
    2. `AGY_BIN` environment variable
    3. System `PATH` (`agy` / `agy.exe`)
    4. Standard Antigravity CLI installation directories (`~/.gemini/bin/`, `~/.local/bin/`)
    """
    candidates: list[Path] = []

    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())

    env_bin = os.environ.get("AGY_BIN")
    if env_bin:
        candidates.append(Path(env_bin).expanduser())

    which_agy = shutil.which("agy") or shutil.which("agy.exe")
    if which_agy:
        candidates.append(Path(which_agy))

    exe_name = "agy.exe" if os.name == "nt" else "agy"
    home = Path.home()
    candidates.extend(
        [
            home / ".gemini" / "bin" / exe_name,
            home / ".local" / "bin" / exe_name,
        ]
    )

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Antigravity" / "agy.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    install_hint = (
        "irm https://antigravity.google/cli/install.ps1 | iex"
        if os.name == "nt"
        else "curl -fsSL https://antigravity.google/cli/install.sh | bash"
    )
    raise FileNotFoundError(
        f"Antigravity CLI ('{exe_name}') was not found in PATH or ~/.gemini/bin. "
        f"Install it via: {install_hint} (or set AGY_BIN=/path/to/{exe_name})."
    )


@dataclass(slots=True)
class OrchestratorSettings:
    """Runtime settings controlling how Antigravity workers are invoked."""

    agy_bin: Path = field(default_factory=resolve_agy_binary)
    default_workspace: Path = field(
        default_factory=lambda: Path(os.environ.get("AGY_DEFAULT_WORKSPACE", os.getcwd())).resolve()
    )
    default_mode: str = field(
        default_factory=lambda: os.environ.get("AGY_DEFAULT_MODE", "accept-edits")
    )
    default_timeout_sec: int = field(
        default_factory=lambda: int(os.environ.get("AGY_TIMEOUT_SEC", "900"))
    )
    max_summary_chars: int = field(
        default_factory=lambda: int(os.environ.get("AGY_MAX_SUMMARY_CHARS", "1800"))
    )
    max_parallel_workers: int = field(
        default_factory=lambda: int(os.environ.get("AGY_MAX_PARALLEL_WORKERS", "3"))
    )
    state_dir_name: str = ".agy-orch"

    def resolve_workspace(self, workspace_dir: str | Path | None = None) -> Path:
        """Resolve and validate a target workspace directory."""
        target = Path(workspace_dir).expanduser().resolve() if workspace_dir else self.default_workspace
        target.mkdir(parents=True, exist_ok=True)
        return target
