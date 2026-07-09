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

"""Unit tests for the email transport layer (spec #51)."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from thestill.services.email_sender import (
    EmailSendError,
    SmtpEmailSender,
    _build_message,
    make_email_sender,
)
from thestill.utils.config import Config


def _config(**overrides) -> Config:
    data = {"email_provider": "none"}
    data.update(overrides)
    return Config(**data)


class TestBuildMessage:
    def test_multipart_alternative_with_headers(self):
        message = _build_message(
            from_addr="Thestill <briefings@example.com>",
            to="alice@example.com",
            subject="Your briefing",
            html="<p>Hi</p>",
            text="Hi",
            headers={"List-Unsubscribe": "<https://x/unsub>"},
        )
        assert message["From"] == "Thestill <briefings@example.com>"
        assert message["To"] == "alice@example.com"
        assert message["List-Unsubscribe"] == "<https://x/unsub>"
        parts = [part.get_content_type() for part in message.iter_parts()]
        assert parts == ["text/plain", "text/html"]


class TestSmtpEmailSender:
    def _sender(self, **overrides) -> SmtpEmailSender:
        kwargs = {
            "host": "smtp.example.com",
            "port": 587,
            "username": "user",
            "password": "pass",
            "starttls": True,
            "from_addr": "briefings@example.com",
        }
        kwargs.update(overrides)
        return SmtpEmailSender(**kwargs)

    def test_sends_via_starttls_and_login(self):
        with patch("thestill.services.email_sender.smtplib.SMTP") as smtp_cls:
            client = smtp_cls.return_value.__enter__.return_value
            self._sender().send(to="alice@example.com", subject="s", html="<p>h</p>", text="t")

        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=30)
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("user", "pass")
        client.send_message.assert_called_once()

    def test_plain_relay_skips_tls_and_login(self):
        with patch("thestill.services.email_sender.smtplib.SMTP") as smtp_cls:
            client = smtp_cls.return_value.__enter__.return_value
            self._sender(starttls=False, username="", password="").send(
                to="alice@example.com", subject="s", html="<p>h</p>", text="t"
            )
        client.starttls.assert_not_called()
        client.login.assert_not_called()

    def test_smtp_errors_surface_as_email_send_error(self):
        with patch("thestill.services.email_sender.smtplib.SMTP") as smtp_cls:
            client = smtp_cls.return_value.__enter__.return_value
            client.send_message.side_effect = smtplib.SMTPServerDisconnected("gone")
            with pytest.raises(EmailSendError, match="SMTP send failed"):
                self._sender().send(to="a@b.c", subject="s", html="h", text="t")

    def test_requires_host_and_from(self):
        with pytest.raises(ValueError, match="SMTP_HOST"):
            self._sender(host="")
        with pytest.raises(ValueError, match="EMAIL_FROM"):
            self._sender(from_addr="")


class TestMakeEmailSender:
    def test_none_provider_returns_none(self):
        assert make_email_sender(_config()) is None
        assert make_email_sender(_config(email_provider="NONE")) is None

    def test_smtp_provider(self):
        config = _config(
            email_provider="smtp",
            smtp_host="smtp.example.com",
            email_from="briefings@example.com",
        )
        assert isinstance(make_email_sender(config), SmtpEmailSender)

    def test_misconfigured_smtp_fails_loud(self):
        with pytest.raises(ValueError, match="SMTP_HOST"):
            make_email_sender(_config(email_provider="smtp", email_from="x@y.z"))

    def test_unknown_provider_fails_loud(self):
        with pytest.raises(ValueError, match="Unknown EMAIL_PROVIDER"):
            make_email_sender(_config(email_provider="carrier-pigeon"))

    def test_ses_provider_lazy_imports_boto3(self):
        config = _config(email_provider="ses", ses_region="eu-central-1", email_from="x@y.z")
        with patch.dict("sys.modules", {"boto3": MagicMock()}) as modules:
            sender = make_email_sender(config)
            modules["boto3"].client.assert_called_once_with("ses", region_name="eu-central-1")
        assert sender is not None
