import os
import sys
from pathlib import Path

src = Path(__file__).parent.parent / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

import shutil

import pytest

from flow_mcp import guards

# The suite runs inside its own path fence in the checkout, whatever config.toml says.
TEST_ROOT = Path(os.path.realpath(Path(__file__).parent.parent / "_flow_mcp_test"))
DENIED_ROOT = TEST_ROOT / "_denied"
guards.ROOT, guards.DENIED = TEST_ROOT, (DENIED_ROOT,)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_flow_mcp_test_root():
    yield
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
