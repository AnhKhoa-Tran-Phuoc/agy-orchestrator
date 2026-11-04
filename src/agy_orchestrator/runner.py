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
from agy_orchestrator.inspector import run_verification_check
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
    verify_command: str | None = None
    max_reflexion_retries: int = 2
    rollback_on_circuit_breaker: bool = False


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
    git_checkpoint_sha: str | None = None
    verification_passed: bool | None = None
    reflexion_attempts: int = 0
    circuit_breaker_tripped: bool = False

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
        if not self.git_checkpoint_sha:
            payload.pop("git_checkpoint_sha", None)
        if self.verification_passed is None:
            payload.pop("verification_passed", None)
        if not self.reflexion_attempts:
            payload.pop("reflexion_attempts", None)
        if not self.circuit_breaker_tripped:
            payload.pop("circuit_breaker_tripped", None)
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


def load_project_state_digest(
    workspace: Path | None,
    state_dir_name: str = ".agy-orch",
    max_chars: int = 700,
) -> str:
    """Load a compact digest of `.agy-orch/STATE.md` (or `STATE.md`) for persistent memory injection."""
    if workspace is None:
        return ""
    candidates = [
        workspace / state_dir_name / "STATE.md",
        workspace / "STATE.md",
    ]
    for path in candidates:
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                if len(text) > max_chars:
                    return text[:max_chars].rstrip() + "\n...[STATE.md truncated]..."
                return text
            except Exception:
                continue
    return ""


def save_project_state(
    workspace: Path,
    summary_markdown: str,
    state_dir_name: str = ".agy-orch",
) -> Path:
    """Persist architectural decisions, milestones, and active tasks to `.agy-orch/STATE.md`."""
    state_dir = workspace / state_dir_name
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "STATE.md"
    state_file.write_text(summary_markdown.strip() + "\n", encoding="utf-8")
    return state_file


def build_worker_prompt(
    task_prompt: str,
    output_files: list[str] | None = None,
    concise_report: bool = True,
    is_continuation: bool = False,
    state_digest: str = "",
) -> str:
    """Wrap the Manager's task prompt with quota-protecting worker rules and optional STATE.md memory."""
    sections: list[str] = []

    if state_digest.strip() and not is_continuation:
        sections.append(
            f"[PROJECT STATE MEMORY (.agy-orch/STATE.md)]\n{state_digest.strip()}\n"
        )

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


def _has_git_repo(workspace: Path) -> bool:
    if (workspace / ".git").exists():
        return True
    return any((parent / ".git").exists() for parent in workspace.parents)


async def _create_git_checkpoint(workspace: Path) -> str | None:
    """Create a non-destructive Git checkpoint SHA (`git stash create` or `HEAD`) before worker edits."""
    if not _has_git_repo(workspace):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "stash",
            "create",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        sha = stdout_b.decode("utf-8", errors="replace").strip()
        if proc.returncode == 0 and sha:
            return sha

        proc_head = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        head_b, _ = await asyncio.wait_for(proc_head.communicate(), timeout=5)
        head_sha = head_b.decode("utf-8", errors="replace").strip()
        if proc_head.returncode == 0 and head_sha:
            return head_sha
    except Exception:
        pass
    return None


async def rollback_to_git_checkpoint(workspace: Path, checkpoint_sha: str) -> dict[str, Any]:
    """Restore tracked files in `workspace` to `checkpoint_sha` (`git checkout <sha> -- .`)."""
    if not checkpoint_sha or not _has_git_repo(workspace):
        return {"rolled_back": False, "reason": "No git repo or checkpoint SHA available."}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "checkout",
            checkpoint_sha.strip(),
            "--",
            ".",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return {"rolled_back": True, "checkpoint_sha": checkpoint_sha.strip()}
        return {
            "rolled_back": False,
            "checkpoint_sha": checkpoint_sha.strip(),
            "reason": stderr_b.decode("utf-8", errors="replace").strip(),
        }
    except Exception as exc:
        return {"rolled_back": False, "checkpoint_sha": checkpoint_sha, "reason": str(exc)}


async def init_or_sync_git_repo(
    workspace: Path,
    repo_name: str | None = None,
    private: bool = True,
    commit_message: str | None = None,
    push: bool = False,
) -> dict[str, Any]:
    """Ensure `workspace` is a Git repo, optionally commit changes, create a GitHub repo via `gh`, and push."""
    workspace.mkdir(parents=True, exist_ok=True)
    initialized_git = False

    if not _has_git_repo(workspace):
        proc_init = await asyncio.create_subprocess_exec(
            "git",
            "init",
            "-b",
            "main",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc_init.communicate(), timeout=10)
        initialized_git = proc_init.returncode == 0

    gitignore = workspace / ".gitignore"
    ignore_rule = ".agy-orch/runs/\n__pycache__/\n.venv/\n"
    if not gitignore.is_file():
        gitignore.write_text(ignore_rule, encoding="utf-8")
    else:
        existing_ig = gitignore.read_text(encoding="utf-8", errors="replace")
        if ".agy-orch/runs/" not in existing_ig:
            gitignore.write_text(existing_ig.rstrip() + "\n.agy-orch/runs/\n", encoding="utf-8")

    committed = False
    commit_sha: str | None = None
    if commit_message:
        proc_add = await asyncio.create_subprocess_exec(
            "git",
            "add",
            "-A",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc_add.communicate(), timeout=10)
        proc_commit = await asyncio.create_subprocess_exec(
            "git",
            "commit",
            "-m",
            commit_message,
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc_commit.communicate(), timeout=15)
        committed = proc_commit.returncode == 0
        head_sha = await _create_git_checkpoint(workspace)
        commit_sha = head_sha

    github_created = False
    remote_url = ""
    if repo_name:
        proc_rem = await asyncio.create_subprocess_exec(
            "git",
            "remote",
            "get-url",
            "origin",
            cwd=str(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rem_b, _ = await asyncio.wait_for(proc_rem.communicate(), timeout=5)
        if proc_rem.returncode == 0 and rem_b.decode("utf-8", errors="replace").strip():
            remote_url = rem_b.decode("utf-8", errors="replace").strip()
        else:
            visibility = "--private" if private else "--public"
            try:
                proc_gh = await asyncio.create_subprocess_exec(
                    "gh",
                    "repo",
                    "create",
                    repo_name,
                    visibility,
                    "--source=.",
                    "--remote=origin",
                    cwd=str(workspace),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                gh_out, _ = await asyncio.wait_for(proc_gh.communicate(), timeout=25)
                if proc_gh.returncode == 0:
                    github_created = True
                    remote_url = gh_out.decode("utf-8", errors="replace").strip()
            except Exception:
                pass

    pushed = False
    if push:
        try:
            proc_push = await asyncio.create_subprocess_exec(
                "git",
                "push",
                "-u",
                "origin",
                "HEAD",
                cwd=str(workspace),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc_push.communicate(), timeout=30)
            pushed = proc_push.returncode == 0
        except Exception:
            pushed = False

    return {
        "workspace_dir": str(workspace),
        "initialized_git": initialized_git,
        "committed": committed,
        "commit_sha": commit_sha,
        "github_repo_created": github_created,
        "remote_url": remote_url,
        "pushed": pushed,
    }


def partition_non_overlapping_waves(
    specs: list[WorkerTaskSpec],
) -> list[list[tuple[int, WorkerTaskSpec, list[str]]]]:
    """Partition parallel tasks into waves so no two tasks in the same wave touch the same output file."""
    waves: list[list[tuple[int, WorkerTaskSpec, list[str]]]] = []
    wave_claimed_files: list[set[str]] = []

    for idx, spec in enumerate(specs):
        norm_files = {
            p.strip().replace("\\", "/").lstrip("./").lower()
            for p in (spec.output_files or [])
            if p and p.strip()
        }
        placed = False
        for wave_idx, claimed in enumerate(wave_claimed_files):
            overlap = sorted(norm_files.intersection(claimed))
            if not overlap:
                claimed.update(norm_files)
                overlap_with_prev: list[str] = []
                if wave_idx > 0:
                    prev_claimed = wave_claimed_files[wave_idx - 1]
                    overlap_with_prev = sorted(norm_files.intersection(prev_claimed))
                waves[wave_idx].append((idx, spec, overlap_with_prev))
                placed = True
                break
        if not placed:
            overlap_with_prev = []
            if wave_claimed_files:
                overlap_with_prev = sorted(norm_files.intersection(wave_claimed_files[-1]))
            wave_claimed_files.append(set(norm_files))
            waves.append([(idx, spec, overlap_with_prev)])

    return waves


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

    async def _execute_single_cli_pass(
        self,
        spec: WorkerTaskSpec,
        workspace: Path,
        resolved_model: str,
        wrapped_prompt: str,
        timeout_sec: int,
    ) -> tuple[dict[str, Any], list[str], int | None]:
        """Run a single `agy` CLI subprocess pass and parse its JSON stdout."""
        cmd_args = self.build_cli_args(spec, resolved_model, wrapped_prompt)
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

        return parsed, warnings, proc.returncode

    async def run_task(self, spec: WorkerTaskSpec) -> WorkerRunResult:
        """Execute a single task on an Antigravity worker model with Git checkpoint, Reflexion, and Circuit Breaker."""
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        workspace = self.settings.resolve_workspace(spec.workspace_dir)

        # 0. Capture non-destructive Git checkpoint SHA before worker edits & load STATE.md digest
        checkpoint_sha = await _create_git_checkpoint(workspace)
        state_digest = load_project_state_digest(workspace, self.settings.state_dir_name)

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
            state_digest=state_digest,
        )
        timeout_sec = spec.timeout_sec or self.settings.default_timeout_sec
        max_chars = spec.max_summary_chars or self.settings.max_summary_chars

        parsed, warnings, returncode = await self._execute_single_cli_pass(
            spec=spec,
            workspace=workspace,
            resolved_model=resolved_model,
            wrapped_prompt=wrapped_prompt,
            timeout_sec=timeout_sec,
        )

        raw_response = str(parsed.get("response") or "").strip()
        state_dir = self._state_dir(workspace)
        full_md_path = state_dir / "runs" / f"{run_id}.md"

        raw_usage = parsed.get("usage")
        usage_dict: dict[str, int] = {}
        if isinstance(raw_usage, dict):
            for k, v in raw_usage.items():
                if isinstance(v, (int, float)):
                    usage_dict[k] = int(v)

        status_str = str(parsed.get("status") or ("SUCCESS" if returncode == 0 else "ERROR"))

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

                # Hands-free fallback to 3-Account Gemini Web CDP Pool for Worker Flash tasks
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

        # 4. Closed-Loop Worker Reflexion & Circuit Breaker when `spec.verify_command` is provided
        verification_passed: bool | None = None
        reflexion_attempts = 0
        circuit_breaker_tripped = False
        conv_id = str(parsed.get("conversation_id") or spec.conversation_id or "").strip() or None

        if spec.verify_command and status_str.upper() == "SUCCESS":
            prev_failure_sig = ""
            max_retries = max(0, int(spec.max_reflexion_retries))
            while True:
                v_res = await run_verification_check(
                    workspace_dir=workspace,
                    command=spec.verify_command,
                    timeout_sec=min(120, timeout_sec),
                    max_output_chars=2000,
                )
                if v_res.get("passed"):
                    verification_passed = True
                    break

                verification_passed = False
                fail_digest = str(v_res.get("output_digest") or "").strip()
                failure_sig = fail_digest[:400]

                if reflexion_attempts >= max_retries or (prev_failure_sig and failure_sig == prev_failure_sig):
                    circuit_breaker_tripped = True
                    status_str = "CIRCUIT_BREAKER_TRIPPED"
                    reason = (
                        "identical error repeated consecutively"
                        if (prev_failure_sig and failure_sig == prev_failure_sig)
                        else f"exceeded max_reflexion_retries={max_retries}"
                    )
                    warnings.append(
                        f"[CIRCUIT-BREAKER] Verification `{spec.verify_command}` failed ({reason}). Halting worker loop."
                    )
                    if spec.rollback_on_circuit_breaker and checkpoint_sha:
                        rb = await rollback_to_git_checkpoint(workspace, checkpoint_sha)
                        if rb.get("rolled_back"):
                            warnings.append(
                                f"[AUTO-ROLLBACK] Restored workspace to pre-task checkpoint `{checkpoint_sha[:10]}`."
                            )
                    raw_response = (
                        f"{raw_response}\n\n[VERIFICATION FAILED — CIRCUIT BREAKER TRIPPED]\n"
                        f"Command: `{spec.verify_command}`\nOutput:\n{fail_digest}"
                    ).strip()
                    break

                prev_failure_sig = failure_sig
                reflexion_attempts += 1
                reflexion_prompt = (
                    f"[REFLEXION SELF-CORRECTION LOOP — ATTEMPT {reflexion_attempts}/{max_retries}]\n"
                    f"Verification command `{spec.verify_command}` failed with exit_code={v_res.get('exit_code')}.\n"
                    f"Failure log:\n{fail_digest}\n\n"
                    "Instructions:\n"
                    "1. State a 1-sentence root-cause hypothesis explaining why the test/check failed.\n"
                    "2. Fix the bug directly on disk inside the workspace.\n"
                    "3. Summarize the fix in <= 4 bullets."
                )
                retry_spec = WorkerTaskSpec(
                    prompt=reflexion_prompt,
                    workspace_dir=workspace,
                    tier=spec.tier,
                    model=resolved_model.split("->")[0],
                    effort=spec.effort,
                    mode=spec.mode,
                    conversation_id=conv_id,
                    output_files=spec.output_files,
                    add_dirs=spec.add_dirs,
                    allow_shell=spec.allow_shell,
                    concise_report=True,
                    timeout_sec=timeout_sec,
                    max_summary_chars=max_chars,
                )
                retry_wrapped = build_worker_prompt(
                    task_prompt=reflexion_prompt,
                    output_files=spec.output_files,
                    concise_report=True,
                    is_continuation=True,
                )
                r_parsed, r_warnings, r_code = await self._execute_single_cli_pass(
                    spec=retry_spec,
                    workspace=workspace,
                    resolved_model=retry_spec.model or resolved_model,
                    wrapped_prompt=retry_wrapped,
                    timeout_sec=timeout_sec,
                )
                warnings.extend(r_warnings)
                conv_id = str(r_parsed.get("conversation_id") or conv_id or "").strip() or None
                r_resp = str(r_parsed.get("response") or "").strip()
                if r_resp:
                    raw_response = r_resp
                r_usage = r_parsed.get("usage")
                if isinstance(r_usage, dict):
                    for k, v in r_usage.items():
                        if isinstance(v, (int, float)):
                            usage_dict[k] = usage_dict.get(k, 0) + int(v)
                    extra_tok = int(r_usage.get("total_tokens", 0))
                    if extra_tok > 0:
                        self.pool.record_usage(
                            tokens=extra_tok,
                            model=resolved_model,
                            account_id=active_acc_id,
                        )
                if r_code != 0 and str(r_parsed.get("status", "")).upper() != "SUCCESS":
                    break

        worker_summary, was_truncated = truncate_for_manager(
            raw_response,
            max_chars=max_chars,
            full_output_path=full_md_path,
        )

        saved_path_str: str | None = None
        if was_truncated or len(raw_response) > 600:
            full_md_path.write_text(raw_response, encoding="utf-8")
            saved_path_str = str(full_md_path)

        git_summary = await _capture_git_summary(workspace)
        approx_mgr_chars = len(worker_summary) + len(git_summary) + 160
        est_mgr_tokens = max(1, approx_mgr_chars // 4)

        result = WorkerRunResult(
            run_id=run_id,
            status=status_str,
            conversation_id=conv_id,
            model_used=resolved_model,
            tier=spec.tier,
            workspace_dir=str(workspace),
            duration_seconds=round(float(parsed.get("duration_seconds") or 0.0), 3),
            num_turns=int(parsed.get("num_turns") or 0) + reflexion_attempts,
            antigravity_usage=usage_dict,
            estimated_manager_tokens=est_mgr_tokens,
            git_changes_summary=git_summary,
            worker_summary=worker_summary,
            full_output_path=saved_path_str,
            warnings=warnings,
            active_account_id=active_acc_id,
            account_swapped=account_swapped,
            git_checkpoint_sha=checkpoint_sha,
            verification_passed=verification_passed,
            reflexion_attempts=reflexion_attempts,
            circuit_breaker_tripped=circuit_breaker_tripped,
        )

        self._append_ledger(workspace, result)
        return result

    async def run_parallel_tasks(
        self,
        specs: list[WorkerTaskSpec],
        max_concurrency: int | None = None,
    ) -> list[WorkerRunResult]:
        """Run multiple worker tasks concurrently while serializing any tasks with overlapping `output_files`."""
        limit = max(1, min(max_concurrency or self.settings.max_parallel_workers, 6))
        semaphore = asyncio.Semaphore(limit)
        waves = partition_non_overlapping_waves(specs)
        ordered_results: dict[int, WorkerRunResult] = {}

        async def _bounded_run(
            orig_idx: int,
            item: WorkerTaskSpec,
            overlap_files: list[str],
        ) -> None:
            async with semaphore:
                res = await self.run_task(item)
                if overlap_files:
                    res.warnings.append(
                        f"[FILE-COLLISION-GUARD] Serialized after earlier wave due to overlapping output_files: {overlap_files}"
                    )
                ordered_results[orig_idx] = res

        for wave in waves:
            await asyncio.gather(
                *(_bounded_run(orig_idx, item, overlap_files) for orig_idx, item, overlap_files in wave)
            )

        return [ordered_results[i] for i in range(len(specs))]
