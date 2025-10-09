"""
MCP Server for thestill.ai

Main MCP server implementation using STDIO transport.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server

from ..utils.config import load_config
from ..utils.logger import setup_logger
from .resources import setup_resources
from .tools import setup_tools

# Configure logging to stderr only (stdout is reserved for MCP protocol)
setup_logger("thestill.mcp", log_level="INFO", console_output=True)
logger = logging.getLogger(__name__)


class ThestillMCPServer:
    """
    MCP server for thestill.ai podcast transcription system.

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
        logger.info(f"Initializing MCP server with storage: {storage_path}")

        # Set up resources and tools
        setup_resources(self.server, storage_path)
        setup_tools(self.server, storage_path)

        logger.info("MCP server initialized successfully")

    async def run(self):
        """Run the MCP server with STDIO transport."""
        logger.info("Starting MCP server with STDIO transport")

        async with stdio_server() as (read_stream, write_stream):
            logger.info("STDIO transport established")
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )


def main():
    """
    Main entry point for the MCP server.

    Usage:
        thestill-mcp
    """
    try:
        # Load configuration
        config = load_config()
        logger.info("Configuration loaded successfully")

        # Create and run server
        server = ThestillMCPServer(str(config.storage_path))
        asyncio.run(server.run())

    except KeyboardInterrupt:
        logger.info("MCP server shutting down (KeyboardInterrupt)")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error in MCP server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
