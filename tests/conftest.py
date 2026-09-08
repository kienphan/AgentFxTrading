import os
import sys
import logging
from pathlib import Path
import pytest

# Ensure TESTING environment variable is set
os.environ["TESTING"] = "1"

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
