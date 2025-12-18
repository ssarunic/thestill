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
FastAPI application factory for thestill.me web server.

This module creates and configures the FastAPI application with:
- Dependency injection for services (same as CLI)
- Route registration for webhooks and API endpoints
- Middleware configuration
- Error handling

Usage:
    from thestill.web.app import create_app
    app = create_app()
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..services import PodcastService, RefreshService, StatsService
from ..utils.config import Config, load_config
from ..utils.path_manager import PathManager
from .dependencies import AppState
from .routes import health, webhooks

logger = logging.getLogger(__name__)


def create_app(config: Optional[Config] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    This factory function initializes all services and registers routes.
    It follows the same dependency injection pattern as the CLI.

    Args:
        config: Optional Config object. If not provided, loads from environment.

    Returns:
        Configured FastAPI application instance.
    """
    # Load configuration if not provided
    if config is None:
        config = load_config()

    # Initialize shared services (same pattern as CLI)
    path_manager = PathManager(str(config.storage_path))
    repository = SqlitePodcastRepository(db_path=config.database_path)
    podcast_service = PodcastService(config.storage_path, repository, path_manager)
    stats_service = StatsService(config.storage_path, repository, path_manager)

    # Create application state for dependency injection
    app_state = AppState(
        config=config,
        path_manager=path_manager,
        repository=repository,
        podcast_service=podcast_service,
        stats_service=stats_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan manager for startup/shutdown."""
        logger.info("Starting thestill web server...")
        logger.info(f"Storage path: {config.storage_path}")
        logger.info(f"Database: {config.database_path}")

        # Store state in app for access in routes
        app.state.app_state = app_state

        yield

        # Cleanup on shutdown
        logger.info("Shutting down thestill web server...")

    # Create FastAPI application
    app = FastAPI(
        title="thestill.me",
        description="Automated podcast transcription and summarization pipeline",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Register routes
    app.include_router(health.router, tags=["health"])
    app.include_router(webhooks.router, prefix="/webhook", tags=["webhooks"])

    logger.info("FastAPI application created successfully")

    return app
