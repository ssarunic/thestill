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
MCP Server for thestill.me

Main MCP server implementation using STDIO transport.

NOTE: This server uses stdio transport. When migrating to HTTP/SSE transport,
use LoggingMiddleware from thestill.mcp.middleware instead of the stdio adapter.
"""

import asyncio
import sys
from pathlib import Path

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server

from ..logging import configure_structlog
from ..utils.config import load_config
from .resources import setup_resources
from .tools import setup_tools

# Configure structlog for MCP server (logs go to stderr, stdout reserved for MCP protocol)
configure_structlog()
logger = structlog.get_logger(__name__)


class ThestillMCPServer:
    """
    MCP server for thestill.me podcast transcription system.

    Provides resources and tools for managing podcasts, episodes, and transcripts.
    """

    def __init__(self, storage_path: str):
        """
        Initialize the MCP server.

        Args:
            storage_path: Path to data storage directory
        """
        self.storage_path = Path(storage_path)
        self.server = Server("thestill-mcp")
        logger.info("initializing_mcp_server", storage_path=storage_path, transport="stdio")

        # Set up resources and tools
        setup_resources(self.server, storage_path)
        setup_tools(self.server, storage_path)

        logger.info("mcp_server_initialized")

    async def run(self):
        """Run the MCP server with STDIO transport."""
        logger.info("starting_mcp_server", transport="stdio")

        async with stdio_server() as (read_stream, write_stream):
            logger.info("stdio_transport_established")
            await self.server.run(read_stream, write_stream, self.server.create_initialization_options())


def main():
    """
    Main entry point for the MCP server.

    Usage:
        thestill-mcp
    """
    try:
        # Load configuration
        config = load_config()
        logger.info("configuration_loaded")

        # Create and run server
        server = ThestillMCPServer(str(config.storage_path))
        asyncio.run(server.run())

    except KeyboardInterrupt:
        logger.info("mcp_server_shutting_down", reason="keyboard_interrupt")
        sys.exit(0)
    except Exception as e:
        logger.error("fatal_error_in_mcp_server", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
