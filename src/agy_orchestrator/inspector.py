"""Low-token inspection and verification utilities for the ChatGPT/Codex Manager."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
from typing import Any


def _clip_middle(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    head_len = max(200, (max_chars - 120) // 2)
    tail_len = max(200, max_chars - head_len - 120)
    omitted = len(cleaned) - head_len - tail_len
    return f"{cleaned[:head_len].rstrip()}\n\n...[omitted {omitted} chars]...\n\n{cleaned[-tail_len:].lstrip()}"


async def _run_git(workspace: Path, *git_args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *git_args,
        cwd=str(workspace),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=15)
    out = stdout_b.decode("utf-8", errors="replace")
    if proc.returncode != 0 and not out.strip():
        out = stderr_b.decode("utf-8", errors="replace")
    return proc.returncode or 0, out.strip()


def _recent_files_snapshot(workspace: Path, limit: int = 25) -> list[dict[str, Any]]:
    """Fallback change listing for plain folders that are not Git repositories."""
    entries: list[tuple[float, Path, int]] = []
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".agy-orch", ".pytest_cache"}

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        root_path = Path(root)
        for fname in files:
            fpath = root_path / fname
            try:
                stat = fpath.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, fpath, stat.st_size))

    entries.sort(key=lambda item: item[0], reverse=True)
    snapshot: list[dict[str, Any]] = []
    for _, fpath, size in entries[:limit]:
        rel = str(fpath.relative_to(workspace)).replace("\\", "/")
        snapshot.append({"file": rel, "bytes": size})
    return snapshot


async def inspect_workspace_changes(
    workspace_dir: str | Path,
    file_path: str | None = None,
    include_patch: bool = True,
    max_chars: int = 4000,
) -> dict[str, Any]:
    """Return a compact Git diff or recent-file overview so the Manager reviews with minimal tokens."""
    workspace = Path(workspace_dir).expanduser().resolve()
    if not workspace.is_dir():
        return {"error": f"Workspace directory does not exist: {workspace}"}

    rc, inside_git = await _run_git(workspace, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or inside_git.lower() != "true":
        return {
            "workspace_dir": str(workspace),
            "is_git_repo": False,
            "recent_files": _recent_files_snapshot(workspace),
        }

    _, status_out = await _run_git(workspace, "status", "--short", "--branch")
    stat_args = ["diff", "--stat"]
    if file_path:
        stat_args.extend(["--", file_path])
    _, diff_stat = await _run_git(workspace, *stat_args)

    result: dict[str, Any] = {
        "workspace_dir": str(workspace),
        "is_git_repo": True,
        "git_status": _clip_middle(status_out, 1200),
        "diff_stat": _clip_middle(diff_stat, 1200),
    }

    if include_patch:
        patch_args = ["diff", "-U3"]
        if file_path:
            patch_args.extend(["--", file_path])
        _, patch_out = await _run_git(workspace, *patch_args)
        result["patch_preview"] = _clip_middle(patch_out, max(600, max_chars))

    return result


def read_file_excerpt(
    file_path: str | Path,
    start_line: int = 1,
    end_line: int = 100,
    max_chars: int = 4000,
) -> dict[str, Any]:
    """Read a bounded line-numbered slice of a file for surgical Manager spot-checks."""
    target = Path(file_path).expanduser().resolve()
    if not target.is_file():
        return {"error": f"File not found: {target}"}

    all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total_lines = len(all_lines)
    safe_start = max(1, start_line)
    safe_end = min(total_lines, max(safe_start, end_line))

    selected = all_lines[safe_start - 1 : safe_end]
    numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=safe_start))

    return {
        "file_path": str(target),
        "total_lines": total_lines,
        "start_line": safe_start,
        "end_line": safe_end,
        "excerpt": _clip_middle(numbered, max(500, max_chars)),
    }


async def run_verification_check(
    workspace_dir: str | Path,
    command: str,
    timeout_sec: int = 120,
    max_output_chars: int = 3000,
) -> dict[str, Any]:
    """Run a test, linter, or verification command and return a bounded log digest."""
    workspace = Path(workspace_dir).expanduser().resolve()
    if not workspace.is_dir():
        return {"error": f"Workspace directory does not exist: {workspace}"}

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    started = time.perf_counter()
    if os.name == "nt":
        ps_script = f"$global:LASTEXITCODE = 0; {command}; if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}"
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps_script,
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

    timed_out = False
    try:
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=max(5, timeout_sec))
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        stdout_b, _ = await proc.communicate()

    elapsed = round(time.perf_counter() - started, 3)
    raw_output = stdout_b.decode("utf-8", errors="replace")
    exit_code = -1 if timed_out else int(proc.returncode or 0)

    return {
        "workspace_dir": str(workspace),
        "command": command,
        "passed": (not timed_out) and exit_code == 0,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "duration_seconds": elapsed,
        "output_digest": _clip_middle(raw_output, max(500, max_output_chars)),
    }
