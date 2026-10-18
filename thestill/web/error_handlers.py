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

"""Spec #104 — HTTP mapping for policy refusals raised anywhere below a route.

A refusal can surface from a dependency (a disabled account), a service
called by a ``GET`` (a paused translation) or a background helper, so one
handler maps them all instead of a try/except in every route. The body is
``{"detail": {...}}`` — the shape ``HTTPException(detail=dict)`` produces,
which the frontend already parses (``error.detail.error``).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from structlog import get_logger

from ..services.abuse.errors import AbusePreventionError

logger = get_logger(__name__)


def register_abuse_handlers(app: FastAPI) -> None:
    @app.exception_handler(AbusePreventionError)
    async def _abuse_refusal(request: Request, exc: AbusePreventionError) -> JSONResponse:  # noqa: ANN001
        logger.info("request_refused", error=type(exc).__name__, status_code=exc.status_code)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail()})
