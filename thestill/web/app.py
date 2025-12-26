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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core.feed_manager import PodcastFeedManager
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..services import PodcastService, RefreshService, StatsService
from ..utils.config import Config, load_config
from ..utils.path_manager import PathManager
from .dependencies import AppState
from .routes import api_commands, api_dashboard, api_podcasts, health, webhooks
from .task_manager import get_task_manager

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
    feed_manager = PodcastFeedManager(repository, path_manager)
    podcast_service = PodcastService(config.storage_path, repository, path_manager)
    refresh_service = RefreshService(feed_manager, podcast_service)
    stats_service = StatsService(config.storage_path, repository, path_manager)
    task_manager = get_task_manager()

    # Create application state for dependency injection
    app_state = AppState(
        config=config,
        path_manager=path_manager,
        repository=repository,
        feed_manager=feed_manager,
        podcast_service=podcast_service,
        refresh_service=refresh_service,
        stats_service=stats_service,
        task_manager=task_manager,
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

    # Add CORS middleware for frontend development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite dev server
            "http://localhost:3000",  # Alternative dev port
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(health.router, tags=["health"])
    app.include_router(webhooks.router, prefix="/webhook", tags=["webhooks"])

    # API routes for web UI
    app.include_router(api_dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
    app.include_router(api_podcasts.router, prefix="/api/podcasts", tags=["podcasts"])
    app.include_router(api_commands.router, prefix="/api/commands", tags=["commands"])

    # Serve static frontend files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        # Mount static assets (JS, CSS)
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # Catch-all route for SPA - must be after API routes
        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """Serve the SPA index.html for all non-API routes."""
            # Skip if it's an API or known route
            if full_path.startswith(("api/", "webhook/", "docs", "redoc", "openapi.json", "health", "status")):
                return None
            index_file = static_dir / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
            return FileResponse(str(static_dir / "index.html"))

        logger.info(f"Serving static frontend from: {static_dir}")
    else:
        logger.warning(f"Static directory not found: {static_dir} - frontend not available")

    logger.info("FastAPI application created successfully")

    return app
