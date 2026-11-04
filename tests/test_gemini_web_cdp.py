"""Unit tests for Gemini Web CDP browser resolution and configuration helpers."""

from pathlib import Path

from agy_orchestrator.gemini_web_cdp import (
    default_chrome_profile_dir,
    is_cdp_port_open,
    resolve_browser_binary,
)


def test_default_chrome_profile_dir_and_browser_discovery(tmp_path: Path) -> None:
    fake_chrome = tmp_path / "chrome.exe"
    fake_chrome.write_text("bin", encoding="utf-8")

    resolved = resolve_browser_binary(fake_chrome)
    assert resolved == fake_chrome.resolve()
    assert default_chrome_profile_dir().name == "chrome_profile"
    assert isinstance(is_cdp_port_open(59999), bool)

