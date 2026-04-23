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

"""Web middleware for logging and request tracking."""

from thestill.web.middleware.body_size import BodySizeLimitMiddleware
from thestill.web.middleware.logging_middleware import LoggingMiddleware
from thestill.web.middleware.rate_limit import (
    AUTH_LIMIT,
    MCP_MUTATION_LIMIT,
    WEBHOOK_LIMIT,
    RateLimit,
    RateLimitExceeded,
    enforce_mcp_mutation_quota,
    rate_limit_dependency,
)
from thestill.web.middleware.rate_limit import reset_for_testing as reset_rate_limits_for_testing
from thestill.web.middleware.rate_limit import trusted_proxy_set
from thestill.web.middleware.security_headers import SecurityHeadersMiddleware

__all__ = [
    "AUTH_LIMIT",
    "BodySizeLimitMiddleware",
    "LoggingMiddleware",
    "MCP_MUTATION_LIMIT",
    "RateLimit",
    "RateLimitExceeded",
    "SecurityHeadersMiddleware",
    "WEBHOOK_LIMIT",
    "enforce_mcp_mutation_quota",
    "rate_limit_dependency",
    "reset_rate_limits_for_testing",
    "trusted_proxy_set",
]
