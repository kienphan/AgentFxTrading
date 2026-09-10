import os
import sys
import logging
import shutil
import tempfile
from pathlib import Path
import pytest

# Ensure TESTING environment variable is set
os.environ["TESTING"] = "1"

# Never let a pytest run write into the production database: app.db redirects the default
# DB target (None / portfolio.db) to this throwaway SQLite file when AGENTFX_TEST_DB is set.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="agentfx-tests-")
os.environ["AGENTFX_TEST_DB"] = str(Path(_TEST_DB_DIR) / "test_portfolio.db")

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

@pytest.fixture(scope="session", autouse=True)
def isolate_test_logging():
    """
    Redirect all logging during pytest runs to logs/test.log
    so that daily live trading agent logs (logs/agent_YYYY-MM-DD.log)
    remain completely clean and free of test traffic.
    """
    from app.server import setup_agent_logging
    setup_agent_logging(level=logging.INFO, log_filename="test.log")
    yield


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database():
    """Drop the throwaway test database once the pytest session ends."""
    yield
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)
