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

"""
Background webhook server for CLI commands.

This module provides a context manager that starts the FastAPI webhook server
in a background thread, allowing CLI commands like `transcribe` to receive
webhook callbacks from ElevenLabs without requiring a separate server process.
"""

import socket
import threading
import time
from contextlib import contextmanager
from typing import Generator, Optional

import requests
import uvicorn
from structlog import get_logger

from thestill.utils.config import Config

logger = get_logger(__name__)


def is_thestill_server_running(host: str, port: int, timeout: float = 2.0) -> bool:
    """
    Check if a thestill server is running on the specified host and port.

    Makes an HTTP request to the root endpoint and verifies the response
    identifies as "thestill.me" service.

    Args:
        host: Host to check
        port: Port to check
        timeout: Request timeout in seconds

    Returns:
        True if a thestill server is running, False otherwise
    """
    # Use localhost for checking since 0.0.0.0 binds to all interfaces
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{check_host}:{port}/"

    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            return data.get("service") == "thestill.me" and data.get("status") == "ok"
    except (requests.RequestException, ValueError):
        # Connection failed or invalid JSON
        pass

    return False


class BackgroundWebhookServer:
    """
    Manages a webhook server running in a background thread.

    This allows CLI commands to receive webhook callbacks while processing.
    The server starts on a configurable port and shuts down gracefully
    when the context manager exits.
    """

    def __init__(
        self,
        config: Config,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        """
        Initialize the background webhook server.

        Args:
            config: Application configuration
            host: Host to bind to (default: 0.0.0.0 for external access)
            port: Port to bind to (default: 8000)
        """
        self.config = config
        self.host = host
        self.port = port
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._started = threading.Event()

    def _is_port_available(self) -> bool:
        """Check if the configured port is available."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.bind((self.host, self.port))
                return True
        except OSError:
            return False

    def _run_server(self) -> None:
        """Run the uvicorn server (called in background thread)."""
        from thestill.web.app import create_app

        app = create_app(self.config)

        # Configure uvicorn with minimal logging
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        # Signal that server is starting
        self._started.set()

        # Run the server (blocks until shutdown)
        self._server.run()

    def start(self) -> bool:
        """
        Start the webhook server in a background thread.

        Returns:
            True if server started successfully, False if port unavailable
        """
        if not self._is_port_available():
            logger.warning(f"Port {self.port} is not available. Another server may be running.")
            return False

        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()

        # Wait for server to start (with timeout)
        if not self._started.wait(timeout=5.0):
            logger.error("Webhook server failed to start within timeout")
            return False

        # Give uvicorn a moment to bind the socket
        time.sleep(0.5)

        logger.info(f"Background webhook server started on http://{self.host}:{self.port}")
        return True

    def stop(self) -> None:
        """Stop the webhook server gracefully."""
        if self._server:
            self._server.should_exit = True

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Webhook server thread did not stop gracefully")

        logger.info("Background webhook server stopped")

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def webhook_url(self) -> str:
        """Get the webhook URL for ElevenLabs configuration."""
        return f"http://{self.host}:{self.port}/webhook/elevenlabs/speech-to-text"


class ExistingServerInfo:
    """Information about an existing server detected on the port."""

    def __init__(self, host: str, port: int, is_thestill: bool):
        self.host = host
        self.port = port
        self.is_thestill = is_thestill

    @property
    def webhook_url(self) -> str:
        """Get the webhook URL for ElevenLabs configuration."""
        return f"http://{self.host}:{self.port}/webhook/elevenlabs/speech-to-text"


@contextmanager
def webhook_server_context(
    config: Config,
    host: str = "0.0.0.0",
    port: int = 8000,
    required: bool = False,
) -> Generator[Optional[BackgroundWebhookServer | ExistingServerInfo], None, None]:
    """
    Context manager for running a background webhook server.

    Usage:
        with webhook_server_context(config) as server:
            if server:
                print(f"Webhook URL: {server.webhook_url}")
            # ... do transcription work ...
        # Server automatically stops when exiting context

    Args:
        config: Application configuration
        host: Host to bind to
        port: Port to bind to
        required: If True, raise exception if server fails to start

    Yields:
        BackgroundWebhookServer if started new server,
        ExistingServerInfo if thestill server already running,
        None if port unavailable but not a thestill server

    Raises:
        RuntimeError: If required=True and server fails to start
    """
    server = BackgroundWebhookServer(config, host, port)

    if server.start():
        try:
            yield server
        finally:
            server.stop()
    else:
        # Port is unavailable - check if it's a thestill server
        if is_thestill_server_running(host, port):
            logger.info(
                f"thestill server already running on port {port}. "
                "Webhook callbacks will be handled by the existing server."
            )
            yield ExistingServerInfo(host, port, is_thestill=True)
        elif required:
            raise RuntimeError(
                f"Failed to start webhook server on port {port}. "
                "Another service (not thestill) is using this port. "
                "Either stop that service or use a different port via WEBHOOK_SERVER_PORT."
            )
        else:
            # Port in use by something else, not required
            logger.warning(
                f"Port {port} is in use by another service (not thestill). "
                "Webhook callbacks may not work. Consider changing WEBHOOK_SERVER_PORT."
            )
            yield None
