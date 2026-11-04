"""MCP Server exposing Antigravity CLI workers and low-token Manager inspection tools."""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from agy_orchestrator import __version__
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.inspector import (
    inspect_workspace_changes as _inspect_changes,
    read_file_excerpt as _read_excerpt,
    run_verification_check as _run_check,
)
from agy_orchestrator.models import TIER_DESCRIPTIONS, ModelRegistry, TaskTier
from agy_orchestrator.runner import (
    AntigravityWorkerRunner,
    EffortLevel,
    ExecutionMode,
    WorkerTaskSpec,
)

# Ensure logging never touches stdout (reserved strictly for MCP stdio JSON-RPC frames)
logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stderr,
    format="[agy-orchestrator] %(levelname)s: %(message)s",
)

MANAGER_SYSTEM_INSTRUCTIONS = """\
You are connected to `antigravity_workers`, a Manager-Worker orchestration bridge backed by Google Antigravity CLI (`agy`).

To conserve your own context window and message quota:
1. Act as the Manager, Architect, and Quality Inspector. Delegate coding, writing, documentation, boilerplate, and refactoring tasks to `delegate_task` or `delegate_parallel_tasks`.
2. Pick the most quota-efficient `tier`:
   - `trivial`: formatting, renaming, conversions, reading/summarizing huge files (uses Flash Low).
   - `writing`: articles, docs, READMEs, tutorials, Vietnamese/English copy (uses Flash High).
   - `coding`: multi-file features, unit tests, standard refactors (uses Argon Medium / Sonnet Medium).
   - `complex`: difficult bugs, concurrency, intricate algorithms (uses Argon High / Sonnet High / Opus).
   Or pass an explicit `model` ID from `list_worker_models`.
3. Audit deliverables using `inspect_workspace_changes`, `read_file_excerpt`, and `run_verification_check` rather than loading entire large files into context.
4. When changes need fixes after inspection, call `continue_worker_session` with the worker's `conversation_id` so Antigravity reuses its cached session context.
"""


class ParallelTaskItem(BaseModel):
    """A single independent task inside a parallel worker batch."""

    prompt: str = Field(description="Clear, self-contained instructions and acceptance criteria for the worker.")
    tier: TaskTier = Field(
        default="coding",
        description="Task complexity tier: 'trivial', 'writing', 'coding', or 'complex'.",
    )
    model: str | None = Field(
        default=None,
        description="Optional explicit Antigravity model ID or shorthand (e.g. 'gemini-3.8-flash-high', 'sonnet-medium').",
    )
    output_files: list[str] | None = Field(
        default=None,
        description="Target file paths (relative to workspace_dir) that this worker owns and should create/edit.",
    )
    effort: EffortLevel | None = Field(
        default=None,
        description="Optional reasoning effort override ('low', 'medium', 'high', 'xhigh', 'max').",
    )
    allow_shell: bool = Field(
        default=False,
        description="Set True only if the worker itself must execute sandboxed terminal commands.",
    )


def create_mcp_server(settings: OrchestratorSettings | None = None) -> MCPServer:
    """Instantiate and wire the `antigravity_workers` MCP server."""
    cfg = settings or OrchestratorSettings()
    registry = ModelRegistry(cfg.agy_bin)
    runner = AntigravityWorkerRunner(settings=cfg, registry=registry)

    mcp = MCPServer(
        name="antigravity_workers",
        title="Antigravity Worker Orchestrator",
        description="Delegates heavy coding and writing workloads to Google Antigravity CLI models while keeping Manager reviews ultra-compact.",
        instructions=MANAGER_SYSTEM_INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
    )

    @mcp.tool(
        description=(
            "List all AI models available in the local Antigravity CLI account (`agy models`), "
            "along with task-tier routing descriptions ('trivial', 'writing', 'coding', 'complex')."
        )
    )
    async def list_worker_models(refresh: bool = False) -> dict[str, Any]:
        models = await registry.list_models(refresh=refresh)
        return {
            "agy_binary": str(cfg.agy_bin),
            "tier_guide": TIER_DESCRIPTIONS,
            "default_models_by_tier": {
                tier_name: registry.resolve_from_catalog(models, tier=tier_name)
                for tier_name in ("trivial", "writing", "coding", "complex")
            },
            "available_models": [m.to_dict() for m in models],
        }

    @mcp.tool(
        description=(
            "Delegate a coding, writing, refactoring, or file-summarization task to a Google Antigravity "
            "worker model. The worker edits files directly inside `workspace_dir` and returns a compact "
            "digest plus `conversation_id` so your own context stays tiny."
        )
    )
    async def delegate_task(
        prompt: str,
        workspace_dir: str | None = None,
        tier: TaskTier = "coding",
        model: str | None = None,
        output_files: list[str] | None = None,
        effort: EffortLevel | None = None,
        mode: ExecutionMode = "accept-edits",
        allow_shell: bool = False,
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        spec = WorkerTaskSpec(
            prompt=prompt,
            workspace_dir=workspace_dir,
            tier=tier,
            model=model,
            output_files=output_files,
            effort=effort,
            mode=mode,
            allow_shell=allow_shell,
            timeout_sec=timeout_sec,
        )
        result = await runner.run_task(spec)
        return result.to_compact_dict()

    @mcp.tool(
        description=(
            "Resume an existing Antigravity worker session by `conversation_id` to apply review feedback, "
            "fix failing tests, or refine written content without re-explaining prior context."
        )
    )
    async def continue_worker_session(
        conversation_id: str,
        feedback_prompt: str,
        workspace_dir: str | None = None,
        tier: TaskTier = "coding",
        model: str | None = None,
        output_files: list[str] | None = None,
        allow_shell: bool = False,
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        spec = WorkerTaskSpec(
            prompt=feedback_prompt,
            workspace_dir=workspace_dir,
            tier=tier,
            model=model,
            conversation_id=conversation_id,
            output_files=output_files,
            allow_shell=allow_shell,
            timeout_sec=timeout_sec,
        )
        result = await runner.run_task(spec)
        return result.to_compact_dict()

    @mcp.tool(
        description=(
            "Dispatch multiple independent tasks to Antigravity workers in parallel. "
            "Ensure each task writes to disjoint `output_files` to avoid edit conflicts."
        )
    )
    async def delegate_parallel_tasks(
        tasks: list[ParallelTaskItem],
        workspace_dir: str | None = None,
        max_concurrency: int = 3,
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        specs = [
            WorkerTaskSpec(
                prompt=item.prompt,
                workspace_dir=workspace_dir,
                tier=item.tier,
                model=item.model,
                output_files=item.output_files,
                effort=item.effort,
                allow_shell=item.allow_shell,
                timeout_sec=timeout_sec,
            )
            for item in tasks
        ]
        results = await runner.run_parallel_tasks(specs, max_concurrency=max_concurrency)
        total_agy_tokens = sum(int(r.antigravity_usage.get("total_tokens", 0)) for r in results)
        return {
            "batch_size": len(results),
            "succeeded": sum(1 for r in results if r.status == "SUCCESS"),
            "total_antigravity_tokens": total_agy_tokens,
            "results": [r.to_compact_dict() for r in results],
        }

    @mcp.tool(
        description=(
            "Inspect workspace modifications with minimal token cost. Returns `git status`, `git diff --stat`, "
            "and a bounded unified diff preview (or a recent-files snapshot if not a Git repository)."
        )
    )
    async def inspect_workspace_changes(
        workspace_dir: str | None = None,
        file_path: str | None = None,
        include_patch: bool = True,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        target = cfg.resolve_workspace(workspace_dir)
        return await _inspect_changes(
            workspace_dir=target,
            file_path=file_path,
            include_patch=include_patch,
            max_chars=max_chars,
        )

    @mcp.tool(
        description=(
            "Read a bounded line-numbered excerpt (`start_line`..`end_line`) of a file to spot-check code "
            "or prose quality without loading the entire file into your context."
        )
    )
    def read_file_excerpt(
        file_path: str,
        start_line: int = 1,
        end_line: int = 100,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        return _read_excerpt(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )

    @mcp.tool(
        description=(
            "Execute a test suite, linter, build check, or validation script inside `workspace_dir` "
            "and return the pass/fail status plus a bounded log digest."
        )
    )
    async def run_verification_check(
        command: str,
        workspace_dir: str | None = None,
        timeout_sec: int = 120,
        max_output_chars: int = 3000,
    ) -> dict[str, Any]:
        target = cfg.resolve_workspace(workspace_dir)
        return await _run_check(
            workspace_dir=target,
            command=command,
            timeout_sec=timeout_sec,
            max_output_chars=max_output_chars,
        )

    @mcp.tool(
        description=(
            "Return cumulative quota-offloading statistics showing how many tokens were executed "
            "by Antigravity worker models versus compact summaries returned to the Manager."
        )
    )
    def get_quota_savings_report(workspace_dir: str | None = None) -> dict[str, Any]:
        return runner.get_quota_stats(workspace_dir=workspace_dir)

    @mcp.prompt(
        name="manager_mode",
        title="Manager Mode (Delegate to Antigravity Workers)",
        description="Instruct ChatGPT/Codex to plan, delegate execution to Antigravity models, and verify changes.",
    )
    def manager_mode(goal: str, workspace_dir: str = "") -> str:
        ws_clause = f"\nTarget workspace directory: `{workspace_dir}`" if workspace_dir else ""
        return (
            f"Act strictly as the Engineering/Content Manager for the following goal:{ws_clause}\n\n"
            f"Goal:\n{goal}\n\n"
            "Instructions:\n"
            "1. Outline a brief task breakdown and assign each task an Antigravity `tier` "
            "(`trivial`, `writing`, `coding`, or `complex`).\n"
            "2. Delegate ALL heavy file writing, coding, and prose generation via `delegate_task` "
            "or `delegate_parallel_tasks`—do NOT write full files yourself.\n"
            "3. Inspect deliverables with `inspect_workspace_changes` / `read_file_excerpt` and "
            "validate with `run_verification_check` where applicable.\n"
            "4. If anything needs adjustment, call `continue_worker_session` with its `conversation_id`.\n"
            "5. Report final results and `get_quota_savings_report` concisely."
        )

    return mcp


def run_stdio() -> None:
    """Entrypoint for stdio MCP transport (used by ChatGPT Desktop & Codex CLI)."""
    server = create_mcp_server()
    server.run(transport="stdio")


def run_http(
    host: str = "127.0.0.1",
    port: int = 8765,
    transport: Literal["streamable-http", "sse"] = "streamable-http",
) -> None:
    """Entrypoint for Streamable HTTP / SSE MCP transport."""
    server = create_mcp_server()
    server.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    run_stdio()
