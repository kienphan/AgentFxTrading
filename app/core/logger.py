"""
Structured logging setup using Loguru.
"""

import sys
from pathlib import Path
from typing import Optional
from loguru import logger as _loguru_logger

_logging_configured: bool = False
_LOG_DIR = Path("logs")


def setup_logging(
    log_level: str = "INFO",
    log_dir: Optional[Path] = None,
    rotation: str = "00:00",
    retention: str = "30 days",
    serialize_file: bool = False,
) -> None:
    """Configure Loguru sinks. Idempotent."""
    global _logging_configured, _LOG_DIR
    if _logging_configured:
        return
    if log_dir is not None:
        _LOG_DIR = log_dir
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _loguru_logger.remove()

    console_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    _loguru_logger.add(sys.stderr, format=console_fmt, level=log_level.upper(),
                       colorize=True, backtrace=True, diagnose=True)

    app_log_path = _LOG_DIR / "agentfx_{time:YYYY-MM-DD}.log"
    _loguru_logger.add(str(app_log_path),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=log_level.upper(), rotation=rotation, retention=retention,
        compression="gz", serialize=serialize_file, backtrace=True, diagnose=False, enqueue=True)

    audit_log_path = _LOG_DIR / "audit_{time:YYYY-MM-DD}.log"
    _loguru_logger.add(str(audit_log_path),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | AUDIT | {message}",
        level="DEBUG", filter=_audit_filter, rotation=rotation,
        retention="90 days", compression="gz", enqueue=True)

    _logging_configured = True


def get_logger(name: str):
    """Return a Loguru logger bound to the given module name."""
    return _loguru_logger.bind(name=name)


def get_audit_logger():
    """Return a logger bound with audit=True for trading decisions."""
    return _loguru_logger.bind(audit=True)


def _audit_filter(record: dict) -> bool:
    return record.get("extra", {}).get("audit", False)


def reset_logging() -> None:
    """Reset logging config (for tests)."""
    global _logging_configured
    _loguru_logger.remove()
    _logging_configured = False
