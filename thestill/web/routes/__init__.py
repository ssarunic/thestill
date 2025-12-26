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
- health: Health check and status endpoints
- webhooks: Webhook endpoints for external service callbacks (ElevenLabs)
- api_dashboard: Dashboard statistics and activity
- api_podcasts: Podcast and episode listing/details (slug-based URLs)
- api_commands: Command execution (refresh, download, etc.)
"""

from . import api_commands, api_dashboard, api_podcasts, health, webhooks

__all__ = ["health", "webhooks", "api_dashboard", "api_podcasts", "api_commands"]
