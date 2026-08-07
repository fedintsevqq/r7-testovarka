#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test for _sample_r7_resources fix: verify independent metric handling.
Tests that if one psutil call fails, others still contribute to aggregates.
"""

import sys
import time
from unittest.mock import MagicMock
from pathlib import Path

# Add the worktree to path
sys.path.insert(0, str(Path(__file__).parent))

# Mock psutil before importing r7_Testovarka
import psutil
original_NoSuchProcess = psutil.NoSuchProcess
original_AccessDenied = psutil.AccessDenied

# Import after mocking
from r7_Testovarka import R7Testovarka

def test_independent_metric_failures():
    """
    Test scenario: Process 1 has RAM but cpu_percent() fails.
    Expected: RAM is counted, cpu/threads/uptime reflect reality of other processes.
    """
    print("Testing: RAM succeeds, CPU fails => RAM should still be counted, alive=1")

    # Create minimal mock app with just the _sample_r7_resources method
    app = MagicMock()
    app._sample_r7_resources = R7Testovarka._sample_r7_resources.__get__(app, R7Testovarka)

    # Create mock process with RAM ok, CPU fails
    p1 = MagicMock()
    p1.memory_info.return_value.rss = 100 * 1024 * 1024  # 100 MB
    p1.cpu_percent.side_effect = psutil.NoSuchProcess(0)  # CPU fails
    p1.num_threads.return_value = 5
    p1.create_time.return_value = time.time() - 10

    procs = [p1]
    result = app._sample_r7_resources(procs)

    # Verify: RAM should be counted even though CPU failed
    assert result is not None, "Result should not be None (alive=1 from RAM)"
    assert result['ram_mb'] == 100.0, f"Expected RAM=100.0, got {result['ram_mb']}"
    assert result['threads'] == 5, f"Expected threads=5, got {result['threads']}"
    assert 9 <= result['uptime_sec'] <= 11, f"Expected uptime ~10s, got {result['uptime_sec']}"
    # CPU should be 0 (default, since no successful reads)
    assert result['cpu_raw_pct'] == 0.0, f"Expected cpu_raw=0 (no reads), got {result['cpu_raw_pct']}"
    print("  [OK] RAM counted despite CPU failure; alive=1")

    print("\nTesting: Process with all metrics succeeds")
    app2 = MagicMock()
    app2._sample_r7_resources = R7Testovarka._sample_r7_resources.__get__(app2, R7Testovarka)

    p2 = MagicMock()
    p2.memory_info.return_value.rss = 200 * 1024 * 1024  # 200 MB
    p2.cpu_percent.return_value = 15.5
    p2.num_threads.return_value = 8
    p2.create_time.return_value = time.time() - 20

    procs = [p2]
    result = app2._sample_r7_resources(procs)

    assert result is not None, "Result should not be None"
    assert result['ram_mb'] == 200.0, f"Expected RAM=200.0, got {result['ram_mb']}"
    assert result['cpu_raw_pct'] == 15.5, f"Expected cpu_raw=15.5, got {result['cpu_raw_pct']}"
    assert result['threads'] == 8, f"Expected threads=8, got {result['threads']}"
    print("  [OK] All metrics aggregated correctly")

    print("\nTesting: Empty process list => None")
    app3 = MagicMock()
    app3._sample_r7_resources = R7Testovarka._sample_r7_resources.__get__(app3, R7Testovarka)
    result = app3._sample_r7_resources([])
    assert result is None, "Expected None for empty procs list"
    print("  [OK] Empty list returns None")

    print("\n[PASS] All tests passed! Fix verified: each metric fails independently.")

if __name__ == '__main__':
    test_independent_metric_failures()
