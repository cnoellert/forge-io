from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    gen = FIXTURES / "generate_fixtures.py"
    subprocess.run(
        [sys.executable, str(gen)],
        cwd=str(FIXTURES.parent.parent),
        check=True,
        capture_output=True,
        text=True,
    )
    return FIXTURES
