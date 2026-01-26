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

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from ..core.feed_manager import PodcastFeedManager
from ..core.progress_store import ProgressStore
from ..core.queue_manager import QueueManager
from ..core.task_handlers import create_task_handlers
from ..core.task_worker import TaskWorker
from ..repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..repositories.sqlite_user_repository import SqliteUserRepository
from ..services import FollowerService, PodcastService, RefreshService, StatsService
from ..services.auth_service import AuthService
from ..utils.config import Config, load_config
from ..utils.path_manager import PathManager
from .dependencies import AppState
from .middleware import LoggingMiddleware
from .routes import api_commands, api_dashboard, api_episodes, api_podcasts, api_status, auth, health, webhooks
from .task_manager import get_task_manager

logger = structlog.get_logger(__name__)


class CachedStaticFiles(StaticFiles):
    """
    StaticFiles with aggressive caching for hashed assets.

    Vite builds include content hashes in filenames (e.g., index-abc123.js),
    so we can safely cache them for a long time (1 year with immutable).
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # Add aggressive caching for JS/CSS files (they have content hashes)
        if path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        # Add moderate caching for other assets (images, fonts)
        elif path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ttf")):
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response


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

    # Initialize task queue and worker
    queue_manager = QueueManager(config.database_path)

    # Initialize progress store for real-time progress updates
    progress_store = ProgressStore()

    # Initialize authentication services
    user_repository = SqliteUserRepository(db_path=config.database_path)
    auth_service = AuthService(config, user_repository)

    # Initialize follower services
    follower_repository = SqlitePodcastFollowerRepository(db_path=config.database_path)
    follower_service = FollowerService(follower_repository, repository)

    # Create placeholder app_state first (task_worker needs it for handlers)
    app_state = AppState(
        config=config,
        path_manager=path_manager,
        repository=repository,
        feed_manager=feed_manager,
        podcast_service=podcast_service,
        refresh_service=refresh_service,
        stats_service=stats_service,
        task_manager=task_manager,
        queue_manager=queue_manager,
        task_worker=None,  # type: ignore  # Will be set after creation
        progress_store=progress_store,
        user_repository=user_repository,
        auth_service=auth_service,
        follower_repository=follower_repository,
        follower_service=follower_service,
    )

    # Create task worker with handlers that have access to app_state
    task_handlers = create_task_handlers(app_state)
    task_worker = TaskWorker(queue_manager, task_handlers, progress_store=progress_store, repository=repository)
    app_state.task_worker = task_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan manager for startup/shutdown."""
        import asyncio

        logger.info("starting_web_server")
        logger.info("server_configuration", storage_path=str(config.storage_path), database=str(config.database_path))

        # Recover any tasks that were interrupted by a previous server restart
        # Exclude transcribe tasks if using cloud providers (they may still be running)
        from ..core.queue_manager import TaskStage

        excluded_stages = []
        if config.transcription_provider.lower() in ("google", "elevenlabs"):
            excluded_stages.append(TaskStage.TRANSCRIBE)
            logger.info(
                "cloud_transcription_provider",
                provider=config.transcription_provider,
                note="transcribe_tasks_not_auto_recovered",
            )

        recovered = queue_manager.recover_interrupted_tasks(excluded_stages=excluded_stages)
        if recovered > 0:
            logger.info("recovered_interrupted_tasks", count=recovered)

        # Store state in app for access in routes
        app.state.app_state = app_state

        # In single-user mode, ensure the default user follows all existing podcasts
        # This handles the case where podcasts were added before follower support
        if not config.multi_user:
            default_user = auth_service.get_or_create_default_user()
            all_podcasts = repository.get_all()
            followed_ids = set(follower_repository.get_followed_podcast_ids(default_user.id))

            podcasts_to_follow = [p for p in all_podcasts if p.id not in followed_ids]
            if podcasts_to_follow:
                for podcast in podcasts_to_follow:
                    try:
                        follower_service.follow(default_user.id, podcast.id)
                        logger.info("auto_followed_podcast", podcast_title=podcast.title, user_id=default_user.id)
                    except Exception as e:
                        logger.warning("auto_follow_failed", podcast_title=podcast.title, error=str(e))
                logger.info("single_user_auto_follow_complete", count=len(podcasts_to_follow))

        # Set event loop in progress store for cross-thread async operations
        progress_store.set_event_loop(asyncio.get_event_loop())

        # Start background task worker
        task_worker.start()
        logger.info("task_worker_started")

        yield

        # Cleanup on shutdown
        logger.info("shutting_down_web_server")

        # Stop task worker gracefully
        task_worker.stop()
        logger.info("task_worker_stopped")

    # Create FastAPI application
    app = FastAPI(
        title="thestill.me",
        description="Automated podcast transcription and summarization pipeline",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Add logging middleware for request/response tracking
    app.add_middleware(LoggingMiddleware)

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
    # Health check at root level (infrastructure convention for load balancers)
    app.include_router(health.router, tags=["health"])
    app.include_router(webhooks.router, prefix="/webhook", tags=["webhooks"])

    # API routes for web UI (all under /api prefix)
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(api_status.router, prefix="/api/status", tags=["status"])
    app.include_router(api_dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
    app.include_router(api_podcasts.router, prefix="/api/podcasts", tags=["podcasts"])
    app.include_router(api_episodes.router, prefix="/api/episodes", tags=["episodes"])
    app.include_router(api_commands.router, prefix="/api/commands", tags=["commands"])

    # Serve static frontend files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        # Mount static assets (JS, CSS) with aggressive caching
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", CachedStaticFiles(directory=str(assets_dir)), name="assets")

        # Catch-all route for SPA - must be after API routes
        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            """Serve the SPA index.html for all non-API routes."""
            # Skip if it's an API or known route
            if full_path.startswith(("api/", "webhook/", "docs", "redoc", "openapi.json", "health")):
                return None
            index_file = static_dir / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
            return FileResponse(str(static_dir / "index.html"))

        logger.info("serving_static_frontend", directory=str(static_dir))
    else:
        logger.warning("static_directory_not_found", directory=str(static_dir), note="frontend_not_available")

    logger.info("fastapi_application_created")

    return app
