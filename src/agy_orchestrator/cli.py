"""Command-line interface (`agy-orch`) for terminal delegation, inspection, and MCP/OpenCode setup."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from agy_orchestrator.account_pool import format_per_model_quota_table
from agy_orchestrator.antigravity_installer import (
    install_to_antigravity,
    uninstall_from_antigravity,
)
from agy_orchestrator.codex_installer import install_to_codex, uninstall_from_codex
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.gemini_web_cdp import (
    default_chrome_profile_dir,
    ensure_cdp_browser_running,
    query_gemini_web_cdp,
)
from agy_orchestrator.inspector import (
    inspect_workspace_changes,
    read_file_excerpt,
    run_verification_check,
)
from agy_orchestrator.mcp_server import run_http, run_stdio
from agy_orchestrator.models import TIER_DESCRIPTIONS, VALID_TIERS, ModelRegistry
from agy_orchestrator.opencode_installer import (
    install_to_opencode,
    uninstall_from_opencode,
)
from agy_orchestrator.runner import AntigravityWorkerRunner, WorkerTaskSpec


def _print_json(data: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy-orch",
        description="Orchestrate Google Antigravity CLI & Free OpenCode models as quota-saving workers.",
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
    p_del.add_argument("--timeout", type=int, default=900, help="Timeout in seconds (default: 900).")

    # 3. resume
    p_res = sub.add_parser("resume", help="Continue an existing Antigravity worker session by conversation_id.")
    p_res.add_argument("conversation_id", help="Worker conversation ID returned by a prior run.")
    p_res.add_argument("prompt", help="Review feedback or follow-up instructions.")
    p_res.add_argument("--tier", choices=VALID_TIERS, default="coding", help="Task complexity tier.")
    p_res.add_argument("--model", default=None, help="Explicit model override.")
    p_res.add_argument("--workspace", default=None, help="Target workspace directory.")
    p_res.add_argument("--allow-shell", action="store_true", help="Allow sandboxed shell execution.")
    p_res.add_argument("--timeout", type=int, default=900, help="Timeout in seconds (default: 900).")

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
    p_ver.add_argument("command", nargs="+", help="Verification shell command (e.g. 'pytest -q').")
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

    # 11. install-opencode
    p_oc_inst = sub.add_parser(
        "install-opencode",
        help="Install Boss (GPT + Gemini 4 Argon) and Worker (Free OpenCode + Gemini 3.8 Flash) agents into OpenCode.",
    )
    p_oc_inst.add_argument(
        "--target-dir",
        default=None,
        help="Custom OpenCode config or project directory (defaults to `~/.config/opencode`).",
    )
    p_oc_inst.add_argument(
        "--project",
        action="store_true",
        help="Install into a project directory (`<target-dir>/opencode.json` + `.opencode/agents/`) instead of global.",
    )
    p_oc_inst.add_argument("--workspace", default=None, help="Optional default workspace path for Antigravity workers.")
    p_oc_inst.add_argument("--dry-run", action="store_true", help="Preview installation without writing files.")

    # 12. uninstall-opencode
    p_oc_uninst = sub.add_parser(
        "uninstall-opencode",
        help="Remove managed Boss/Worker agents and `antigravity_workers` MCP server from OpenCode.",
    )
    p_oc_uninst.add_argument("--target-dir", default=None, help="Custom OpenCode config or project directory.")
    p_oc_uninst.add_argument("--project", action="store_true", help="Uninstall from project directory.")

    # install-antigravity
    p_ag_inst = sub.add_parser(
        "install-antigravity",
        help="Install `antigravity_workers` MCP server, skills, and rules into Antigravity IDE and CLI.",
    )
    p_ag_inst.add_argument(
        "--target-dir",
        default=None,
        help="Custom Antigravity config or project directory (defaults to `~/.gemini/config`).",
    )
    p_ag_inst.add_argument(
        "--project",
        action="store_true",
        help="Install into a project directory (`.agents/mcp_config.json` + `.agents/skills/`) instead of global.",
    )
    p_ag_inst.add_argument("--workspace", default=None, help="Optional default workspace path for Antigravity workers.")
    p_ag_inst.add_argument("--dry-run", action="store_true", help="Preview installation without writing files.")

    # uninstall-antigravity
    p_ag_uninst = sub.add_parser(
        "uninstall-antigravity",
        help="Remove managed `antigravity_workers` MCP server and plugin from Antigravity.",
    )
    p_ag_uninst.add_argument("--target-dir", default=None, help="Custom Antigravity config or project directory.")
    p_ag_uninst.add_argument("--project", action="store_true", help="Uninstall from project directory.")

    # 13. web-login
    p_wlogin = sub.add_parser(
        "web-login",
        help="Launch the dedicated Chrome CDP window at https://gemini.google.com/app for one-time Google Pro sign-in.",
    )
    p_wlogin.add_argument("--port", type=int, default=9222, help="Chrome remote debugging port (default: 9222).")

    # 14. web-ask
    p_wask = sub.add_parser(
        "web-ask",
        help="Hands-free auto-input prompt and auto-extract output from https://gemini.google.com/app via CDP.",
    )
    p_wask.add_argument("prompt", help="Prompt to automatically input into Gemini Web.")
    p_wask.add_argument("--port", type=int, default=None, help="Chrome remote debugging port (defaults to active account port).")
    p_wask.add_argument("--timeout", type=int, default=180, help="Response timeout in seconds.")

    # 15. account-status
    sub.add_parser(
        "account-status",
        help="Show 5-hour and weekly quota usage, cooldown status, and active account in the rotation pool.",
    )

    # 16. account-add
    p_aadd = sub.add_parser(
        "account-add",
        help="Register a Google account (e.g. acc-2, acc-3) into the automatic 5h/weekly quota swap pool.",
    )
    p_aadd.add_argument("account_id", help="Short account identifier (e.g. acc-1, acc-2, acc-3).")
    p_aadd.add_argument("--email", default="", help="Optional Google account email label.")
    p_aadd.add_argument("--port", type=int, default=None, help="Dedicated Chrome CDP port (auto-assigned if omitted).")
    p_aadd.add_argument("--limit-5h", type=int, default=400000, help="5-hour rolling token threshold.")
    p_aadd.add_argument("--limit-weekly", type=int, default=2500000, help="7-day weekly token threshold.")

    # 17. account-swap
    p_aswap = sub.add_parser(
        "account-swap",
        help="Swap the active Google account to the next healthy account in the pool.",
    )
    p_aswap.add_argument("--to", default=None, help="Optional target account_id to swap to.")
    p_aswap.add_argument(
        "--open-browser",
        action="store_true",
        help="Also open the target account's dedicated Chrome profile window.",
    )

    # 18. account-login
    p_alogin = sub.add_parser(
        "account-login",
        help="Open the dedicated Chrome profile for a specific account_id to sign in once.",
    )
    p_alogin.add_argument("account_id", help="Account identifier (e.g. acc-1, acc-2).")

    # 19. setup-web-accounts
    p_setup_web = sub.add_parser(
        "setup-web-accounts",
        help="Create N account slots (acc-1 with CLI+Web, acc-2..acc-N as Web-only) and open their Chrome login windows.",
    )
    p_setup_web.add_argument(
        "--count",
        type=int,
        default=2,
        help="Total number of Google accounts to set up and open for one-time sign-in (default: 2).",
    )
    p_setup_web.add_argument(
        "--no-open",
        action="store_true",
        help="Register the account slots without launching Chrome windows immediately.",
    )

    # 20. quota
    p_quota = sub.add_parser(
        "quota",
        help="Display per-model 5-hour & weekly quota dashboard across Antigravity CLI, Gemini Web, and OpenCode.",
    )
    p_quota.add_argument("--json", action="store_true", help="Output raw JSON instead of the formatted table.")
    p_quota.add_argument(
        "--watch",
        type=int,
        default=0,
        help="Continuously refresh the quota dashboard every N seconds (e.g. --watch 5).",
    )

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
        cmd_str = " ".join(args.command) if isinstance(args.command, list) else str(args.command)
        report = await run_verification_check(
            workspace_dir=ws,
            command=cmd_str,
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

    if args.subcommand == "install-opencode":
        target_dir = Path(args.target_dir) if args.target_dir else None
        default_ws = Path(args.workspace) if args.workspace else None
        info = install_to_opencode(
            target_dir=target_dir,
            is_project=args.project,
            agy_bin=settings.agy_bin,
            default_workspace=default_ws,
            dry_run=args.dry_run,
        )
        _print_json(info)
        return 0

    if args.subcommand == "uninstall-opencode":
        target_dir = Path(args.target_dir) if args.target_dir else None
        _print_json(uninstall_from_opencode(target_dir=target_dir, is_project=args.project))
        return 0

    if args.subcommand == "install-antigravity":
        target_dir = Path(args.target_dir) if args.target_dir else None
        default_ws = Path(args.workspace) if args.workspace else None
        info = install_to_antigravity(
            target_dir=target_dir,
            is_project=args.project,
            agy_bin=settings.agy_bin,
            default_workspace=default_ws,
            dry_run=args.dry_run,
        )
        _print_json(info)
        return 0

    if args.subcommand == "uninstall-antigravity":
        target_dir = Path(args.target_dir) if args.target_dir else None
        _print_json(uninstall_from_antigravity(target_dir=target_dir, is_project=args.project))
        return 0

    if args.subcommand == "web-login":
        endpoint = await ensure_cdp_browser_running(cdp_port=args.port)
        _print_json(
            {
                "status": "CHROME_CDP_READY",
                "cdp_endpoint": endpoint,
                "profile_dir": str(default_chrome_profile_dir()),
                "url": "https://gemini.google.com/app",
                "message": "Cửa sổ Chrome đã mở. Hãy đăng nhập tài khoản Google Pro của bạn 1 lần trên cửa sổ này để lưu phiên tự động.",
            }
        )
        return 0

    if args.subcommand == "web-ask":
        res = await query_gemini_web_cdp(
            prompt=args.prompt,
            cdp_port=args.port,
            timeout_sec=args.timeout,
            pool=runner.pool,
        )
        _print_json(res)
        return 0 if res.get("status") == "SUCCESS" else 1

    if args.subcommand == "account-status":
        _print_json(runner.pool.get_pool_status())
        return 0

    if args.subcommand == "account-add":
        acc = runner.pool.add_or_update_account(
            account_id=args.account_id,
            email=args.email,
            cdp_port=args.port,
            limit_5h_tokens=args.limit_5h,
            limit_weekly_tokens=args.limit_weekly,
        )
        _print_json(
            {
                "status": "ACCOUNT_REGISTERED",
                "account": acc.compute_window_metrics(),
                "pool_status": runner.pool.get_pool_status(),
            }
        )
        return 0

    if args.subcommand == "account-swap":
        swap_res = runner.pool.swap_to_next_account(
            target_account_id=args.to,
            reason="cli_account_swap",
        )
        if args.open_browser:
            active_acc = runner.pool.get_active_account()
            endpoint = await ensure_cdp_browser_running(
                cdp_port=active_acc.cdp_port,
                profile_dir=Path(active_acc.chrome_profile_dir),
            )
            swap_res["cdp_endpoint"] = endpoint
        _print_json(swap_res)
        return 0

    if args.subcommand == "account-login":
        acc = runner.pool.accounts.get(args.account_id)
        if acc is None:
            acc = runner.pool.add_or_update_account(account_id=args.account_id)
        endpoint = await ensure_cdp_browser_running(
            cdp_port=acc.cdp_port,
            profile_dir=Path(acc.chrome_profile_dir),
        )
        _print_json(
            {
                "status": "ACCOUNT_CHROME_CDP_READY",
                "account_id": acc.account_id,
                "email": acc.email,
                "cdp_port": acc.cdp_port,
                "cdp_endpoint": endpoint,
                "profile_dir": acc.chrome_profile_dir,
                "message": (
                    f"Đã mở cửa sổ Chrome riêng cho `{acc.account_id}` (port {acc.cdp_port}). "
                    "Hãy đăng nhập tài khoản Google tương ứng trên cửa sổ này 1 lần duy nhất."
                ),
            }
        )
        return 0

    if args.subcommand == "setup-web-accounts":
        count = max(1, min(args.count, 10))
        opened_windows: list[dict[str, Any]] = []
        for idx in range(1, count + 1):
            acc_id = f"acc-{idx}"
            is_primary = idx == 1
            acc = runner.pool.add_or_update_account(
                account_id=acc_id,
                email="primary-google-pro (CLI+Web)" if is_primary else f"web-google-acc-{idx}",
                cdp_port=9221 + idx,
                has_agy_argon=is_primary,
            )
            endpoint = None
            if not args.no_open:
                endpoint = await ensure_cdp_browser_running(
                    cdp_port=acc.cdp_port,
                    profile_dir=Path(acc.chrome_profile_dir),
                )
            opened_windows.append(
                {
                    "account_id": acc.account_id,
                    "mode": "cli_and_web" if acc.has_agy_argon else "web_only",
                    "cdp_port": acc.cdp_port,
                    "cdp_endpoint": endpoint,
                    "profile_dir": acc.chrome_profile_dir,
                }
            )
        _print_json(
            {
                "status": "MULTI_ACCOUNT_SETUP_READY",
                "total_accounts": count,
                "accounts": opened_windows,
                "instructions": (
                    "Each account (`acc-1`, `acc-2`, ...) has its own isolated Chrome profile window. "
                    "Sign in to each Google account once in its corresponding window."
                ),
            }
        )
        return 0

    if args.subcommand == "quota":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if args.watch and args.watch > 0:
            try:
                while True:
                    runner.pool._load()
                    rep = runner.pool.get_per_model_quota_report(include_opencode_stats=True)
                    print("\033[2J\033[H", end="")
                    print(format_per_model_quota_table(rep))
                    print(f"\n  [Live Refresh every {args.watch}s — Press Ctrl+C to stop]")
                    await asyncio.sleep(max(1, args.watch))
            except KeyboardInterrupt:
                return 0
        rep = runner.pool.get_per_model_quota_report(include_opencode_stats=True)
        if args.json:
            _print_json(rep)
        else:
            print(format_per_model_quota_table(rep))
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
