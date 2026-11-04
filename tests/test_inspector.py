"""Unit tests for low-token Manager inspection and verification tools."""

from pathlib import Path
import subprocess
import sys

import pytest

from agy_orchestrator.inspector import (
    inspect_workspace_changes,
    read_file_excerpt,
    run_verification_check,
)


def test_read_file_excerpt_returns_bounded_numbered_lines(tmp_path: Path) -> None:
    sample_file = tmp_path / "article.md"
    sample_file.write_text("\n".join(f"Line {i}" for i in range(1, 51)), encoding="utf-8")

    res = read_file_excerpt(sample_file, start_line=10, end_line=15)
    assert res["total_lines"] == 50
    assert res["start_line"] == 10
    assert res["end_line"] == 15
    assert "10: Line 10" in res["excerpt"]
    assert "15: Line 15" in res["excerpt"]
    assert "16: Line 16" not in res["excerpt"]


@pytest.mark.asyncio
async def test_inspect_workspace_changes_non_git_and_git(tmp_path: Path) -> None:
    plain_file = tmp_path / "draft.md"
    plain_file.write_text("Hello world", encoding="utf-8")

    non_git = await inspect_workspace_changes(tmp_path)
    assert non_git["is_git_repo"] is False
    assert any(item["file"] == "draft.md" for item in non_git["recent_files"])

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "draft.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    plain_file.write_text("Hello updated world\nSecond line\n", encoding="utf-8")
    git_res = await inspect_workspace_changes(tmp_path)
    assert git_res["is_git_repo"] is True
    assert "draft.md" in git_res["git_status"]
    assert "+Hello updated world" in git_res["patch_preview"]


@pytest.mark.asyncio
async def test_run_verification_check_captures_pass_and_fail(tmp_path: Path) -> None:
    py = f'"{sys.executable}"'
    ok_cmd = f"& {py} -c \"print('ALL_TESTS_GREEN')\"" if sys.platform == "win32" else f"{py} -c \"print('ALL_TESTS_GREEN')\""
    ok = await run_verification_check(tmp_path, ok_cmd)
    assert ok["passed"] is True
    assert ok["exit_code"] == 0
    assert "ALL_TESTS_GREEN" in ok["output_digest"]

    fail_cmd = f"& {py} -c \"import sys; sys.exit(2)\"" if sys.platform == "win32" else f"{py} -c \"import sys; sys.exit(2)\""
    fail = await run_verification_check(tmp_path, fail_cmd)
    assert fail["passed"] is False
    assert fail["exit_code"] == 2
