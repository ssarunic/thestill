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

"""Regression tests for spec #25 items 2.5 (CORS) and 2.8 (docs + errors)."""

from unittest.mock import patch

import pytest

from thestill.utils.config import Config


def _config_for(**overrides) -> Config:
    """Build a Config without running the full env-driven loader."""
    base = {
        "openai_api_key": "dummy",
        "environment": overrides.get("environment", "production"),
        "allowed_origins": overrides.get("allowed_origins", []),
        "enable_docs": overrides.get("enable_docs", False),
        "cookie_secure": overrides.get("cookie_secure", True),
        "trusted_proxies": overrides.get("trusted_proxies", []),
        "public_base_url": overrides.get("public_base_url", ""),
        "storage_path": overrides.get("storage_path", "/tmp/thestill-test-storage"),
    }
    return Config(**base)


def _build_app(config: Config):
    """Construct the FastAPI app with most heavy deps stubbed out."""
    # Patch repositories / services so we don't need a real DB or network.
    with (
        patch("thestill.web.app.SqlitePodcastRepository"),
        patch("thestill.web.app.SqliteDigestRepository"),
        patch("thestill.web.app.SqliteUserRepository"),
        patch("thestill.web.app.SqlitePodcastFollowerRepository"),
        patch("thestill.web.app.PodcastFeedManager"),
        patch("thestill.web.app.PodcastService"),
        patch("thestill.web.app.RefreshService"),
        patch("thestill.web.app.StatsService"),
        patch("thestill.web.app.AuthService"),
        patch("thestill.web.app.FollowerService"),
        patch("thestill.web.app.QueueManager"),
        patch("thestill.web.app.ProgressStore"),
        patch("thestill.web.app.get_task_manager"),
        patch("thestill.web.app.create_task_handlers"),
        patch("thestill.web.app.TaskWorker"),
        patch("thestill.web.app.PathManager"),
    ):
        from thestill.web.app import create_app

        return create_app(config=config)


class TestDocsGating:
    def test_docs_disabled_in_production(self):
        app = _build_app(_config_for(environment="production", enable_docs=False))
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_docs_enabled_in_development(self):
        app = _build_app(_config_for(environment="development"))
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"

    def test_docs_enabled_on_explicit_flag(self):
        """ENABLE_DOCS=true re-opens the endpoints even in production."""
        app = _build_app(_config_for(environment="production", enable_docs=True))
        assert app.docs_url == "/docs"


class TestCors:
    def _cors_middleware(self, app):
        from fastapi.middleware.cors import CORSMiddleware

        for mw in app.user_middleware:
            if mw.cls is CORSMiddleware:
                return mw
        return None

    def test_no_cors_middleware_when_origins_empty_in_prod(self):
        app = _build_app(_config_for(environment="production", allowed_origins=[]))
        assert self._cors_middleware(app) is None

    def test_cors_origins_from_env_explicit(self):
        origins = ["https://thestill.example.com"]
        app = _build_app(_config_for(environment="production", allowed_origins=origins))
        mw = self._cors_middleware(app)
        assert mw is not None
        assert mw.kwargs["allow_origins"] == origins
        # Explicit methods/headers - no wildcards.
        assert "*" not in mw.kwargs["allow_methods"]
        assert "*" not in mw.kwargs["allow_headers"]

    def test_dev_has_localhost_fallback(self):
        app = _build_app(_config_for(environment="development"))
        mw = self._cors_middleware(app)
        assert mw is not None
        origins = mw.kwargs["allow_origins"]
        assert any("localhost" in o or "127.0.0.1" in o for o in origins)

    def test_wildcard_origin_rejected_at_startup(self):
        """Post-review fix (spec #25 item 2.5): ALLOWED_ORIGINS='*' with
        credentials violates CORS and would let any site call us with the
        user's auth cookie. Startup must refuse it."""
        with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
            _build_app(_config_for(environment="production", allowed_origins=["*"]))


class TestCookieSecureEnforcement:
    """Post-review fix for spec #25 item 2.1."""

    def test_production_requires_cookie_secure_true(self):
        """load_config must refuse COOKIE_SECURE=false when ENVIRONMENT=production."""
        import os

        from thestill.utils.config import load_config

        saved = {k: os.environ.get(k) for k in ("ENVIRONMENT", "COOKIE_SECURE", "OPENAI_API_KEY")}
        try:
            os.environ["ENVIRONMENT"] = "production"
            os.environ["COOKIE_SECURE"] = "false"
            os.environ["OPENAI_API_KEY"] = "dummy"
            with pytest.raises(ValueError, match="COOKIE_SECURE"):
                load_config()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_development_can_opt_out_of_secure_cookies(self):
        import os

        from thestill.utils.config import load_config

        saved = {k: os.environ.get(k) for k in ("ENVIRONMENT", "COOKIE_SECURE", "OPENAI_API_KEY")}
        try:
            os.environ["ENVIRONMENT"] = "development"
            os.environ["COOKIE_SECURE"] = "false"
            os.environ["OPENAI_API_KEY"] = "dummy"
            cfg = load_config()
            assert cfg.cookie_secure is False
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TestMultiUserOauthRequiresPublicBaseUrl:
    """Post-review fix for spec #25 item 2.4."""

    def test_multi_user_without_public_base_url_fails_load(self):
        import os

        from thestill.utils.config import load_config

        saved = {
            k: os.environ.get(k)
            for k in ("MULTI_USER", "PUBLIC_BASE_URL", "TRUSTED_PROXIES", "OPENAI_API_KEY")
        }
        try:
            os.environ["MULTI_USER"] = "true"
            os.environ.pop("PUBLIC_BASE_URL", None)
            os.environ.pop("TRUSTED_PROXIES", None)
            os.environ["OPENAI_API_KEY"] = "dummy"
            with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
                load_config()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
