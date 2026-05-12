"""Sequence pattern resolution: printf and Flame bracket ranges."""

from __future__ import annotations

import re
from pathlib import Path


def resolve_pattern(pattern: str, frame_idx: int) -> str:
    """Resolve a sequence pattern to a concrete filesystem path.

    Supports:
    - ``printf``-style width token, e.g. ``%04d`` (evaluated before other rules).
    - Flame-style range: ``[0001-0200]`` (padding inferred from left bound).
    - Literal path with a trailing zero-padded frame stem before the extension,
      e.g. ``/shots/plate.0012.exr`` → same directory, stem digits replaced by
      ``frame_idx`` with the same width (compatibility with legacy sequence paths).
    - Any other literal path: returned unchanged.
    """
    if "[" in pattern and "]" in pattern:
        return _resolve_flame(pattern, frame_idx)
    if "%" in pattern:
        return pattern % frame_idx
    p = Path(pattern)
    m = re.match(r"^(.*?)(\d+)(\.\w+)$", p.name)
    if m:
        prefix, num_str, ext = m.groups()
        pad = len(num_str)
        return str(p.with_name(f"{prefix}{str(frame_idx).zfill(pad)}{ext}"))
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
