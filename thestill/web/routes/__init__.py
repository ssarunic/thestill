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
Route modules for thestill.me web server.

This package contains FastAPI routers for different endpoint groups:
- health: Health check endpoint (root level, for load balancers)
- webhooks: Webhook endpoints for external service callbacks (ElevenLabs)
- auth: Authentication endpoints (Google OAuth, login/logout)
- api_status: System status and statistics
- api_dashboard: Dashboard statistics and activity
- api_podcasts: Podcast and episode listing/details (slug-based URLs)
- api_episodes: Cross-podcast episode operations
- api_commands: Command execution (refresh, download, etc.)
"""

from . import api_commands, api_dashboard, api_episodes, api_podcasts, api_status, auth, health, webhooks

__all__ = [
    "health",
    "webhooks",
    "auth",
    "api_status",
    "api_dashboard",
    "api_podcasts",
    "api_episodes",
    "api_commands",
]
