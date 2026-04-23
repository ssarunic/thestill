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

"""Regression tests for spec #25, item 1.2 — SSRF guard on user-supplied URLs."""

from unittest import mock

import pytest

from thestill.utils import url_guard
from thestill.utils.url_guard import UnsafeURLError, validate_public_url


def _fake_resolve(address: str):
    """Patch helper: force getaddrinfo to return a specific IP literal."""

    def _fn(hostname, *_args, **_kwargs):
        return [(None, None, None, None, (address, 0))]

    return _fn


class TestValidatePublicUrl:
    """validate_public_url must refuse private / loopback / metadata targets."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/foo",
            "gopher://example.com/",
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
        ],
    )
    def test_disallowed_schemes(self, url):
        with pytest.raises(UnsafeURLError):
            validate_public_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://127.0.0.1:8000/admin",
            "http://localhost/",  # resolves to 127.0.0.1 / ::1
            "http://[::1]/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "http://[fe80::1]/",  # link-local IPv6
            "http://0.0.0.0/",
        ],
    )
    def test_internal_literals_rejected(self, url):
        with pytest.raises(UnsafeURLError):
            validate_public_url(url)

    def test_missing_hostname_rejected(self):
        with pytest.raises(UnsafeURLError):
            validate_public_url("http:///no-host")

    def test_dns_failure_rejected(self):
        with mock.patch(
            "thestill.utils.url_guard.socket.getaddrinfo",
            side_effect=url_guard.socket.gaierror("boom"),
        ):
            with pytest.raises(UnsafeURLError):
                validate_public_url("http://does-not-resolve.invalid/")

    def test_public_host_accepted(self):
        with mock.patch(
            "thestill.utils.url_guard.socket.getaddrinfo",
            side_effect=_fake_resolve("93.184.216.34"),  # example.com
        ):
            resolved = validate_public_url("https://example.com/rss.xml")
            assert "93.184.216.34" in resolved.addresses

    def test_host_with_mixed_resolution_rejected(self):
        """If any resolved address is private, refuse — defeats DNS-based SSRF."""

        def _mixed(hostname, *_args, **_kwargs):
            return [
                (None, None, None, None, ("93.184.216.34", 0)),
                (None, None, None, None, ("127.0.0.1", 0)),
            ]

        with mock.patch("thestill.utils.url_guard.socket.getaddrinfo", side_effect=_mixed):
            with pytest.raises(UnsafeURLError):
                validate_public_url("https://dns-rebinding.example.com/")

    def test_allowlist_overrides_private_check(self, monkeypatch):
        """URL_GUARD_ALLOWLIST lets a self-hosted service on localhost through."""
        monkeypatch.setenv("URL_GUARD_ALLOWLIST", "dalston.local")
        with mock.patch(
            "thestill.utils.url_guard.socket.getaddrinfo",
            side_effect=_fake_resolve("127.0.0.1"),
        ):
            # Should NOT raise — allowlisted.
            resolved = validate_public_url("http://dalston.local:8080/api")
            assert resolved.hostname == "dalston.local"
