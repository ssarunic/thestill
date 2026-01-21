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
Health check endpoint for thestill.me web server.

This endpoint is mounted at the root level (not under /api) because:
- Load balancers and Kubernetes probes expect /health or /healthz at root
- Infrastructure endpoints follow different conventions than application APIs
"""

from fastapi import APIRouter

from ..responses import api_response

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.

    Returns:
        Health status with timestamp.
    """
    return api_response({}, status="healthy")
