"""Unit tests for Gemini Web CDP browser resolution, file extraction, and worker Web CDP fallback."""

from pathlib import Path
import pytest

from agy_orchestrator.account_pool import AccountPoolManager
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.gemini_web_cdp import (
    default_chrome_profile_dir,
    extract_and_write_web_files,
    is_cdp_port_open,
    resolve_browser_binary,
)
from agy_orchestrator.models import ModelRegistry
from agy_orchestrator.runner import AntigravityWorkerRunner, WorkerTaskSpec


def test_default_chrome_profile_dir_and_browser_discovery(tmp_path: Path) -> None:
    fake_chrome = tmp_path / "chrome.exe"
    fake_chrome.write_text("bin", encoding="utf-8")

    resolved = resolve_browser_binary(fake_chrome)
    assert resolved == fake_chrome.resolve()
    assert default_chrome_profile_dir().name == "chrome_profile"
    assert isinstance(is_cdp_port_open(59999), bool)


def test_extract_and_write_web_files_tags_and_fenced_fallback(tmp_path: Path) -> None:
    resp_xml = (
        "Here are the generated files:\n"
        '<file path="src/hello.py">\n```python\nprint("hello world")\n```\n</file>\n'
        '<file path="README.md"># Title\n</file>\n'
    )
    written = extract_and_write_web_files(resp_xml, tmp_path, ["src/hello.py", "README.md"])
    assert written == ["src/hello.py", "README.md"]
    assert (tmp_path / "src" / "hello.py").read_text(encoding="utf-8").strip() == 'print("hello world")'
    assert (tmp_path / "README.md").read_text(encoding="utf-8").strip() == "# Title"

    # Single output file fallback using fenced code block
    resp_fenced = "Sure, here is the module:\n```python\ndef add(a, b):\n    return a + b\n```\n"
    written2 = extract_and_write_web_files(resp_fenced, tmp_path, ["src/math_utils.py"])
    assert written2 == ["src/math_utils.py"]
    assert "def add(a, b):" in (tmp_path / "src" / "math_utils.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_worker_flash_quota_falls_back_to_web_cdp_and_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_agy = tmp_path / "fake_agy.cmd"
    fake_agy.write_text("@echo off\n", encoding="utf-8")

    pool = AccountPoolManager(pool_file=tmp_path / "pool.json")
    settings = OrchestratorSettings(agy_bin=fake_agy, default_workspace=tmp_path)
    registry = ModelRegistry(agy_bin=fake_agy)
    runner = AntigravityWorkerRunner(settings=settings, registry=registry, pool=pool)

    class _FakeProc:
        returncode = 1

        async def communicate(self):
            err = b'{"status":"ERROR","response":"Quota exceeded for 5-hour rolling window on gemini-3.8-flash-high"}'
            return err, b""

        def kill(self):
            pass

    async def _fake_subproc(*args, **kwargs):
        return _FakeProc()

    async def _fake_web_cdp(prompt: str, **kwargs):
        return {
            "status": "SUCCESS",
            "account_id": "acc-2",
            "cdp_port": 9223,
            "model_used": "gemini-web-pro",
            "worker_summary": (
                '<file path="deliverables/report.md">\n# Web Fallback Deliverable\nCompleted via acc-2.\n</file>\n'
                "- Created `deliverables/report.md` via Gemini Web CDP."
            ),
            "antigravity_usage": {"total_tokens": 250},
        }

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_subproc)
    monkeypatch.setattr("agy_orchestrator.runner.query_gemini_web_cdp", _fake_web_cdp)

    spec = WorkerTaskSpec(
        prompt="Generate report",
        workspace_dir=tmp_path,
        tier="coding",
        model="gemini-3.8-flash-high",
        output_files=["deliverables/report.md"],
    )
    res = await runner.run_task(spec)
    assert res.status == "SUCCESS"
    assert "gemini-web-cdp(acc-2)" in res.model_used
    assert (tmp_path / "deliverables" / "report.md").is_file()
    assert "Completed via acc-2." in (tmp_path / "deliverables" / "report.md").read_text(encoding="utf-8")


