"""Sequence pattern resolution: printf and Flame bracket ranges."""

from __future__ import annotations

import re
from pathlib import Path


def resolve_pattern(pattern: str, frame_idx: int) -> str:
    """Resolve a sequence pattern to a concrete filesystem path.

    Supports:
    - Literal path (no printf token and no Flame bracket range): returned unchanged.
    - ``printf``-style width token, e.g. ``%04d``.
    - Flame-style range: ``[0001-0200]`` (padding inferred from left bound).
    """
    if "[" in pattern and "]" in pattern:
        return _resolve_flame(pattern, frame_idx)
    if "%" in pattern:
        return pattern % frame_idx
    return pattern


def _resolve_flame(pattern: str, frame_idx: int) -> str:
    m = re.search(r"\[(\d+)\s*-\s*(\d+)\]", pattern)
    if not m:
        raise ValueError(f"Invalid Flame bracket range in pattern: {pattern!r}")
    start_s, end_s = m.group(1), m.group(2)
    if len(start_s) != len(end_s):
        raise ValueError("Flame bracket bounds must have the same padding width")
    width = len(start_s)
    start_i, end_i = int(start_s), int(end_s)
    if not (start_i <= frame_idx <= end_i):
        raise ValueError(f"frame_idx {frame_idx} out of range [{start_i}, {end_i}]")
    frame_str = str(frame_idx).zfill(width)
    return pattern[: m.start()] + frame_str + pattern[m.end() :]


def resolve_pattern_path(pattern: str | Path, frame_idx: int) -> Path:
    return Path(resolve_pattern(str(pattern), frame_idx))
