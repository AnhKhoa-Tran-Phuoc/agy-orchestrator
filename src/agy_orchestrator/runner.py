"""Asynchronous headless runner for Google Antigravity CLI (`agy`) workers."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Literal
import uuid

from agy_orchestrator.account_pool import AccountPoolManager, classify_quota_window_error
from agy_orchestrator.config import OrchestratorSettings
from agy_orchestrator.gemini_web_cdp import extract_and_write_web_files, query_gemini_web_cdp
from agy_orchestrator.models import ModelRegistry, TaskTier

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
ExecutionMode = Literal["accept-edits", "plan", "auto"]


@dataclass(slots=True)
class WorkerTaskSpec:
    """Specification for a single task delegated to an Antigravity worker."""

    prompt: str
    workspace_dir: str | Path | None = None
    tier: TaskTier = "coding"
    model: str | None = None
    effort: EffortLevel | None = None
    mode: ExecutionMode | None = None
    conversation_id: str | None = None
    output_files: list[str] | None = None
    add_dirs: list[str] | None = None
    allow_shell: bool = False
    concise_report: bool = True
    timeout_sec: int | None = None
    max_summary_chars: int | None = None


@dataclass(slots=True)
class WorkerRunResult:
    """Bounded, low-token result returned to the ChatGPT/Codex Manager."""

    run_id: str
    status: str
    conversation_id: str | None
    model_used: str
    tier: str
    workspace_dir: str
    duration_seconds: float
    num_turns: int
    antigravity_usage: dict[str, int]
    estimated_manager_tokens: int
    git_changes_summary: str
    worker_summary: str
    full_output_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    active_account_id: str | None = None
    account_swapped: bool = False

    def to_compact_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.warnings:
            payload.pop("warnings", None)
        if not self.full_output_path:
            payload.pop("full_output_path", None)
        if not self.git_changes_summary:
            payload.pop("git_changes_summary", None)
        if not self.account_swapped:
            payload.pop("account_swapped", None)
        if not self.active_account_id:
            payload.pop("active_account_id", None)
        return payload


def parse_agy_json_stdout(raw_stdout: str, raw_stderr: str = "") -> tuple[dict[str, Any], list[str]]:
    """Parse `agy --output-format json` stdout, tolerating leading warning/banner lines."""
    warnings: list[str] = []
    text = raw_stdout.strip()

    if raw_stderr.strip():
        for line in raw_stderr.strip().splitlines():
            cleaned = line.strip()
            if cleaned:
                warnings.append(cleaned)

    if not text:
        return (
            {
                "status": "ERROR",
                "response": "Antigravity CLI produced empty stdout.",
                "usage": {},
            },
            warnings,
        )

    # Fast path: entire stdout is a single JSON object
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, warnings
    except json.JSONDecodeError:
        pass

    # Fallback: scan lines from bottom up for the JSON result object, collecting warnings
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    json_index: int | None = None
    parsed_obj: dict[str, Any] | None = None

    for idx in range(len(lines) - 1, -1, -1):
        candidate = lines[idx]
        if candidate.startswith("{") and candidate.endswith("}"):
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    json_index = idx
                    parsed_obj = obj
                    break
            except json.JSONDecodeError:
                continue

    if parsed_obj is not None and json_index is not None:
        for extra_line in lines[:json_index]:
            warnings.append(extra_line)
        return parsed_obj, warnings

    return (
        {
            "status": "ERROR",
            "response": text,
            "usage": {},
        },
        warnings,
    )


def build_worker_prompt(
    task_prompt: str,
    output_files: list[str] | None = None,
    concise_report: bool = True,
    is_continuation: bool = False,
) -> str:
    """Wrap the Manager's task prompt with quota-protecting worker rules."""
    sections: list[str] = []

    if is_continuation:
        sections.append("[MANAGER REVIEW / FOLLOW-UP INSTRUCTION]")
    else:
        sections.append("[DELEGATED WORKER TASK]")

    sections.append(task_prompt.strip())

    if output_files:
        formatted_files = ", ".join(f"`{p}`" for p in output_files)
        sections.append(
            f"\n[TARGET DELIVERABLE FILES]\nWrite or update the output directly in: {formatted_files}."
        )

    if concise_report:
        sections.append(
            "\n[REPORTING RULES FOR MANAGER]\n"
            "- Perform all code/content edits directly on disk inside the workspace.\n"
            "- Do NOT paste full file contents into your final reply unless no file path applies.\n"
            "- End with a concise bullet summary (max 8 bullets): files created/modified, "
            "key implementation notes, and any remaining risks."
        )

    return "\n".join(sections)


def truncate_for_manager(text: str, max_chars: int, full_output_path: Path | None) -> tuple[str, bool]:
    """Ensure the worker's response never floods the ChatGPT Manager's context window."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned, False

    half = max(200, (max_chars - 180) // 2)
    head = cleaned[:half].rstrip()
    tail = cleaned[-half:].lstrip()
    omitted = len(cleaned) - len(head) - len(tail)
    pointer = f" (full output saved at {full_output_path})" if full_output_path else ""
    truncated = f"{head}\n\n...[truncated {omitted} chars to save Manager quota{pointer}]...\n\n{tail}"
    return truncated, True


async def _capture_git_summary(workspace: Path) -> str:
    """Capture a tiny git status + diff stat summary if `workspace` is inside a Git repo."""
    if not (workspace / ".git").exists():
        # Check parent directories quickly
        has_git = any((parent / ".git").exists() for parent in workspace.parents)
        if not has_git:
            return ""

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--short",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return ""
        status_out = stdout_b.decode("utf-8", errors="replace").strip()
        if not status_out:
            return "Clean working tree (no uncommitted changes)."
        lines = status_out.splitlines()
        if len(lines) > 20:
            status_out = "\n".join(lines[:20]) + f"\n... (+{len(lines) - 20} more files)"
        return status_out
    except Exception:
        return ""


class AntigravityWorkerRunner:
    """Executes single, resumed, and parallel tasks via `agy` headless CLI."""

    def __init__(
        self,
        settings: OrchestratorSettings | None = None,
        registry: ModelRegistry | None = None,
        pool: AccountPoolManager | None = None,
    ) -> None:
        self.settings = settings or OrchestratorSettings()
        self.registry = registry or ModelRegistry(self.settings.agy_bin)
        self.pool = pool or AccountPoolManager()

    def _state_dir(self, workspace: Path) -> Path:
        state_dir = workspace / self.settings.state_dir_name
        (state_dir / "runs").mkdir(parents=True, exist_ok=True)
        return state_dir

    def _append_ledger(self, workspace: Path, result: WorkerRunResult) -> None:
        state_dir = self._state_dir(workspace)
        ledger_file = state_dir / "ledger.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": result.run_id,
            "account_id": result.active_account_id,
            "account_swapped": result.account_swapped,
            "conversation_id": result.conversation_id,
            "status": result.status,
            "model_used": result.model_used,
            "tier": result.tier,
            "duration_seconds": round(result.duration_seconds, 3),
            "num_turns": result.num_turns,
            "antigravity_total_tokens": int(result.antigravity_usage.get("total_tokens", 0)),
            "antigravity_input_tokens": int(result.antigravity_usage.get("input_tokens", 0)),
            "antigravity_output_tokens": int(result.antigravity_usage.get("output_tokens", 0)),
            "antigravity_cache_read_tokens": int(result.antigravity_usage.get("cache_read_tokens", 0)),
            "estimated_manager_tokens": result.estimated_manager_tokens,
        }
        with ledger_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_quota_stats(self, workspace_dir: str | Path | None = None) -> dict[str, Any]:
        """Compute cumulative token offloading statistics from `.agy-orch/ledger.jsonl` and account pool."""
        workspace = self.settings.resolve_workspace(workspace_dir)
        ledger_file = workspace / self.settings.state_dir_name / "ledger.jsonl"
        pool_status = self.pool.get_pool_status()
        if not ledger_file.is_file():
            return {
                "workspace_dir": str(workspace),
                "total_runs": 0,
                "antigravity_total_tokens": 0,
                "estimated_manager_tokens": 0,
                "offload_percentage": 0.0,
                "by_model": {},
                "account_pool": pool_status,
            }

        total_runs = 0
        agy_tokens = 0
        mgr_tokens = 0
        by_model: dict[str, dict[str, int]] = {}

        for raw_line in ledger_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            total_runs += 1
            run_agy = int(rec.get("antigravity_total_tokens", 0))
            run_mgr = int(rec.get("estimated_manager_tokens", 0))
            model_name = str(rec.get("model_used", "unknown"))

            agy_tokens += run_agy
            mgr_tokens += run_mgr

            bucket = by_model.setdefault(model_name, {"runs": 0, "total_tokens": 0})
            bucket["runs"] += 1
            bucket["total_tokens"] += run_agy

        combined = agy_tokens + mgr_tokens
        offload_pct = round((agy_tokens / combined) * 100.0, 2) if combined > 0 else 0.0

        return {
            "workspace_dir": str(workspace),
            "total_runs": total_runs,
            "antigravity_total_tokens": agy_tokens,
            "estimated_manager_tokens": mgr_tokens,
            "offload_percentage": offload_pct,
            "by_model": by_model,
            "account_pool": pool_status,
        }

    def build_cli_args(self, spec: WorkerTaskSpec, resolved_model: str, wrapped_prompt: str) -> list[str]:
        """Construct the exact command-line invocation for `agy` headless execution."""
        args: list[str] = [
            str(self.settings.agy_bin),
            "--model",
            resolved_model,
            "--output-format",
            "json",
            "--disable-slash-commands",
        ]

        mode = spec.mode or self.settings.default_mode
        if mode:
            args.extend(["--mode", mode])

        if spec.effort:
            args.extend(["--effort", spec.effort])

        if spec.conversation_id:
            args.extend(["--conversation", spec.conversation_id.strip()])

        if spec.add_dirs:
            for extra_dir in spec.add_dirs:
                args.extend(["--add-dir", str(Path(extra_dir).expanduser().resolve())])

        if spec.allow_shell:
            args.extend(["--dangerously-skip-permissions", "--sandbox"])

        timeout_sec = spec.timeout_sec or self.settings.default_timeout_sec
        if timeout_sec > 0:
            args.extend(["--print-timeout", f"{timeout_sec}s"])

        args.extend(["-p", wrapped_prompt])
        return args

    async def run_task(self, spec: WorkerTaskSpec) -> WorkerRunResult:
        """Execute a single task on an Antigravity worker model and auto-swap account on 5h/weekly quota."""
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        workspace = self.settings.resolve_workspace(spec.workspace_dir)

        # 1. Pre-check 5-hour & weekly quota on active account; auto-swap if >= 85% or in cooldown
        pre_check = self.pool.ensure_healthy_account()
        account_swapped = bool(pre_check.get("swapped"))
        active_acc_id = self.pool.active_account_id

        resolved_model = await self.registry.resolve_model(
            tier=spec.tier,
            explicit_model=spec.model,
        )

        wrapped_prompt = build_worker_prompt(
            task_prompt=spec.prompt,
            output_files=spec.output_files,
            concise_report=spec.concise_report,
            is_continuation=bool(spec.conversation_id),
        )
        cmd_args = self.build_cli_args(spec, resolved_model, wrapped_prompt)
        timeout_sec = spec.timeout_sec or self.settings.default_timeout_sec
        max_chars = spec.max_summary_chars or self.settings.max_summary_chars

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec + 15,
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = await proc.communicate()

        raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
        raw_stderr = stderr_bytes.decode("utf-8", errors="replace")

        if timed_out:
            parsed: dict[str, Any] = {
                "status": "TIMEOUT",
                "conversation_id": spec.conversation_id,
                "response": f"Worker timed out after {timeout_sec}s.",
                "duration_seconds": float(timeout_sec),
                "num_turns": 0,
                "usage": {},
            }
            warnings = [line for line in raw_stderr.splitlines() if line.strip()]
        else:
            parsed, warnings = parse_agy_json_stdout(raw_stdout, raw_stderr)

        raw_response = str(parsed.get("response") or "").strip()
        state_dir = self._state_dir(workspace)
        full_md_path = state_dir / "runs" / f"{run_id}.md"

        worker_summary, was_truncated = truncate_for_manager(
            raw_response,
            max_chars=max_chars,
            full_output_path=full_md_path,
        )

        saved_path_str: str | None = None
        if was_truncated or len(raw_response) > 600:
            full_md_path.write_text(raw_response, encoding="utf-8")
            saved_path_str = str(full_md_path)

        raw_usage = parsed.get("usage")
        usage_dict: dict[str, int] = {}
        if isinstance(raw_usage, dict):
            for k, v in raw_usage.items():
                if isinstance(v, (int, float)):
                    usage_dict[k] = int(v)

        git_summary = await _capture_git_summary(workspace)
        status_str = str(parsed.get("status") or ("SUCCESS" if proc.returncode == 0 else "ERROR"))

        # 2. Record 5h & weekly rolling token usage on the active account
        total_tok = int(usage_dict.get("total_tokens", 0))
        if total_tok > 0:
            self.pool.record_usage(
                tokens=total_tok,
                model=resolved_model,
                account_id=active_acc_id,
            )

        # 3. Check if this run hit a 5-hour or weekly quota error or crossed the 85% threshold
        quota_err_window = classify_quota_window_error(raw_response, warnings)
        is_argon_model = "argon" in resolved_model.lower()
        if status_str.upper() != "SUCCESS" and quota_err_window is not None:
            if is_argon_model:
                # Do NOT mark acc-1's 3.8 Flash quota exhausted when only Argon CLI hits its limit!
                warnings.append(
                    f"[ARGON-CLI-QUOTA] Account `{active_acc_id}` hit {quota_err_window} Argon CLI limit on `{resolved_model}`; "
                    "3.8 Flash on Antigravity CLI remains active."
                )
            else:
                self.pool.mark_account_exhausted(
                    window_type=quota_err_window,
                    account_id=active_acc_id,
                    channel="cli_flash",
                )
                swap_res = self.pool.swap_to_next_account(
                    reason=f"agy_flash_{quota_err_window}_quota_exhausted"
                )
                if swap_res.get("swapped"):
                    account_swapped = True
                    active_acc_id = str(swap_res.get("to_account", active_acc_id))
                    warnings.append(
                        f"[AUTO-SWAP] Account `{swap_res.get('from_account')}` hit {quota_err_window} Flash quota; "
                        f"rotated Antigravity CLI to `{active_acc_id}`."
                    )

                # Upgrade 3: Hands-free fallback to 3-Account Gemini Web CDP Pool for Worker Flash tasks
                web_prompt = spec.prompt.strip()
                if spec.output_files:
                    file_tags_instr = "\n".join(
                        f'<file path="{rel}">\n...full file content...\n</file>'
                        for rel in spec.output_files
                    )
                    web_prompt += (
                        "\n\n[WEB FALLBACK DELIVERABLE FORMAT]\n"
                        "Output each requested deliverable file inside exact XML tags so the orchestrator "
                        "can write them directly to disk:\n"
                        f"{file_tags_instr}\n"
                        "After the `<file>` blocks, provide a concise bullet summary."
                    )
                try:
                    web_res = await query_gemini_web_cdp(
                        prompt=web_prompt,
                        timeout_sec=min(timeout_sec, 180),
                        pool=self.pool,
                        new_chat=True,
                        prefer_pro_mode=False,
                    )
                    if web_res.get("status") == "SUCCESS":
                        web_text = str(web_res.get("worker_summary") or "").strip()
                        written_files = extract_and_write_web_files(
                            response_text=web_text,
                            workspace=workspace,
                            output_files=spec.output_files,
                        )
                        status_str = "SUCCESS"
                        resolved_model = f"{resolved_model}->gemini-web-cdp({web_res.get('account_id', 'web')})"
                        active_acc_id = str(web_res.get("account_id") or active_acc_id)
                        account_swapped = True
                        raw_response = web_text
                        worker_summary, was_truncated = truncate_for_manager(
                            raw_response,
                            max_chars=max_chars,
                            full_output_path=full_md_path,
                        )
                        if was_truncated or len(raw_response) > 600:
                            full_md_path.write_text(raw_response, encoding="utf-8")
                            saved_path_str = str(full_md_path)
                        git_summary = await _capture_git_summary(workspace)
                        warnings.append(
                            f"[WEB-CDP-FALLBACK] Antigravity CLI hit {quota_err_window} Flash quota; "
                            f"completed task via Gemini Web CDP (`{active_acc_id}`) and wrote {len(written_files)} file(s): {written_files}."
                        )
                except Exception as exc:
                    warnings.append(f"[WEB-CDP-FALLBACK-ERROR] {exc}")
        else:
            post_check = self.pool.ensure_healthy_account()
            if post_check.get("swapped"):
                account_swapped = True
                active_acc_id = str(post_check.get("to_account", active_acc_id))
                warnings.append(
                    f"[AUTO-SWAP] Account `{post_check.get('from_account')}` reached {post_check.get('reason')}; "
                    f"rotated to `{active_acc_id}` for subsequent runs."
                )

        approx_mgr_chars = len(worker_summary) + len(git_summary) + 160
        est_mgr_tokens = max(1, approx_mgr_chars // 4)

        result = WorkerRunResult(
            run_id=run_id,
            status=status_str,
            conversation_id=parsed.get("conversation_id") or spec.conversation_id,
            model_used=resolved_model,
            tier=spec.tier,
            workspace_dir=str(workspace),
            duration_seconds=round(float(parsed.get("duration_seconds") or 0.0), 3),
            num_turns=int(parsed.get("num_turns") or 0),
            antigravity_usage=usage_dict,
            estimated_manager_tokens=est_mgr_tokens,
            git_changes_summary=git_summary,
            worker_summary=worker_summary,
            full_output_path=saved_path_str,
            warnings=warnings,
            active_account_id=active_acc_id,
            account_swapped=account_swapped,
        )

        self._append_ledger(workspace, result)
        return result

    async def run_parallel_tasks(
        self,
        specs: list[WorkerTaskSpec],
        max_concurrency: int | None = None,
    ) -> list[WorkerRunResult]:
        """Run multiple independent worker tasks concurrently to maximize throughput."""
        limit = max(1, min(max_concurrency or self.settings.max_parallel_workers, 6))
        semaphore = asyncio.Semaphore(limit)

        async def _bounded_run(item: WorkerTaskSpec) -> WorkerRunResult:
            async with semaphore:
                return await self.run_task(item)

        return list(await asyncio.gather(*(_bounded_run(s) for s in specs)))
