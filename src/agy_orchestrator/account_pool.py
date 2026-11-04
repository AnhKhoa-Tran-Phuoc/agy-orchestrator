"""Multi-account quota tracker (5-hour & weekly windows) and automatic account swapper."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

WINDOW_5H_SECONDS = 5 * 3600
WINDOW_WEEKLY_SECONDS = 7 * 86400

DEFAULT_5H_TOKEN_LIMIT = 400_000
DEFAULT_WEEKLY_TOKEN_LIMIT = 2_500_000
DEFAULT_5H_RUN_LIMIT = 45
DEFAULT_WEEKLY_RUN_LIMIT = 280
DEFAULT_SWAP_THRESHOLD_PCT = 85.0


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
    """Represents a single Google account configured for Antigravity / Gemini Web rotation."""

    account_id: str
    email: str = ""
    cdp_port: int = 9222
    chrome_profile_dir: str = ""
    limit_5h_tokens: int = DEFAULT_5H_TOKEN_LIMIT
    limit_weekly_tokens: int = DEFAULT_WEEKLY_TOKEN_LIMIT
    limit_5h_runs: int = DEFAULT_5H_RUN_LIMIT
    limit_weekly_runs: int = DEFAULT_WEEKLY_RUN_LIMIT
    cooldown_until_ts: float = 0.0
    cooldown_reason: str | None = None
    usage_history: list[dict[str, Any]] = field(default_factory=list)

    def prune_history(self, now_ts: float | None = None) -> None:
        """Keep only usage records within the last 7 days."""
        now = now_ts if now_ts is not None else time.time()
        cutoff = now - WINDOW_WEEKLY_SECONDS
        self.usage_history = [
            rec for rec in self.usage_history if float(rec.get("ts", 0.0)) >= cutoff
        ]
        if self.cooldown_until_ts > 0 and now >= self.cooldown_until_ts:
            self.cooldown_until_ts = 0.0
            self.cooldown_reason = None

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

        return {
            "account_id": self.account_id,
            "email": self.email,
            "cdp_port": self.cdp_port,
            "chrome_profile_dir": self.chrome_profile_dir,
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
        """Return `(True, reason)` if this account is in cooldown or above the 5h/weekly threshold."""
        metrics = self.compute_window_metrics(now_ts)
        if metrics["in_cooldown"]:
            return True, str(metrics["cooldown_reason"] or "cooldown")
        if metrics["pct_5h"] >= swap_threshold_pct:
            return True, f"5h_quota_near_limit ({metrics['pct_5h']}%)"
        if metrics["pct_weekly"] >= swap_threshold_pct:
            return True, f"weekly_quota_near_limit ({metrics['pct_weekly']}%)"
        return False, None


class AccountPoolManager:
    """Manages a pool of Google Antigravity / Gemini Web accounts and auto-swaps on 5h/weekly quota."""

    def __init__(
        self,
        pool_file: Path | None = None,
        swap_threshold_pct: float = DEFAULT_SWAP_THRESHOLD_PCT,
    ) -> None:
        self.pool_file = (pool_file or default_pool_file()).resolve()
        self.swap_threshold_pct = swap_threshold_pct
        self.active_account_id: str = "acc-1"
        self.accounts: dict[str, AccountEntry] = {}
        self.swap_events: list[dict[str, Any]] = []
        self._load()

    def _default_profile_dir_for(self, account_id: str) -> str:
        if account_id == "acc-1":
            # Reuse the primary chrome_profile directory for acc-1 for backwards compatibility
            return str((self.pool_file.parent / "chrome_profile").resolve())
        return str((self.pool_file.parent / "chrome_profiles" / account_id).resolve())

    def _load(self) -> None:
        if not self.pool_file.is_file():
            default_acc = AccountEntry(
                account_id="acc-1",
                email="primary-google-pro",
                cdp_port=9222,
                chrome_profile_dir=self._default_profile_dir_for("acc-1"),
            )
            self.accounts = {"acc-1": default_acc}
            self.active_account_id = "acc-1"
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
                limit_5h_tokens=int(item.get("limit_5h_tokens", DEFAULT_5H_TOKEN_LIMIT)),
                limit_weekly_tokens=int(
                    item.get("limit_weekly_tokens", DEFAULT_WEEKLY_TOKEN_LIMIT)
                ),
                limit_5h_runs=int(item.get("limit_5h_runs", DEFAULT_5H_RUN_LIMIT)),
                limit_weekly_runs=int(item.get("limit_weekly_runs", DEFAULT_WEEKLY_RUN_LIMIT)),
                cooldown_until_ts=float(item.get("cooldown_until_ts", 0.0)),
                cooldown_reason=item.get("cooldown_reason"),
                usage_history=list(item.get("usage_history", [])),
            )

        if not loaded:
            loaded["acc-1"] = AccountEntry(
                account_id="acc-1",
                email="primary-google-pro",
                cdp_port=9222,
                chrome_profile_dir=self._default_profile_dir_for("acc-1"),
            )

        self.accounts = loaded
        active = str(raw.get("active_account_id", ""))
        self.active_account_id = active if active in self.accounts else next(iter(self.accounts))
        self.swap_events = list(raw.get("swap_events", []))[-30:]

    def _save(self) -> None:
        self.pool_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_account_id": self.active_account_id,
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
        limit_5h_tokens: int = DEFAULT_5H_TOKEN_LIMIT,
        limit_weekly_tokens: int = DEFAULT_WEEKLY_TOKEN_LIMIT,
    ) -> AccountEntry:
        """Register a new Google account in the rotation pool."""
        acc_id = account_id.strip()
        if acc_id in self.accounts:
            acc = self.accounts[acc_id]
            if email:
                acc.email = email
            if cdp_port is not None:
                acc.cdp_port = cdp_port
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
                limit_5h_tokens=limit_5h_tokens,
                limit_weekly_tokens=limit_weekly_tokens,
            )
            self.accounts[acc_id] = acc

        self._save()
        return acc

    def get_active_account(self) -> AccountEntry:
        """Return the currently active `AccountEntry`."""
        if self.active_account_id not in self.accounts:
            self.active_account_id = next(iter(self.accounts))
        return self.accounts[self.active_account_id]

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

    def mark_account_exhausted(
        self,
        window_type: str = "5h",
        account_id: str | None = None,
        now_ts: float | None = None,
    ) -> AccountEntry:
        """Put an account into a 5-hour or 7-day cooldown after hitting a quota limit."""
        now = now_ts if now_ts is not None else time.time()
        acc = self.accounts.get(account_id or self.active_account_id) or self.get_active_account()
        if window_type == "weekly":
            acc.cooldown_until_ts = now + WINDOW_WEEKLY_SECONDS
            acc.cooldown_reason = "weekly_quota_exhausted"
        else:
            acc.cooldown_until_ts = now + WINDOW_5H_SECONDS
            acc.cooldown_reason = "5h_quota_exhausted"
        self._save()
        return acc

    def ensure_healthy_account(self, now_ts: float | None = None) -> dict[str, Any]:
        """Check if active account is near 5h/weekly quota or in cooldown; auto-swap if needed."""
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
        """Rotate `active_account_id` to the healthiest available account in the pool."""
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

            # First pass: find an account not near limit and not in cooldown
            for cid in ordered_ids:
                cand = self.accounts[cid]
                over, _ = cand.is_near_or_over_limit(self.swap_threshold_pct, now_ts=now)
                if not over:
                    chosen = cand
                    self.active_account_id = cid
                    swapped = cid != previous_id
                    break
            else:
                # Second pass: pick the account with the lowest 5h usage percentage
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
            "active_account_id": self.active_account_id,
            "swap_threshold_pct": self.swap_threshold_pct,
            "total_accounts": len(self.accounts),
            "accounts": accounts_metrics,
            "recent_swaps": self.swap_events[-10:],
        }
