# Copyright 2025 thestill.me
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Structured logging configuration for thestill.

This module provides production-grade structured logging using structlog,
with support for multiple output formats and cloud-native observability
platforms (AWS Elastic, GCP Cloud Logging).

Environment Variables:
    LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Default: INFO
    LOG_FORMAT: Output format (console, json, ecs, gcp, auto). Default: auto
    LOG_FILE: Optional file path for log output. Default: None (stderr only)

Example:
    from thestill.logging import configure_structlog, get_logger

    configure_structlog()
    logger = get_logger(__name__)
    logger.info("Episode processed", episode_id=123, duration_ms=4500)
"""

import logging
import os
import sys
from typing import Any, List

import structlog
from structlog.dev import ConsoleRenderer
from structlog.processors import JSONRenderer

# Optional cloud integrations - import only if available
try:
    import ecs_logging

    HAS_ECS = True
except ImportError:
    HAS_ECS = False

try:
    import structlog_gcp

    HAS_GCP = True
except ImportError:
    HAS_GCP = False


def _cloudwatch_processor(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Process log events for AWS CloudWatch Logs Insights compatibility.

    CloudWatch Logs automatically parses JSON, but this processor ensures:
    - 'message' field exists (alias for 'event', expected by CloudWatch dashboards)
    - '@timestamp' field exists (alias for 'timestamp', CloudWatch convention)
    - 'level' is uppercase (INFO, ERROR) matching AWS severity conventions

    CloudWatch Logs Insights uses dot notation for nested fields, so we keep
    the structure flat where possible for easier querying.

    Args:
        logger: The wrapped logger object (unused)
        method_name: The name of the method called on the logger (unused)
        event_dict: The event dictionary to process

    Returns:
        The processed event dictionary with CloudWatch-compatible field names
    """
    # Rename 'event' to 'message' (CloudWatch convention)
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")

    # Rename timestamp to @timestamp (CloudWatch convention)
    if "timestamp" in event_dict:
        event_dict["@timestamp"] = event_dict.pop("timestamp")

    # Ensure level is uppercase (CloudWatch severity convention)
    if "level" in event_dict:
        event_dict["level"] = event_dict["level"].upper()

    return event_dict


def get_log_level() -> int:
    """Get log level from environment variable.

    Returns:
        Logging level constant from logging module (e.g., logging.INFO)
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


def get_log_format() -> str:
    """Get log format from environment variable.

    Formats:
        - console: Colored output for development (default for TTY)
        - json: JSON output for production
        - ecs: Elastic Common Schema (AWS Elastic Stack)
        - gcp: Google Cloud Logging format
        - cloudwatch: AWS CloudWatch Logs format (simpler alternative to ECS)
        - auto: console if TTY, json otherwise (default)

    Returns:
        Format string: 'console', 'json', 'ecs', 'gcp', or 'cloudwatch'
    """
    format_str = os.getenv("LOG_FORMAT", "auto").lower()
    if format_str == "auto":
        return "console" if sys.stderr.isatty() else "json"
    return format_str


def _get_renderer(log_format: str) -> Any:
    """Get the appropriate renderer based on log format.

    Args:
        log_format: Format string ('console', 'json', 'ecs')
        Note: 'gcp' format is handled separately in configure_structlog()

    Returns:
        Structlog processor for rendering logs

    Raises:
        ValueError: If format requires missing dependency
    """
    if log_format == "console":
        return ConsoleRenderer(colors=True)

    if log_format == "json":
        return JSONRenderer()

    if log_format == "ecs":
        if not HAS_ECS:
            raise ValueError("ECS format requires 'ecs-logging' package. Install with: pip install ecs-logging")
        return ecs_logging.StructlogFormatter()

    # Default to JSON for unknown formats
    return JSONRenderer()


def configure_structlog() -> None:
    """Configure structlog based on environment variables.

    This function should be called once at application startup, before
    any loggers are created. It reads LOG_LEVEL, LOG_FORMAT, and LOG_FILE
    environment variables to configure the logging system.

    The logging output goes to stderr by default, with optional file output
    if LOG_FILE is set. This keeps stdout clean for user-facing output and
    MCP protocol communication.

    Example:
        # In your main application entry point
        from thestill.logging import configure_structlog

        configure_structlog()
        # Now all loggers will use the configured format
    """
    log_level = get_log_level()
    log_format = get_log_format()
    log_file = os.getenv("LOG_FILE")

    # GCP format uses its own complete processor chain
    if log_format == "gcp":
        if not HAS_GCP:
            raise ValueError("GCP format requires 'structlog-gcp' package. Install with: pip install structlog-gcp")

        # Get service info from environment
        service_name = os.getenv("SERVICE_NAME", "thestill")
        service_version = os.getenv("SERVICE_VERSION", "1.0.0")

        # Build GCP-specific processors (includes JSON renderer)
        processors = structlog_gcp.build_processors(service=service_name, version=service_version)
    else:
        # Shared processors for console/json/ecs/cloudwatch formats
        # Order matters: processors run sequentially on each log message
        shared_processors: List[Any] = [
            # 1. Merge context variables: Automatically includes correlation IDs
            #    (request_id, command_id, mcp_request_id, task_id, etc.) that were
            #    bound via structlog.contextvars.bind_contextvars() in middleware/decorators.
            #    This is the magic that enables automatic correlation across layers!
            structlog.contextvars.merge_contextvars,
            # 2. Add log level as a field
            structlog.processors.add_log_level,
            # 3. Add ISO8601 timestamp
            structlog.processors.TimeStamper(fmt="iso"),
            # 4. Include stack traces for exc_info=True logs
            structlog.processors.StackInfoRenderer(),
        ]

        # Add format-specific renderer
        if log_format == "cloudwatch":
            # CloudWatch format: shared processors + field renaming + JSON output
            processors = shared_processors + [_cloudwatch_processor, JSONRenderer()]
        else:
            processors = shared_processors + [_get_renderer(log_format)]

    # Configure file output if LOG_FILE is set
    # Must be done before structlog.configure() to set up the logging infrastructure
    if log_file:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Configure root logger for file output
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        # Add stderr handler (replaces PrintLoggerFactory)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(log_level)
        stderr_handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(stderr_handler)

        # Add file handler with rotation (100MB max, 5 backups)
        file_handler = RotatingFileHandler(log_file, maxBytes=100 * 1024 * 1024, backupCount=5)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(file_handler)

        # Use stdlib logger factory when file output is enabled
        # This routes structlog through standard logging infrastructure
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # No file output - use simple PrintLoggerFactory for stderr only
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger instance.

    Args:
        name: Logger name, typically __name__ of the calling module

    Returns:
        Configured structlog logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Processing started", episode_id=123)
        logger.error("Processing failed", episode_id=123, error=str(e))
    """
    return structlog.get_logger(name)
