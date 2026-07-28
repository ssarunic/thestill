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

"""Default-deny auth audit over the real app's route table.

Builds the app via ``create_app`` (the same registration path production
uses) and introspects every route's dependency graph:

- every ``/api`` route outside ``/api/auth`` must carry ``require_auth``
  (added at router registration in ``app.py``);
- the operator-only surface must carry ``require_admin``;
- the deliberately public routes (health, unsubscribe, HMAC-verified
  webhook ingress, auth flow) must NOT carry ``require_auth``.

This is the regression net for the bug class that motivated the sweep:
adding a new router (or endpoint) to ``app.py`` without auth now fails
here loudly instead of shipping an open endpoint.
"""

from fastapi.routing import APIRoute

from thestill.web.dependencies import require_admin, require_auth

# Every (method, path) that must be admin-gated. Grow this list when new
# operator surfaces are added; shrinking it is a conscious security call.
ADMIN_ROUTES = {
    ("POST", "/api/commands/refresh"),
    ("POST", "/api/commands/download"),
    ("POST", "/api/commands/downsample"),
    ("POST", "/api/commands/transcribe"),
    ("POST", "/api/commands/clean"),
    ("POST", "/api/commands/summarize"),
    ("POST", "/api/commands/run-pipeline"),
    ("POST", "/api/commands/episode/{episode_id}/cancel-pipeline"),
    ("GET", "/api/commands/queue/status"),
    ("GET", "/api/commands/queue/tasks"),
    ("POST", "/api/commands/queue/task/{task_id}/bump"),
    ("POST", "/api/commands/queue/task/{task_id}/cancel"),
    ("GET", "/api/commands/dlq"),
    ("POST", "/api/commands/dlq/{task_id}/retry"),
    ("POST", "/api/commands/dlq/{task_id}/skip"),
    ("POST", "/api/commands/dlq/retry-all"),
    ("POST", "/api/episodes/bulk/process"),
    ("POST", "/api/episodes/{episode_id}/retry"),
    ("GET", "/api/entities/review-queue"),
    ("POST", "/api/entities/corrections"),
    ("GET", "/api/status"),
    ("GET", "/api/dashboard/stats"),
    ("GET", "/api/dashboard/activity"),
    ("GET", "/api/dashboard/narration"),
    ("GET", "/webhook/elevenlabs/results"),
    ("GET", "/webhook/elevenlabs/results/{transcription_id}"),
    ("DELETE", "/webhook/elevenlabs/results/{transcription_id}"),
}

# Routes that must stay reachable without a session.
PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("GET", "/unsubscribe/briefings"),
    ("POST", "/unsubscribe/briefings"),
    ("POST", "/webhook/elevenlabs/speech-to-text"),
}


def _dependency_callables(dependant, acc=None):
    """Collect every dependency callable in a route's dependant tree."""
    if acc is None:
        acc = set()
    for dep in dependant.dependencies:
        acc.add(dep.call)
        _dependency_callables(dep, acc)
    return acc


def _api_routes(app):
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route


def test_every_api_route_requires_auth(client):
    """No /api route outside /api/auth may be reachable without a session."""
    unguarded = []
    for route in _api_routes(client.app):
        if not route.path.startswith("/api/") or route.path.startswith("/api/auth"):
            continue
        deps = _dependency_callables(route.dependant)
        if require_auth not in deps and require_admin not in deps:
            unguarded.extend((m, route.path) for m in route.methods)
    assert not unguarded, f"routes missing require_auth: {sorted(unguarded)}"


def test_admin_surface_requires_admin(client):
    """The operator-only endpoints must all carry require_admin."""
    admin_gated = set()
    for route in _api_routes(client.app):
        if require_admin in _dependency_callables(route.dependant):
            admin_gated.update((m, route.path) for m in route.methods)
    missing = ADMIN_ROUTES - admin_gated
    assert not missing, f"admin routes missing require_admin: {sorted(missing)}"


def test_no_unexpected_admin_gating(client):
    """Admin gating beyond ADMIN_ROUTES means this list (and the docs) are stale."""
    admin_gated = set()
    for route in _api_routes(client.app):
        if require_admin in _dependency_callables(route.dependant):
            admin_gated.update((m, route.path) for m in route.methods)
    unexpected = {(m, p) for (m, p) in admin_gated if m != "HEAD"} - ADMIN_ROUTES
    assert not unexpected, f"unexpected admin-gated routes: {sorted(unexpected)}"


def test_public_routes_stay_public(client):
    """Health, unsubscribe, and HMAC webhook ingress must not require a session."""
    for route in _api_routes(client.app):
        for method in route.methods:
            if (method, route.path) in PUBLIC_ROUTES:
                deps = _dependency_callables(route.dependant)
                assert require_auth not in deps, f"{method} {route.path} must stay public"
                assert require_admin not in deps, f"{method} {route.path} must stay public"


def test_public_routes_exist(client):
    """Guard against silently renaming/removing a deliberately public route."""
    registered = {(method, route.path) for route in _api_routes(client.app) for method in route.methods}
    missing = PUBLIC_ROUTES - registered
    assert not missing, f"expected public routes not registered: {sorted(missing)}"
