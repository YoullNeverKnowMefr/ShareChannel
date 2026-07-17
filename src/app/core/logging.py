from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import settings


def _configure_structlog() -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        timestamper,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def configure_logging() -> None:

    logging_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=logging_level,
        format="%(message)s",
        stream=sys.stdout,
    )

    _configure_structlog()


def get_logger(*args: Any, **kwargs: Any) -> structlog.stdlib.BoundLogger:

    return structlog.get_logger(*args, **kwargs)


__all__ = ["configure_logging", "get_logger"]
