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
Health check endpoint for Thestill web server.

This endpoint is mounted at the root level (not under /api) because:
- Load balancers and Kubernetes probes expect /health or /healthz at root
- Infrastructure endpoints follow different conventions than application APIs
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from structlog import get_logger

from ..dependencies import AppState, get_app_state
from ..responses import api_response

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.

    Liveness only — no dependency checks, so it stays cheap for probes
    that fire every few seconds. Readiness (DB reachability) is
    /health/ready.

    Returns:
        Health status with timestamp.
    """
    return api_response({}, status="healthy")


@router.get("/health/ready")
def readiness_check(state: AppState = Depends(get_app_state)):
    """
    Readiness probe (spec #66): one cheap DB round-trip against the
    configured backend. Compose healthchecks and future ALB target groups
    point here so a container with a dead database stops reporting ready.

    Deliberately a sync handler: FastAPI runs it in the threadpool, so the
    blocking psycopg/sqlite connect (up to its 3s timeout) can never stall
    the event loop — this endpoint is unauthenticated and probed repeatedly.

    Returns:
        200 with status "ready", or 503 with status "unready". The failure
        detail goes to the log, not the response — this endpoint is
        unauthenticated and must not leak DSNs or driver internals.
    """
    service = state.health_service
    if service is None:
        # Hand-built test fixtures may not wire the service; construct on
        # the fly from config so the probe still answers truthfully.
        from ..services import HealthService

        service = HealthService(state.config)
    try:
        service.ping_database()
    except Exception as exc:  # noqa: BLE001 — any failure means "not ready"
        logger.warning("readiness_check_failed", error=str(exc), exc_info=True)
        return JSONResponse(status_code=503, content=api_response({}, status="unready"))
    return api_response({}, status="ready")
