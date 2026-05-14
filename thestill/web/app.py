# Copyright 2025-2026 Thestill
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
FastAPI application factory for Thestill web server.

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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from ..core.feed_manager import PodcastFeedManager
from ..core.progress_store import ProgressStore
from ..core.queue_manager import QueueManager
from ..core.task_handlers import create_task_handlers
from ..core.task_worker import TaskWorker
from ..repositories.sqlite_briefing_repository import SqliteBriefingRepository
from ..repositories.sqlite_inbox_repository import SqliteInboxRepository
from ..repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..repositories.sqlite_user_repository import SqliteUserRepository
from ..services import FollowerService, PodcastService, RefreshService, StatsService
from ..services.auth_service import AuthService
from ..services.briefing_generator import BriefingGenerator
from ..services.briefing_service import BriefingService
from ..services.import_service import ImportService
from ..services.inbox_service import InboxService
from ..services.narration import NarrationGenerator, NarrationRunner
from ..utils.config import Config, load_config
from ..utils.path_manager import PathManager
from .dependencies import AppState
from .middleware import BodySizeLimitMiddleware, LoggingMiddleware, SecurityHeadersMiddleware
from .routes import (
    api_briefings,
    api_commands,
    api_dashboard,
    api_entities,
    api_episodes,
    api_imports,
    api_inbox,
    api_narrations,
    api_podcasts,
    api_search,
    api_status,
    api_top_podcasts,
    api_transcript_words,
    auth,
    health,
    webhooks,
)
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


def _build_narration_runner(
    config: Config,
    path_manager: PathManager,
    podcast_repository: SqlitePodcastRepository,
    briefing_repository: SqliteBriefingRepository,
) -> Optional[NarrationRunner]:
    """Construct a ``NarrationRunner`` when narration is enabled (spec #33).

    Returns ``None`` when ``config.narration_enabled`` is False or the
    LLM provider cannot be initialised — the API surface returns 503
    in that mode so callers can handle the rollout gate cleanly.
    """
    if not config.narration_enabled:
        return None
    from ..core.llm_provider import create_llm_provider_from_config

    try:
        llm_provider = create_llm_provider_from_config(config)
    except Exception as exc:  # noqa: BLE001 — surface the gate, don't crash the server
        logger.warning("narration.runner_disabled", reason=str(exc))
        return None
    generator = NarrationGenerator(
        path_manager=path_manager,
        file_storage=config.file_storage,
        llm_provider=llm_provider,
    )
    return NarrationRunner(
        generator=generator,
        briefing_repository=briefing_repository,
        podcast_repository=podcast_repository,
    )


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
    feed_manager = PodcastFeedManager(
        repository,
        path_manager,
        max_workers=config.refresh_max_workers,
        max_per_host=config.refresh_max_per_host,
    )
    podcast_service = PodcastService(config.storage_path, repository, path_manager, file_storage=config.file_storage)
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

    # Initialize follower + inbox services. The inbox service is injected
    # into FollowerService so ``follow`` can seed the new follower's inbox.
    follower_repository = SqlitePodcastFollowerRepository(db_path=config.database_path)
    inbox_repository = SqliteInboxRepository(db_path=config.database_path)
    inbox_service = InboxService.from_config(config, inbox_repository, follower_repository)
    follower_service = FollowerService(follower_repository, repository, inbox_service=inbox_service)

    import_service = ImportService(
        repository=repository,
        inbox_repository=inbox_repository,
        queue_manager=queue_manager,
        feed_manager=feed_manager,
    )

    # Initialize briefing repository
    briefing_repository = SqliteBriefingRepository(db_path=config.database_path)

    # Spec #40 — pending transcription operations now live in SQLite.
    from ..repositories.sqlite_pending_operations_repository import SqlitePendingOperationsRepository

    pending_ops_repository = SqlitePendingOperationsRepository(db_path=config.database_path)

    # User-facing "Today's briefing" runs through the briefing path with
    # inbox-driven selection (cursor = previous briefing's ``period_end``).
    briefing_service = BriefingService.from_config(
        config,
        briefing_repository,
        inbox_repository,
        repository,
        BriefingGenerator(path_manager, config.file_storage),
        path_manager,
    )

    # Spec #28 — entity-layer repository. Schema is created by the
    # podcast repo's migration block; this just opens connections.
    from ..core.embedding_model import EmbeddingModel
    from ..repositories.sqlite_entity_repository import SqliteEntityRepository
    from ..search.sqlite_vec_client import SqliteVecBackend

    entity_repository = SqliteEntityRepository(db_path=config.database_path)
    # Spec #28 §2.10 — eager construction of both the wrapper and the
    # backend; sentence-transformers itself only loads inside
    # EmbeddingModel.encode_one() on the first semantic/hybrid call.
    embedding_model = EmbeddingModel(config.embedding_model)
    search_backend = SqliteVecBackend(
        db_path=config.database_path,
        embedding_model=embedding_model,
    )

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
        inbox_repository=inbox_repository,
        inbox_service=inbox_service,
        import_service=import_service,
        briefing_repository=briefing_repository,
        briefing_service=briefing_service,
        pending_ops_repository=pending_ops_repository,
        entity_repository=entity_repository,
        search_backend=search_backend,
        embedding_model=embedding_model,
        narration_runner=_build_narration_runner(config, path_manager, repository, briefing_repository),
    )

    # Create task worker with handlers that have access to app_state.
    # Each TaskStage gets its own poll loop + semaphore so slow stages
    # (transcribe) don't starve fast ones (clean).
    task_handlers = create_task_handlers(app_state)
    task_worker = TaskWorker(
        queue_manager,
        task_handlers,
        progress_store=progress_store,
        repository=repository,
        parallel_jobs=config.parallel_jobs,
        parallel_jobs_per_stage=config.get_parallel_jobs_per_stage(),
    )
    app_state.task_worker = task_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan manager for startup/shutdown."""
        import asyncio

        logger.info("starting_web_server")
        logger.info("server_configuration", storage_path=str(config.storage_path), database=str(config.database_path))

        # Fail fast on misconfigured transcription provider. In slim Docker
        # deployments this catches the .env.example default
        # (TRANSCRIPTION_PROVIDER=whisper) before any episode is processed.
        from ..core.transcriber_factory import validate_transcription_provider

        validate_transcription_provider(config)

        # Recover any tasks that were interrupted by a previous server restart
        # Exclude transcribe tasks if using cloud providers (they may still be running)
        from ..core.queue_manager import TaskStage

        excluded_stages = []
        if config.transcription_provider.lower() in ("google", "elevenlabs", "dalston"):
            excluded_stages.append(TaskStage.TRANSCRIBE)
            logger.info(
                "cloud_transcription_provider",
                provider=config.transcription_provider,
                note="transcribe_tasks_not_auto_recovered",
            )

        recovered = queue_manager.recover_interrupted_tasks(excluded_stages=excluded_stages)
        if recovered > 0:
            logger.info("recovered_interrupted_tasks", count=recovered)

        # Dalston jobs run server-side and survive a thestill restart. The
        # ``DalstonTranscriber`` resume path checks ``pending_ops_repository``
        # for an existing job_id keyed by episode and re-polls instead of
        # submitting a duplicate. Flip the transcribe rows that ``recover_interrupted_tasks``
        # just preserved (excluded from the failed-state recovery) back to
        # ``pending`` so the worker re-claims them immediately instead of
        # waiting out the stale-task sweep.
        if config.transcription_provider.lower() == "dalston":
            requeued = queue_manager.reset_stale_tasks(
                timeout_minutes=0,
                stages=[TaskStage.TRANSCRIBE],
            )
            if requeued > 0:
                logger.info("dalston_transcribe_tasks_requeued_for_resume", count=requeued)

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

        # Warm the embedding model in the background. The first
        # semantic/hybrid search request would otherwise pay a 5-30s
        # cold-load (model deserialization, plus the HuggingFace
        # download on a fresh cache). Running it on a daemon thread
        # keeps boot non-blocking; ``EmbeddingModel._get_model`` is
        # lock-guarded so a search arriving mid-warmup waits for the
        # in-progress load instead of starting a second one.
        import threading as _threading

        _threading.Thread(
            target=embedding_model.warmup,
            name="embedding-model-warmup",
            daemon=True,
        ).start()
        logger.info("embedding_model_warmup_scheduled", model=embedding_model.model_name)

        yield

        # Cleanup on shutdown
        logger.info("shutting_down_web_server")

        # Stop task worker gracefully
        task_worker.stop()
        logger.info("task_worker_stopped")

    # /docs and /redoc are off by default in production.
    # Flip ENABLE_DOCS=true (or ENVIRONMENT=development) to re-enable them.
    _is_dev = config.environment == "development"
    docs_enabled = _is_dev or config.enable_docs
    app = FastAPI(
        title="Thestill",
        description="Automated podcast transcription and summarization pipeline",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Sanitise the default exception response so we don't leak exception
    # messages / tracebacks to clients in production. Logs keep full
    # detail server-side.
    @app.exception_handler(Exception)
    async def _generic_exception_handler(request: Request, exc: Exception):  # noqa: ANN001
        logger.exception("unhandled_exception", path=str(request.url.path), error_type=type(exc).__name__)
        if _is_dev:
            return JSONResponse(
                status_code=500,
                content={"detail": f"{type(exc).__name__}: {exc}"},
            )
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    # Add logging middleware for request/response tracking
    app.add_middleware(LoggingMiddleware)

    # spec #25 item 3.1: defence-in-depth response headers (CSP, HSTS,
    # X-Frame-Options, X-Content-Type-Options, Referrer-Policy).
    app.add_middleware(SecurityHeadersMiddleware, is_production=not _is_dev)

    # spec #25 item 3.7: application-layer body-size cap. Webhooks get the
    # tighter webhook cap; everything else falls back to the default
    # (matches the webhook cap so nothing accidentally ships unlimited).
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=config.max_webhook_body_bytes,
        route_limits=[("/webhook/", config.max_webhook_body_bytes)],
    )

    # CORS: origins come from ALLOWED_ORIGINS env,
    # methods and headers are explicit. In development we fall back to the
    # Vite dev server ports so local work still functions.
    cors_origins = list(config.allowed_origins)
    if _is_dev and not cors_origins:
        cors_origins = [
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
        ]
    # Reject credentialed wildcard at startup (post-review hardening of
    # ). Browsers spec-forbid Access-Control-Allow-Credentials
    # with Access-Control-Allow-Origin: *, and Starlette would echo whatever
    # origin asked, so ALLOWED_ORIGINS="*" is a footgun — refuse it.
    if any(origin.strip() == "*" for origin in cors_origins):
        raise ValueError(
            "ALLOWED_ORIGINS='*' is not permitted: the web app issues "
            "credentialed cookies, and wildcard origins with credentials "
            "violate the CORS spec. Enumerate origins explicitly."
        )
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Accept-Language",
                "Authorization",
                "Content-Language",
                "Content-Type",
                "X-Requested-With",
            ],
            max_age=600,
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
    # spec #38 karaoke wipe: separate file, but URL pattern slots in next to
    # api_podcasts so it mounts under the same /api/podcasts prefix.
    app.include_router(api_transcript_words.router, prefix="/api/podcasts", tags=["transcript-words"])
    app.include_router(api_top_podcasts.router, prefix="/api/top-podcasts", tags=["top-podcasts"])
    app.include_router(api_episodes.router, prefix="/api/episodes", tags=["episodes"])
    # Spec #28 §5.2 — episode-page entity UX (mention list per episode +
    # entity summary). Routes span /api/episodes/.../entities and
    # /api/entities/..., so the router declares full paths internally
    # and mounts under the bare /api prefix.
    app.include_router(api_entities.router, prefix="/api", tags=["entities"])
    # Spec #28 §2.10 — corpus search (REST mirror of search_corpus MCP tool).
    app.include_router(api_search.router, prefix="/api/search", tags=["search"])
    app.include_router(api_briefings.router, prefix="/api/briefings", tags=["briefings"])
    app.include_router(api_inbox.router, prefix="/api/inbox", tags=["inbox"])
    app.include_router(api_narrations.router, prefix="/api/narrations", tags=["narrations"])
    app.include_router(api_imports.router, prefix="/api/imports", tags=["imports"])
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
            """Serve the SPA index.html for all non-API routes.

            ``index.html`` references the hashed JS/CSS chunks by filename, so
            it itself is the only file that must never be cached — otherwise
            the browser keeps the old shell after a redeploy and never loads
            the freshly-built chunks. The hashed assets under /assets are
            cached aggressively by ``CachedStaticFiles``; here we explicitly
            opt out for the shell.
            """
            # Skip if it's an API or known route
            if full_path.startswith(("api/", "webhook/", "docs", "redoc", "openapi.json", "health")):
                return None
            index_file = static_dir / "index.html"
            target = index_file if index_file.exists() else (static_dir / "index.html")
            return FileResponse(
                str(target),
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        logger.info("serving_static_frontend", directory=str(static_dir))
    else:
        logger.warning("static_directory_not_found", directory=str(static_dir), note="frontend_not_available")

    logger.info("fastapi_application_created")

    return app
