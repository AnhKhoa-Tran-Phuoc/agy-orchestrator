"""Command-line interface (`agy-orch`) for terminal delegation, inspection, and MCP setup."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from agy_orchestrator.codex_installer import install_to_codex, uninstall_from_codex
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.inspector import (
    inspect_workspace_changes,
    read_file_excerpt,
    run_verification_check,
)
from agy_orchestrator.mcp_server import run_http, run_stdio
from agy_orchestrator.models import TIER_DESCRIPTIONS, VALID_TIERS, ModelRegistry
from agy_orchestrator.runner import AntigravityWorkerRunner, WorkerTaskSpec


def _print_json(data: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy-orch",
        description="Orchestrate Google Antigravity CLI models as quota-saving workers for ChatGPT / Codex.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # 1. models
    p_models = sub.add_parser("models", help="List available Antigravity models and task-tier mappings.")
    p_models.add_argument("--refresh", action="store_true", help="Force fresh query of `agy models`.")

    # 2. delegate
    p_del = sub.add_parser("delegate", help="Delegate a coding or writing task to an Antigravity worker.")
    p_del.add_argument("prompt", help="Task prompt / specification for the worker.")
    p_del.add_argument("--tier", choices=VALID_TIERS, default="coding", help="Task complexity tier.")
    p_del.add_argument("--model", default=None, help="Explicit model ID or shorthand override.")
    p_del.add_argument("--workspace", default=None, help="Target workspace directory.")
    p_del.add_argument(
        "--output-file",
        action="append",
        dest="output_files",
        default=None,
        help="Deliverable file path (repeatable).",
    )
    p_del.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        default=None,
        help="Optional reasoning effort override.",
    )
    p_del.add_argument("--allow-shell", action="store_true", help="Allow sandboxed shell execution.")
    p_del.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300).")

    # 3. resume
    p_res = sub.add_parser("resume", help="Continue an existing Antigravity worker session by conversation_id.")
    p_res.add_argument("conversation_id", help="Worker conversation ID returned by a prior run.")
    p_res.add_argument("prompt", help="Review feedback or follow-up instructions.")
    p_res.add_argument("--tier", choices=VALID_TIERS, default="coding", help="Task complexity tier.")
    p_res.add_argument("--model", default=None, help="Explicit model override.")
    p_res.add_argument("--workspace", default=None, help="Target workspace directory.")
    p_res.add_argument("--allow-shell", action="store_true", help="Allow sandboxed shell execution.")
    p_res.add_argument("--timeout", type=int, default=300, help="Timeout in seconds.")

    # 4. inspect
    p_ins = sub.add_parser("inspect", help="Inspect workspace Git changes or recent files with low token output.")
    p_ins.add_argument("--workspace", default=None, help="Workspace directory to inspect.")
    p_ins.add_argument("--file", default=None, help="Optional single file path filter.")
    p_ins.add_argument("--no-patch", action="store_true", help="Omit unified diff preview.")

    # 5. excerpt
    p_exc = sub.add_parser("excerpt", help="Read a line-numbered excerpt of a file.")
    p_exc.add_argument("file_path", help="File path to read.")
    p_exc.add_argument("--start", type=int, default=1, help="Start line (1-indexed).")
    p_exc.add_argument("--end", type=int, default=100, help="End line (inclusive).")

    # 6. verify
    p_ver = sub.add_parser("verify", help="Run a test/linter command and print a bounded verification digest.")
    p_ver.add_argument("command", help="Verification shell command (e.g. 'pytest -q').")
    p_ver.add_argument("--workspace", default=None, help="Workspace directory.")
    p_ver.add_argument("--timeout", type=int, default=120, help="Command timeout in seconds.")

    # 7. stats
    p_stats = sub.add_parser("stats", help="Show cumulative quota offloading and token savings report.")
    p_stats.add_argument("--workspace", default=None, help="Workspace directory.")

    # 8. mcp
    p_mcp = sub.add_parser("mcp", help="Start the MCP server (stdio by default, or HTTP).")
    p_mcp.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport protocol.",
    )
    p_mcp.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP/SSE transport.")
    p_mcp.add_argument("--port", type=int, default=8765, help="Bind port for HTTP/SSE transport.")

    # 9. install-codex
    p_inst = sub.add_parser(
        "install-codex",
        help="Register MCP server and Manager skill in ChatGPT Desktop / Codex (`~/.codex`).",
    )
    p_inst.add_argument("--codex-home", default=None, help="Custom `.codex` directory path.")
    p_inst.add_argument("--workspace", default=None, help="Optional default workspace path.")
    p_inst.add_argument("--no-skill", action="store_true", help="Skip installing the `antigravity-manager` skill.")
    p_inst.add_argument("--dry-run", action="store_true", help="Preview generated TOML block without writing files.")

    # 10. uninstall-codex
    p_uninst = sub.add_parser(
        "uninstall-codex",
        help="Remove `antigravity_workers` MCP configuration and skill from `~/.codex`.",
    )
    p_uninst.add_argument("--codex-home", default=None, help="Custom `.codex` directory path.")

    return parser


async def _dispatch_async(args: argparse.Namespace) -> int:
    settings = OrchestratorSettings()
    registry = ModelRegistry(settings.agy_bin)
    runner = AntigravityWorkerRunner(settings=settings, registry=registry)

    if args.subcommand == "models":
        catalog = await registry.list_models(refresh=args.refresh)
        _print_json(
            {
                "agy_binary": str(settings.agy_bin),
                "tier_guide": TIER_DESCRIPTIONS,
                "default_models_by_tier": {
                    t: registry.resolve_from_catalog(catalog, tier=t) for t in VALID_TIERS
                },
                "available_models": [m.to_dict() for m in catalog],
            }
        )
        return 0

    if args.subcommand == "delegate":
        spec = WorkerTaskSpec(
            prompt=args.prompt,
            workspace_dir=args.workspace,
            tier=args.tier,
            model=args.model,
            output_files=args.output_files,
            effort=args.effort,
            allow_shell=args.allow_shell,
            timeout_sec=args.timeout,
        )
        res = await runner.run_task(spec)
        _print_json(res.to_compact_dict())
        return 0 if res.status == "SUCCESS" else 1

    if args.subcommand == "resume":
        spec = WorkerTaskSpec(
            prompt=args.prompt,
            workspace_dir=args.workspace,
            tier=args.tier,
            model=args.model,
            conversation_id=args.conversation_id,
            allow_shell=args.allow_shell,
            timeout_sec=args.timeout,
        )
        res = await runner.run_task(spec)
        _print_json(res.to_compact_dict())
        return 0 if res.status == "SUCCESS" else 1

    if args.subcommand == "inspect":
        ws = settings.resolve_workspace(args.workspace)
        report = await inspect_workspace_changes(
            workspace_dir=ws,
            file_path=args.file,
            include_patch=not args.no_patch,
        )
        _print_json(report)
        return 0

    if args.subcommand == "excerpt":
        _print_json(read_file_excerpt(args.file_path, start_line=args.start, end_line=args.end))
        return 0

    if args.subcommand == "verify":
        ws = settings.resolve_workspace(args.workspace)
        report = await run_verification_check(
            workspace_dir=ws,
            command=args.command,
            timeout_sec=args.timeout,
        )
        _print_json(report)
        return 0 if report.get("passed") else 1

    if args.subcommand == "stats":
        _print_json(runner.get_quota_stats(args.workspace))
        return 0

    if args.subcommand == "install-codex":
        codex_home = Path(args.codex_home) if args.codex_home else None
        default_ws = Path(args.workspace) if args.workspace else None
        info = install_to_codex(
            codex_home=codex_home,
            agy_bin=settings.agy_bin,
            default_workspace=default_ws,
            install_skill=not args.no_skill,
            dry_run=args.dry_run,
        )
        _print_json(info)
        return 0

    if args.subcommand == "uninstall-codex":
        codex_home = Path(args.codex_home) if args.codex_home else None
        _print_json(uninstall_from_codex(codex_home=codex_home))
        return 0

    return 1


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "mcp":
        if args.transport == "stdio":
            run_stdio()
        else:
            run_http(host=args.host, port=args.port, transport=args.transport)
        return

    code = asyncio.run(_dispatch_async(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
