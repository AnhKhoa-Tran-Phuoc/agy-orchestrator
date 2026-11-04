"""Unit tests for 5-hour and weekly quota tracking and automatic Google account swapping."""

from pathlib import Path
import time

import pytest

from agy_orchestrator.account_pool import (
    AccountPoolManager,
    classify_quota_window_error,
)


def test_classify_quota_window_error_distinguishes_5h_and_weekly() -> None:
    assert classify_quota_window_error("Error: weekly quota limit reached for gemini-4-argon") == "weekly"
    assert classify_quota_window_error("Error 429: 5-hour rate limit exceeded") == "5h"
    assert classify_quota_window_error("Resource exhausted: too many requests") == "5h"
    assert classify_quota_window_error("All tests passed successfully.") is None


def test_account_pool_5h_and_weekly_auto_swap(tmp_path: Path) -> None:
    pool_file = tmp_path / "account_pool.json"
    pool = AccountPoolManager(pool_file=pool_file, swap_threshold_pct=85.0)

    # Register acc-1, acc-2, acc-3
    pool.add_or_update_account("acc-1", email="acc1@gmail.com", cdp_port=9222, limit_5h_tokens=100_000)
    pool.add_or_update_account("acc-2", email="acc2@gmail.com", cdp_port=9223, limit_5h_tokens=100_000)
    pool.add_or_update_account("acc-3", email="acc3@gmail.com", cdp_port=9224, limit_5h_tokens=100_000)

    now = time.time()
    assert pool.active_account_id == "acc-1"

    # Record 88,000 tokens on acc-1 (88% of 5h limit >= 85% threshold)
    metrics_1 = pool.record_usage(tokens=88_000, model="gemini-3.8-flash-high", now_ts=now)
    assert metrics_1["pct_5h"] == 88.0

    # Next ensure_healthy_account() must automatically swap from acc-1 to acc-2!
    swap_res = pool.ensure_healthy_account(now_ts=now + 1)
    assert swap_res["swapped"] is True
    assert swap_res["from_account"] == "acc-1"
    assert swap_res["to_account"] == "acc-2"
    assert pool.active_account_id == "acc-2"

    # Simulate acc-2 hitting a hard weekly quota error -> 7-day cooldown -> swaps to acc-3
    pool.mark_account_exhausted(window_type="weekly", account_id="acc-2", now_ts=now + 2)
    swap_res_2 = pool.ensure_healthy_account(now_ts=now + 3)
    assert swap_res_2["swapped"] is True
    assert swap_res_2["from_account"] == "acc-2"
    assert swap_res_2["to_account"] == "acc-3"
    assert pool.active_account_id == "acc-3"

    # Simulate 5 hours + 10 seconds passing: acc-1's 5h window resets to 0%!
    after_5h = now + (5 * 3600) + 10
    acc1_after = pool.accounts["acc-1"].compute_window_metrics(now_ts=after_5h)
    assert acc1_after["tokens_5h"] == 0
    assert acc1_after["pct_5h"] == 0.0
    # Weekly tokens on acc-1 are still tracked within the 7-day window
    assert acc1_after["tokens_weekly"] == 88_000

