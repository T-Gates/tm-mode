#!/usr/bin/env python3
"""Throttle predicate shared by SessionStart's product-upstream fetch.

Issue #128 removed origin auto-pull orchestration from runtime.  This historical
module name remains because deployed SessionStart hooks import ``should_pull``;
it deliberately performs no Git operation and imports no git_ops symbols.
"""
from __future__ import annotations


def should_pull(state_path: str, now: float, throttle_seconds: int) -> bool:
    """Return whether a throttled fetch should run, failing open on bad state."""
    try:
        with open(state_path, encoding="utf-8") as stream:
            last = float(stream.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return True
    return (now - last) >= throttle_seconds
