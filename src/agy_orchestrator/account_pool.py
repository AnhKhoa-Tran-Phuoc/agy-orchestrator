"""Multi-account quota tracker (5-hour & weekly windows) separating Argon (CLI vs Web) and 3.8 Flash (CLI)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

WINDOW_5H_SECONDS = 5 * 3600
WINDOW_WEEKLY_SECONDS = 7 * 86400

DEFAULT_5H_TOKEN_LIMIT = 400_000
DEFAULT_WEEKLY_TOKEN_LIMIT = 2_500_000
DEFAULT_5H_RUN_LIMIT = 45
DEFAULT_WEEKLY_RUN_LIMIT = 280
DEFAULT_SWAP_THRESHOLD_PCT = 85.0

MODEL_QUOTA_BUDGETS: dict[str, dict[str, Any]] = {
    "gemini-4-argon-high": {
        "role": "Boss (AGY CLI acc-1)",
        "limit_5h_runs": 15,
        "limit_5h_tokens": 180_000,
        "limit_weekly_runs": 60,
        "limit_weekly_tokens": 750_000,
    },
    "gemini-4-argon-medium": {
        "role": "Boss Fallback (AGY CLI acc-1)",
        "limit_5h_runs": 25,
        "limit_5h_tokens": 250_000,
        "limit_weekly_runs": 100,
        "limit_weekly_tokens": 1_000_000,
    },
    "gemini-4-argon-low": {
        "role": "Boss Fast (AGY CLI acc-1)",
        "limit_5h_runs": 35,
        "limit_5h_tokens": 350_000,
        "limit_weekly_runs": 140,
        "limit_weekly_tokens": 1_400_000,
    },
    "gemini-web-pro": {
        "role": "Boss Web CDP (acc-1/2/3 Pool)",
        "limit_5h_runs": 40,
        "limit_5h_tokens": 400_000,
        "limit_weekly_runs": 250,
        "limit_weekly_tokens": 2_500_000,
    },
    "gemini-3.8-flash-high": {
        "role": "Worker Heavy (AGY CLI 3-Acc)",
        "limit_5h_runs": 45,
        "limit_5h_tokens": 400_000,
        "limit_weekly_runs": 280,
        "limit_weekly_tokens": 2_500_000,
    },
    "gemini-3.8-flash-medium": {
        "role": "Worker Standard (AGY CLI 3-Acc)",
        "limit_5h_runs": 65,
        "limit_5h_tokens": 600_000,
        "limit_weekly_runs": 400,
        "limit_weekly_tokens": 3_500_000,
    },
    "gemini-3.8-flash-low": {
        "role": "Worker Fast (AGY CLI 3-Acc)",
        "limit_5h_runs": 90,
        "limit_5h_tokens": 800_000,
        "limit_weekly_runs": 600,
        "limit_weekly_tokens": 5_000_000,
    },
}

OPENCODE_TRACKED_MODELS: dict[str, str] = {
    "openai/gpt-6-astra": "Boss Primary (1-Acc MAX=high)",
    "openai/gpt-6.1-sol": "Boss Reviewer (1-Acc MAX=high)",
    "opencode/deepseek-v4.1-flash-free": "Worker Code/Test ($0 Free)",
    "opencode/nemotron-3.5-super-vl-free": "Worker VL/Pre-Review ($0 Free)",
    "opencode/nemotron-3.5-lightning-free": "Worker Scout/Runner ($0 Free)",
    "opencode/mimo-v2.6-flash-free": "Worker UI/Config ($0 Free)",
    "opencode/glm-5.3-flash-free": "Worker Docs/State ($0 Free)",
}


def default_pool_file() -> Path:
    """Return the path to `~/.agy-orch/account_pool.json`."""
    env_path = os.environ.get("AGY_ACCOUNT_POOL_FILE")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (Path.home() / ".agy-orch" / "account_pool.json").resolve()


def classify_quota_window_error(response_text: str, warnings: list[str] | None = None) -> str | None:
    """Inspect error text and classify as `'weekly'`, `'5h'`, or `None`."""
    combined = (response_text + "\n" + "\n".join(warnings or [])).lower()
    weekly_markers = (
        "weekly",
        "week limit",
        "weekly quota",
        "7-day",
        "7 days",
        "quota for this week",
    )
    if any(m in combined for m in weekly_markers):
        return "weekly"

    five_hour_markers = (
        "5-hour",
        "5 hour",
        "5h",
        "rate limit",
        "ratelimit",
        "resource_exhausted",
        "resource exhausted",
        "429",
        "too many requests",
        "quota",
        "limit reached",
        "limit exceeded",
        "exhausted",
    )
    if any(m in combined for m in five_hour_markers):
        return "5h"

    return None


@dataclass(slots=True)
class AccountEntry:
    """Represents a Google account in the orchestrator pool.

    - `acc-1` (Google Pro): `has_agy_argon=True`, `has_agy_flash=True` (Argon on CLI + Web, 3.8 Flash on CLI)
    - `acc-2`, `acc-3`, ...: `has_agy_argon=False`, `has_agy_flash=True` (Argon on Web ONLY, 3.8 Flash on CLI)
    """

    account_id: str
    email: str = ""
    cdp_port: int = 9222
    chrome_profile_dir: str = ""
    has_agy_argon: bool = False
    has_agy_flash: bool = True
    limit_5h_tokens: int = DEFAULT_5H_TOKEN_LIMIT
    limit_weekly_tokens: int = DEFAULT_WEEKLY_TOKEN_LIMIT
    limit_5h_runs: int = DEFAULT_5H_RUN_LIMIT
    limit_weekly_runs: int = DEFAULT_WEEKLY_RUN_LIMIT
    cooldown_until_ts: float = 0.0
    cooldown_reason: str | None = None
    argon_cli_cooldown_until_ts: float = 0.0
    web_cooldown_until_ts: float = 0.0
    usage_history: list[dict[str, Any]] = field(default_factory=list)

    def prune_history(self, now_ts: float | None = None) -> None:
        """Keep only usage records within the last 7 days and expire elapsed cooldowns."""
        now = now_ts if now_ts is not None else time.time()
        cutoff = now - WINDOW_WEEKLY_SECONDS
        self.usage_history = [
            rec for rec in self.usage_history if float(rec.get("ts", 0.0)) >= cutoff
        ]
        if self.cooldown_until_ts > 0 and now >= self.cooldown_until_ts:
            self.cooldown_until_ts = 0.0
            self.cooldown_reason = None
        if self.argon_cli_cooldown_until_ts > 0 and now >= self.argon_cli_cooldown_until_ts:
            self.argon_cli_cooldown_until_ts = 0.0
        if self.web_cooldown_until_ts > 0 and now >= self.web_cooldown_until_ts:
            self.web_cooldown_until_ts = 0.0

    def compute_window_metrics(self, now_ts: float | None = None) -> dict[str, Any]:
        """Compute rolling 5-hour and 7-day token and run percentages."""
        now = now_ts if now_ts is not None else time.time()
        self.prune_history(now)

        cutoff_5h = now - WINDOW_5H_SECONDS
        tokens_5h = 0
        runs_5h = 0
        tokens_weekly = 0
        runs_weekly = 0

        for rec in self.usage_history:
            ts = float(rec.get("ts", 0.0))
            tok = int(rec.get("tokens", 0))
            tokens_weekly += tok
            runs_weekly += 1
            if ts >= cutoff_5h:
                tokens_5h += tok
                runs_5h += 1

        pct_5h_tok = (tokens_5h / max(1, self.limit_5h_tokens)) * 100.0
        pct_5h_runs = (runs_5h / max(1, self.limit_5h_runs)) * 100.0
        pct_5h = round(max(pct_5h_tok, pct_5h_runs), 2)

        pct_wk_tok = (tokens_weekly / max(1, self.limit_weekly_tokens)) * 100.0
        pct_wk_runs = (runs_weekly / max(1, self.limit_weekly_runs)) * 100.0
        pct_weekly = round(max(pct_wk_tok, pct_wk_runs), 2)

        in_cooldown = self.cooldown_until_ts > now
        cooldown_remaining_min = (
            round((self.cooldown_until_ts - now) / 60.0, 1) if in_cooldown else 0.0
        )
        argon_cli_ready = self.has_agy_argon and (self.argon_cli_cooldown_until_ts <= now) and not in_cooldown
        web_ready = self.web_cooldown_until_ts <= now

        return {
            "account_id": self.account_id,
            "email": self.email,
            "cdp_port": self.cdp_port,
            "chrome_profile_dir": self.chrome_profile_dir,
            "has_agy_argon": self.has_agy_argon,
            "has_agy_flash": self.has_agy_flash,
            "argon_routing": "agy_cli_then_web" if self.has_agy_argon else "web_only",
            "flash_routing": "agy_cli",
            "argon_cli_ready": argon_cli_ready,
            "web_ready": web_ready,
            "tokens_5h": tokens_5h,
            "runs_5h": runs_5h,
            "pct_5h": pct_5h,
            "tokens_weekly": tokens_weekly,
            "runs_weekly": runs_weekly,
            "pct_weekly": pct_weekly,
            "in_cooldown": in_cooldown,
            "cooldown_reason": self.cooldown_reason if in_cooldown else None,
            "cooldown_remaining_minutes": cooldown_remaining_min,
        }

    def is_near_or_over_limit(
        self,
        swap_threshold_pct: float = DEFAULT_SWAP_THRESHOLD_PCT,
        now_ts: float | None = None,
    ) -> tuple[bool, str | None]:
        """Return `(True, reason)` if this account's CLI Flash quota is in cooldown or >= threshold."""
        metrics = self.compute_window_metrics(now_ts)
        if metrics["in_cooldown"]:
            return True, str(metrics["cooldown_reason"] or "cooldown")
        if metrics["pct_5h"] >= swap_threshold_pct:
            return True, f"5h_quota_near_limit ({metrics['pct_5h']}%)"
        if metrics["pct_weekly"] >= swap_threshold_pct:
            return True, f"weekly_quota_near_limit ({metrics['pct_weekly']}%)"
        return False, None


class AccountPoolManager:
    """Manages Google accounts with independent routing for Argon (CLI on acc-1, Web on acc-2..N) and Flash 3.8 (CLI on all)."""

    def __init__(
        self,
        pool_file: Path | None = None,
        swap_threshold_pct: float = DEFAULT_SWAP_THRESHOLD_PCT,
    ) -> None:
        self.pool_file = (pool_file or default_pool_file()).resolve()
        self.swap_threshold_pct = swap_threshold_pct
        self.active_account_id: str = "acc-1"
        self.active_web_account_id: str = "acc-1"
        self.accounts: dict[str, AccountEntry] = {}
        self.swap_events: list[dict[str, Any]] = []
        self._load()

    def _default_profile_dir_for(self, account_id: str) -> str:
        if account_id == "acc-1":
            return str((self.pool_file.parent / "chrome_profile").resolve())
        return str((self.pool_file.parent / "chrome_profiles" / account_id).resolve())

    def _load(self) -> None:
        if not self.pool_file.is_file():
            default_acc = AccountEntry(
                account_id="acc-1",
                email="primary-google-pro",
                cdp_port=9222,
                chrome_profile_dir=self._default_profile_dir_for("acc-1"),
                has_agy_argon=True,
                has_agy_flash=True,
            )
            self.accounts = {"acc-1": default_acc}
            self.active_account_id = "acc-1"
            self.active_web_account_id = "acc-1"
            self._save()
            return

        try:
            raw = json.loads(self.pool_file.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

        loaded: dict[str, AccountEntry] = {}
        for item in raw.get("accounts", []):
            if not isinstance(item, dict) or not item.get("account_id"):
                continue
            acc_id = str(item["account_id"])
            loaded[acc_id] = AccountEntry(
                account_id=acc_id,
                email=str(item.get("email", "")),
                cdp_port=int(item.get("cdp_port", 9222)),
                chrome_profile_dir=str(
                    item.get("chrome_profile_dir") or self._default_profile_dir_for(acc_id)
                ),
                has_agy_argon=bool(item.get("has_agy_argon", acc_id == "acc-1")),
                has_agy_flash=bool(item.get("has_agy_flash", True)),
                limit_5h_tokens=int(item.get("limit_5h_tokens", DEFAULT_5H_TOKEN_LIMIT)),
                limit_weekly_tokens=int(
                    item.get("limit_weekly_tokens", DEFAULT_WEEKLY_TOKEN_LIMIT)
                ),
                limit_5h_runs=int(item.get("limit_5h_runs", DEFAULT_5H_RUN_LIMIT)),
                limit_weekly_runs=int(item.get("limit_weekly_runs", DEFAULT_WEEKLY_RUN_LIMIT)),
                cooldown_until_ts=float(item.get("cooldown_until_ts", 0.0)),
                cooldown_reason=item.get("cooldown_reason"),
                argon_cli_cooldown_until_ts=float(item.get("argon_cli_cooldown_until_ts", 0.0)),
                web_cooldown_until_ts=float(item.get("web_cooldown_until_ts", 0.0)),
                usage_history=list(item.get("usage_history", [])),
            )

        if not loaded:
            loaded["acc-1"] = AccountEntry(
                account_id="acc-1",
                email="primary-google-pro",
                cdp_port=9222,
                chrome_profile_dir=self._default_profile_dir_for("acc-1"),
                has_agy_argon=True,
                has_agy_flash=True,
            )

        self.accounts = loaded
        active = str(raw.get("active_account_id", ""))
        self.active_account_id = active if active in self.accounts else next(iter(self.accounts))
        active_web = str(raw.get("active_web_account_id", ""))
        self.active_web_account_id = (
            active_web if active_web in self.accounts else self.active_account_id
        )
        self.swap_events = list(raw.get("swap_events", []))[-30:]

    def _save(self) -> None:
        self.pool_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_account_id": self.active_account_id,
            "active_web_account_id": self.active_web_account_id,
            "swap_threshold_pct": self.swap_threshold_pct,
            "accounts": [asdict(acc) for acc in self.accounts.values()],
            "swap_events": self.swap_events[-30:],
        }
        self.pool_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def add_or_update_account(
        self,
        account_id: str,
        email: str = "",
        cdp_port: int | None = None,
        has_agy_argon: bool | None = None,
        has_agy_flash: bool = True,
        limit_5h_tokens: int = DEFAULT_5H_TOKEN_LIMIT,
        limit_weekly_tokens: int = DEFAULT_WEEKLY_TOKEN_LIMIT,
    ) -> AccountEntry:
        """Register or update a Google account in the pool."""
        acc_id = account_id.strip()
        if acc_id in self.accounts:
            acc = self.accounts[acc_id]
            if email:
                acc.email = email
            if cdp_port is not None:
                acc.cdp_port = cdp_port
            if has_agy_argon is not None:
                acc.has_agy_argon = has_agy_argon
            acc.has_agy_flash = has_agy_flash
            acc.limit_5h_tokens = limit_5h_tokens
            acc.limit_weekly_tokens = limit_weekly_tokens
        else:
            used_ports = {a.cdp_port for a in self.accounts.values()}
            next_port = cdp_port or 9222
            while next_port in used_ports:
                next_port += 1
            acc = AccountEntry(
                account_id=acc_id,
                email=email,
                cdp_port=next_port,
                chrome_profile_dir=self._default_profile_dir_for(acc_id),
                has_agy_argon=bool(has_agy_argon) if has_agy_argon is not None else (acc_id == "acc-1"),
                has_agy_flash=has_agy_flash,
                limit_5h_tokens=limit_5h_tokens,
                limit_weekly_tokens=limit_weekly_tokens,
            )
            self.accounts[acc_id] = acc

        self._save()
        return acc

    def get_active_account(self) -> AccountEntry:
        """Return the active account for Antigravity CLI (`gemini-3.8-flash` & `acc-1` Argon)."""
        if self.active_account_id not in self.accounts:
            self.active_account_id = next(iter(self.accounts))
        return self.accounts[self.active_account_id]

    def get_active_web_account(self) -> AccountEntry:
        """Return the active account for Gemini Web CDP (`acc-1`, `acc-2`, `acc-3`, ...)."""
        if self.active_web_account_id not in self.accounts:
            self.active_web_account_id = self.active_account_id
        return self.accounts[self.active_web_account_id]

    def swap_web_account(self, reason: str = "web_quota_rotation", now_ts: float | None = None) -> AccountEntry:
        """Rotate `active_web_account_id` across `acc-1` -> `acc-2` -> `acc-3` for Gemini Web Boss."""
        now = now_ts if now_ts is not None else time.time()
        ids = list(self.accounts.keys())
        start_idx = ids.index(self.active_web_account_id) if self.active_web_account_id in ids else 0
        ordered = ids[start_idx + 1 :] + ids[: start_idx + 1]
        prev = self.active_web_account_id

        for cid in ordered:
            cand = self.accounts[cid]
            cand.prune_history(now)
            if cand.web_cooldown_until_ts <= now:
                self.active_web_account_id = cid
                break

        if self.active_web_account_id != prev:
            self.swap_events.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "channel": "gemini_web_argon",
                    "from_account": prev,
                    "to_account": self.active_web_account_id,
                    "swapped": True,
                    "reason": reason,
                }
            )
        self._save()
        return self.accounts[self.active_web_account_id]

    def record_usage(
        self,
        tokens: int,
        model: str,
        account_id: str | None = None,
        now_ts: float | None = None,
    ) -> dict[str, Any]:
        """Record token consumption for the active (or specified) account."""
        now = now_ts if now_ts is not None else time.time()
        acc = self.accounts.get(account_id or self.active_account_id) or self.get_active_account()
        acc.prune_history(now)
        acc.usage_history.append(
            {
                "ts": now,
                "tokens": max(0, int(tokens)),
                "model": model,
            }
        )
        metrics = acc.compute_window_metrics(now)
        self._save()
        return metrics

    def mark_argon_cli_exhausted(
        self,
        window_type: str = "weekly",
        account_id: str | None = None,
        now_ts: float | None = None,
    ) -> AccountEntry:
        """Mark ONLY `argon` on Antigravity CLI as exhausted (so `gemini-3.8-flash` on CLI keeps working!)."""
        now = now_ts if now_ts is not None else time.time()
        acc = self.accounts.get(account_id or self.active_account_id) or self.get_active_account()
        duration = WINDOW_WEEKLY_SECONDS if window_type == "weekly" else WINDOW_5H_SECONDS
        acc.argon_cli_cooldown_until_ts = now + duration
        self._save()
        return acc

    def mark_account_exhausted(
        self,
        window_type: str = "5h",
        account_id: str | None = None,
        now_ts: float | None = None,
        channel: str = "cli_flash",
    ) -> AccountEntry:
        """Put an account into a 5-hour or 7-day cooldown after hitting a quota limit."""
        now = now_ts if now_ts is not None else time.time()
        acc = self.accounts.get(account_id or self.active_account_id) or self.get_active_account()
        duration = WINDOW_WEEKLY_SECONDS if window_type == "weekly" else WINDOW_5H_SECONDS

        if channel == "web":
            acc.web_cooldown_until_ts = now + duration
        elif channel == "cli_argon":
            acc.argon_cli_cooldown_until_ts = now + duration
        else:
            acc.cooldown_until_ts = now + duration
            acc.cooldown_reason = f"{window_type}_quota_exhausted"
        self._save()
        return acc

    def ensure_healthy_account(self, now_ts: float | None = None) -> dict[str, Any]:
        """Check if active CLI Flash account is near 5h/weekly quota or in cooldown; auto-swap if needed."""
        now = now_ts if now_ts is not None else time.time()
        active = self.get_active_account()
        needs_swap, reason = active.is_near_or_over_limit(
            swap_threshold_pct=self.swap_threshold_pct,
            now_ts=now,
        )
        if not needs_swap:
            return {
                "swapped": False,
                "active_account": active.compute_window_metrics(now),
            }

        return self.swap_to_next_account(reason=reason or "quota_threshold", now_ts=now)

    def swap_to_next_account(
        self,
        target_account_id: str | None = None,
        reason: str = "manual_swap",
        now_ts: float | None = None,
    ) -> dict[str, Any]:
        """Rotate `active_account_id` (used by `gemini-3.8-flash` on `agy` CLI) to the next healthy account."""
        now = now_ts if now_ts is not None else time.time()
        previous_id = self.active_account_id

        if target_account_id and target_account_id in self.accounts:
            self.active_account_id = target_account_id
            chosen = self.accounts[target_account_id]
            swapped = previous_id != target_account_id
        else:
            ids = list(self.accounts.keys())
            start_idx = ids.index(previous_id) if previous_id in ids else 0
            ordered_ids = ids[start_idx + 1 :] + ids[: start_idx + 1]

            chosen = self.accounts[previous_id]
            swapped = False

            for cid in ordered_ids:
                cand = self.accounts[cid]
                if not cand.has_agy_flash:
                    continue
                over, _ = cand.is_near_or_over_limit(self.swap_threshold_pct, now_ts=now)
                if not over:
                    chosen = cand
                    self.active_account_id = cid
                    swapped = cid != previous_id
                    break
            else:
                best_acc = min(
                    self.accounts.values(),
                    key=lambda a: (
                        1 if a.compute_window_metrics(now)["in_cooldown"] else 0,
                        a.compute_window_metrics(now)["pct_5h"],
                    ),
                )
                chosen = best_acc
                self.active_account_id = best_acc.account_id
                swapped = best_acc.account_id != previous_id

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": "agy_cli_flash",
            "from_account": previous_id,
            "to_account": self.active_account_id,
            "swapped": swapped,
            "reason": reason,
        }
        if swapped:
            self.swap_events.append(event)
        self._save()

        return {
            "swapped": swapped,
            "from_account": previous_id,
            "to_account": self.active_account_id,
            "reason": reason,
            "active_account": chosen.compute_window_metrics(now),
            "pool_size": len(self.accounts),
        }

    def get_pool_status(self, now_ts: float | None = None) -> dict[str, Any]:
        """Return a complete status report of all accounts, 5h/weekly quotas, and recent swaps."""
        now = now_ts if now_ts is not None else time.time()
        accounts_metrics = [
            acc.compute_window_metrics(now) for acc in self.accounts.values()
        ]
        return {
            "pool_file": str(self.pool_file),
            "active_cli_flash_account_id": self.active_account_id,
            "active_web_argon_account_id": self.active_web_account_id,
            "swap_threshold_pct": self.swap_threshold_pct,
            "total_accounts": len(self.accounts),
            "accounts": accounts_metrics,
            "recent_swaps": self.swap_events[-10:],
        }

    def get_per_model_quota_report(
        self,
        now_ts: float | None = None,
        include_opencode_stats: bool = True,
    ) -> dict[str, Any]:
        """Compute a unified per-model quota & usage breakdown across Antigravity CLI, Gemini Web, and OpenCode."""
        now = now_ts if now_ts is not None else time.time()
        cutoff_5h = now - WINDOW_5H_SECONDS
        total_accs = max(1, len(self.accounts))

        # Aggregate usage across all accounts by model
        usage_by_model: dict[str, dict[str, Any]] = {}
        for model_id in MODEL_QUOTA_BUDGETS:
            usage_by_model[model_id] = {
                "runs_5h": 0,
                "tokens_5h": 0,
                "runs_weekly": 0,
                "tokens_weekly": 0,
                "oldest_ts_5h": None,
                "by_account": {},
            }

        for acc in self.accounts.values():
            acc.prune_history(now)
            for rec in acc.usage_history:
                m_id = str(rec.get("model", "unknown"))
                ts = float(rec.get("ts", 0.0))
                tok = int(rec.get("tokens", 0))
                bucket = usage_by_model.setdefault(
                    m_id,
                    {
                        "runs_5h": 0,
                        "tokens_5h": 0,
                        "runs_weekly": 0,
                        "tokens_weekly": 0,
                        "oldest_ts_5h": None,
                        "by_account": {},
                    },
                )
                bucket["runs_weekly"] += 1
                bucket["tokens_weekly"] += tok
                acc_bucket = bucket["by_account"].setdefault(
                    acc.account_id, {"runs_5h": 0, "tokens_5h": 0, "runs_weekly": 0, "tokens_weekly": 0}
                )
                acc_bucket["runs_weekly"] += 1
                acc_bucket["tokens_weekly"] += tok

                if ts >= cutoff_5h:
                    bucket["runs_5h"] += 1
                    bucket["tokens_5h"] += tok
                    acc_bucket["runs_5h"] += 1
                    acc_bucket["tokens_5h"] += tok
                    if bucket["oldest_ts_5h"] is None or ts < bucket["oldest_ts_5h"]:
                        bucket["oldest_ts_5h"] = ts

        agy_and_web_models: list[dict[str, Any]] = []
        for model_id, bucket in usage_by_model.items():
            budget = MODEL_QUOTA_BUDGETS.get(
                model_id,
                {
                    "role": "Worker/Custom",
                    "limit_5h_runs": DEFAULT_5H_RUN_LIMIT,
                    "limit_5h_tokens": DEFAULT_5H_TOKEN_LIMIT,
                    "limit_weekly_runs": DEFAULT_WEEKLY_RUN_LIMIT,
                    "limit_weekly_tokens": DEFAULT_WEEKLY_TOKEN_LIMIT,
                },
            )
            # Multi-account multiplier: Web and Flash models scale across all registered accounts; CLI Argon is on acc-1 only
            is_cli_argon = model_id.startswith("gemini-4-argon")
            multiplier = 1 if is_cli_argon else total_accs

            lim_5h_runs = int(budget["limit_5h_runs"]) * multiplier
            lim_5h_tok = int(budget["limit_5h_tokens"]) * multiplier
            lim_wk_runs = int(budget["limit_weekly_runs"]) * multiplier
            lim_wk_tok = int(budget["limit_weekly_tokens"]) * multiplier

            used_5h_pct = round(
                max(
                    (bucket["runs_5h"] / max(1, lim_5h_runs)) * 100.0,
                    (bucket["tokens_5h"] / max(1, lim_5h_tok)) * 100.0,
                ),
                2,
            )
            used_wk_pct = round(
                max(
                    (bucket["runs_weekly"] / max(1, lim_wk_runs)) * 100.0,
                    (bucket["tokens_weekly"] / max(1, lim_wk_tok)) * 100.0,
                ),
                2,
            )
            rem_5h_pct = round(max(0.0, 100.0 - used_5h_pct), 2)
            rem_wk_pct = round(max(0.0, 100.0 - used_wk_pct), 2)

            reset_5h_min = 0.0
            if bucket["oldest_ts_5h"] is not None:
                reset_5h_min = round(
                    max(0.0, (float(bucket["oldest_ts_5h"]) + WINDOW_5H_SECONDS - now) / 60.0),
                    1,
                )

            agy_and_web_models.append(
                {
                    "model": model_id,
                    "role": budget["role"],
                    "pool_accounts_backing": multiplier,
                    "runs_5h": bucket["runs_5h"],
                    "limit_5h_runs": lim_5h_runs,
                    "tokens_5h": bucket["tokens_5h"],
                    "limit_5h_tokens": lim_5h_tok,
                    "used_5h_pct": used_5h_pct,
                    "remaining_5h_pct": rem_5h_pct,
                    "window_5h_resets_in_minutes": reset_5h_min,
                    "runs_weekly": bucket["runs_weekly"],
                    "limit_weekly_runs": lim_wk_runs,
                    "tokens_weekly": bucket["tokens_weekly"],
                    "limit_weekly_tokens": lim_wk_tok,
                    "used_weekly_pct": used_wk_pct,
                    "remaining_weekly_pct": rem_wk_pct,
                    "by_account": bucket["by_account"],
                }
            )

        opencode_models = _fetch_opencode_model_stats() if include_opencode_stats else []
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pool_status": self.get_pool_status(now),
            "antigravity_and_web_models": agy_and_web_models,
            "opencode_models": opencode_models,
        }


def _find_opencode_cli() -> str | None:
    found = shutil.which("opencode") or shutil.which("opencode-cli")
    if found:
        return found
    if os.name == "nt":
        roaming = os.environ.get("APPDATA", "")
        candidate = Path(roaming) / "ai.opencode.desktop" / "cli" / "2.2.5-x64" / "opencode-cli.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _fetch_opencode_model_stats() -> list[dict[str, Any]]:
    """Query `opencode stats --models --cost --json --days 7` and merge with tracked OpenCode models."""
    cli_bin = _find_opencode_cli()
    raw_models: dict[str, dict[str, Any]] = {}
    if cli_bin:
        try:
            proc = subprocess.run(
                [cli_bin, "stats", "--models", "--cost", "--json", "--days", "7"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=6,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                for entry in parsed.get("models", []):
                    m_info = entry.get("model") or {}
                    prov = str(m_info.get("providerID", ""))
                    m_id = str(m_info.get("id", ""))
                    full_id = f"{prov}/{m_id}" if prov else m_id
                    tok = entry.get("tokens") or {}
                    cache = tok.get("cache") or {}
                    raw_models[full_id] = {
                        "steps_7d": int(entry.get("steps", 0)),
                        "input_tokens_7d": int(tok.get("input", 0)),
                        "output_tokens_7d": int(tok.get("output", 0)),
                        "reasoning_tokens_7d": int(tok.get("reasoning", 0)),
                        "cache_read_tokens_7d": int(cache.get("read", 0)),
                        "cost_usd_7d": float(entry.get("cost", 0.0)),
                    }
        except Exception:
            pass

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for full_id, role_desc in OPENCODE_TRACKED_MODELS.items():
        seen.add(full_id)
        stats = raw_models.get(
            full_id,
            {
                "steps_7d": 0,
                "input_tokens_7d": 0,
                "output_tokens_7d": 0,
                "reasoning_tokens_7d": 0,
                "cache_read_tokens_7d": 0,
                "cost_usd_7d": 0.0,
            },
        )
        is_free = full_id.startswith("opencode/")
        results.append(
            {
                "model": full_id,
                "role": role_desc,
                "quota_policy": "UNLIMITED ($0 Free)" if is_free else "1-Account Shield (MAX=high)",
                **stats,
            }
        )

    for extra_id, stats in raw_models.items():
        if extra_id not in seen:
            is_free = extra_id.startswith("opencode/")
            results.append(
                {
                    "model": extra_id,
                    "role": "OpenCode Active Model",
                    "quota_policy": "UNLIMITED ($0 Free)" if is_free else "Standard Quota",
                    **stats,
                }
            )
    return results


def _bar(remaining_pct: float, width: int = 10) -> str:
    clamped = max(0.0, min(100.0, remaining_pct))
    filled = int(round((clamped / 100.0) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def format_per_model_quota_table(report: dict[str, Any]) -> str:
    """Format the per-model quota report as a clean ASCII dashboard table for CLI & OpenCode chat."""
    lines: list[str] = []
    pool = report.get("pool_status") or {}
    lines.append("==========================================================================================")
    lines.append("  AGY-ORCHESTRATOR — PER-MODEL & MULTI-ACCOUNT QUOTA MONITOR (5H & WEEKLY WINDOWS)")
    lines.append("==========================================================================================")
    lines.append(
        f"  Active CLI Flash Account : {pool.get('active_cli_flash_account_id', 'acc-1')}  |  "
        f"Active Web Argon Account : {pool.get('active_web_argon_account_id', 'acc-1')}  |  "
        f"Auto-Swap Threshold : {pool.get('swap_threshold_pct', 85.0)}%"
    )
    lines.append("------------------------------------------------------------------------------------------")
    lines.append("  1. GOOGLE ANTIGRAVITY CLI & GEMINI WEB MODELS (5-HOUR & WEEKLY QUOTA REMAINING)")
    lines.append("------------------------------------------------------------------------------------------")
    lines.append(
        f"  {'Model ID':<25} {'Role':<29} {'5h Quota Left':<20} {'Weekly Left':<18} {'5h Runs'}"
    )
    lines.append("  " + ("-" * 86))

    for m in report.get("antigravity_and_web_models", []):
        rem_5h = float(m.get("remaining_5h_pct", 100.0))
        rem_wk = float(m.get("remaining_weekly_pct", 100.0))
        bar_5h = f"{_bar(rem_5h, 8)} {rem_5h:>5.1f}%"
        bar_wk = f"{_bar(rem_wk, 6)} {rem_wk:>5.1f}%"
        runs_str = f"{m.get('runs_5h', 0)}/{m.get('limit_5h_runs', 0)}"
        lines.append(
            f"  {str(m['model']):<25} {str(m['role']):<29} {bar_5h:<20} {bar_wk:<18} {runs_str}"
        )

    lines.append("")
    lines.append("------------------------------------------------------------------------------------------")
    lines.append("  2. GOOGLE ACCOUNT POOL STATUS (acc-1, acc-2, acc-3, ...)")
    lines.append("------------------------------------------------------------------------------------------")
    lines.append(
        f"  {'Acc ID':<8} {'CDP Port':<9} {'Argon Mode':<17} {'5h Quota Left':<20} {'Weekly Left':<18} {'Status'}"
    )
    lines.append("  " + ("-" * 86))
    for acc in pool.get("accounts", []):
        rem_5h = round(max(0.0, 100.0 - float(acc.get("pct_5h", 0.0))), 1)
        rem_wk = round(max(0.0, 100.0 - float(acc.get("pct_weekly", 0.0))), 1)
        bar_5h = f"{_bar(rem_5h, 8)} {rem_5h:>5.1f}%"
        bar_wk = f"{_bar(rem_wk, 6)} {rem_wk:>5.1f}%"
        if acc.get("in_cooldown"):
            status_str = f"COOLDOWN ({acc.get('cooldown_remaining_minutes')}m)"
        else:
            status_str = "READY (CLI+Web)" if acc.get("argon_cli_ready") else "READY (Web+Flash)"
        lines.append(
            f"  {str(acc['account_id']):<8} {str(acc['cdp_port']):<9} "
            f"{str(acc.get('argon_routing', '')):<17} {bar_5h:<20} {bar_wk:<18} {status_str}"
        )

    lines.append("")
    lines.append("------------------------------------------------------------------------------------------")
    lines.append("  3. OPENCODE BOSS & ZERO-COST WORKER MODELS (7-DAY LIVE TELEMETRY)")
    lines.append("------------------------------------------------------------------------------------------")
    lines.append(
        f"  {'Model ID':<36} {'Quota Policy':<28} {'Steps':<7} {'Input Tok':<11} {'Out/Rsn Tok'}"
    )
    lines.append("  " + ("-" * 86))
    for oc in report.get("opencode_models", []):
        out_rsn = int(oc.get("output_tokens_7d", 0)) + int(oc.get("reasoning_tokens_7d", 0))
        lines.append(
            f"  {str(oc['model']):<36} {str(oc['quota_policy']):<28} "
            f"{int(oc.get('steps_7d', 0)):<7} {int(oc.get('input_tokens_7d', 0)):<11} {out_rsn}"
        )
    lines.append("==========================================================================================")
    return "\n".join(lines)
