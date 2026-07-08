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
Pluggable email transport (spec #51).

``EmailSender`` is the narrow seam between the delivery pass and the
outside world: one ``send`` call, one fully-composed MIME message. Two v1
implementations — ``SmtpEmailSender`` (stdlib ``smtplib``, the self-host
default: no new dependency) and ``SesEmailSender`` (boto3, lazy-imported
like the Postgres repos so self-host installs never import it). Selected
via ``EMAIL_PROVIDER``; ``none`` returns no sender at all, which switches
delivery off globally.

Transport owns *how* bytes leave the box, nothing else: rendering lives in
``BriefingEmailRenderer``, retry/backoff in ``BriefingDeliveryService``.
Any provider error surfaces as ``EmailSendError`` so the delivery service
has a single failure type to settle against.
"""

import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage
from email.utils import parseaddr
from typing import TYPE_CHECKING, Dict, Optional

from structlog import get_logger

if TYPE_CHECKING:
    from ..utils.config import Config

logger = get_logger(__name__)


class EmailSendError(Exception):
    """A send attempt failed at the transport layer (retryable by the
    delivery service's backoff, never by the transport itself)."""


class EmailSender(ABC):
    """Abstract email transport."""

    @abstractmethod
    def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Send one email with an HTML part and a plain-text alternative.

        Raises:
            EmailSendError: on any transport/provider failure.
        """


def _build_message(
    *,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    headers: Optional[Dict[str, str]] = None,
) -> EmailMessage:
    """Compose the multipart/alternative message both providers send.

    Text part first, HTML last — clients render the last alternative they
    support, so HTML-capable clients show HTML and the text part carries
    deliverability + accessibility.
    """
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


class SmtpEmailSender(EmailSender):
    """Env-configured SMTP relay transport (self-host default)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        starttls: bool = True,
        from_addr: str,
        timeout_seconds: int = 30,
    ) -> None:
        if not host:
            raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp")
        if not from_addr:
            raise ValueError("EMAIL_FROM is required when EMAIL_PROVIDER=smtp")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._starttls = starttls
        self._from = from_addr
        self._timeout = timeout_seconds
        logger.info("SmtpEmailSender initialized", host=host, port=port, starttls=starttls)

    def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        message = _build_message(from_addr=self._from, to=to, subject=subject, html=html, text=text, headers=headers)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
                if self._starttls:
                    client.starttls()
                if self._username:
                    client.login(self._username, self._password)
                client.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailSendError(f"SMTP send failed: {exc}") from exc


class SesEmailSender(EmailSender):
    """AWS SES transport (aligns with the #43 hosted story).

    boto3 is lazy-imported at construction — the same pattern as the
    Postgres repositories — so self-host installs never import it. Uses
    ``send_raw_email`` because the composed message carries custom headers
    (List-Unsubscribe et al.) that the structured SES API drops.
    Credentials come from the ambient AWS chain (task role / instance
    profile), never explicit keys.
    """

    def __init__(self, *, region: str, from_addr: str) -> None:
        if not region:
            raise ValueError("SES_REGION is required when EMAIL_PROVIDER=ses")
        if not from_addr:
            raise ValueError("EMAIL_FROM is required when EMAIL_PROVIDER=ses")
        import boto3  # lazy: only an SES deployment pays the import

        self._client = boto3.client("ses", region_name=region)
        self._from = from_addr
        logger.info("SesEmailSender initialized", region=region)

    def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        message = _build_message(from_addr=self._from, to=to, subject=subject, html=html, text=text, headers=headers)
        # Source must be a bare address; EMAIL_FROM may be display form.
        source = parseaddr(self._from)[1] or self._from
        try:
            self._client.send_raw_email(
                Source=source,
                Destinations=[to],
                RawMessage={"Data": message.as_bytes()},
            )
        except Exception as exc:  # boto3 raises botocore.exceptions.ClientError et al.
            raise EmailSendError(f"SES send failed: {exc}") from exc


def make_email_sender(config: "Config") -> Optional[EmailSender]:
    """Provider factory: ``EMAIL_PROVIDER`` → transport, or ``None``.

    ``None`` (provider ``none``, the default) means email delivery is off
    globally — callers skip building the whole delivery stack. Misconfigured
    providers raise at startup (FM-4: fail loud at boot, not silently at
    8am).
    """
    provider = (config.email_provider or "none").lower()
    if provider == "none":
        return None
    if provider == "smtp":
        return SmtpEmailSender(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username,
            password=config.smtp_password,
            starttls=config.smtp_starttls,
            from_addr=config.email_from,
        )
    if provider == "ses":
        return SesEmailSender(region=config.ses_region, from_addr=config.email_from)
    raise ValueError(f"Unknown EMAIL_PROVIDER: {provider!r} (expected smtp, ses, or none)")
