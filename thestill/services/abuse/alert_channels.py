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

"""Spec #104 §3.1 — where operator alerts go.

- Every alert is logged as ``admin_alert`` with its ``details`` (ids and
  numbers only), so a JSON log pipeline can alert from logs instead.
- Email goes to ``ADMIN_ALERT_EMAIL`` through the configured
  ``EMAIL_PROVIDER``.
- The optional webhook POSTs ``{"text", "content", "title", "alert",
  "details"}``: ``text`` is what Slack incoming webhooks show, ``content``
  what Discord shows. For ntfy, use its JSON publishing URL. The text names
  the user an alert is about (their email), as the email does.

A channel failure is logged, never raised or retried.
"""

import html
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, Sequence

import httpx
from structlog import get_logger

if TYPE_CHECKING:
    from ...utils.config import Config
    from ..email_sender import EmailSender

logger = get_logger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 5.0


class AlertDeliveryError(Exception):
    """A channel failed. Its message never carries the webhook URL (a
    bearer secret) or an address list."""


@dataclass(frozen=True)
class AlertMessage:
    kind: str
    title: str
    #: For people: may name the user's email and suggest an action.
    text: str
    #: For machines and the log: ids and numbers only, never PII.
    details: Dict[str, Any] = field(default_factory=dict)


class AlertChannel(Protocol):
    name: str

    def send(self, message: AlertMessage) -> None: ...


class LogChannel:
    name = "log"

    def send(self, message: AlertMessage) -> None:
        logger.warning("admin_alert", alert=message.kind, **message.details)


class EmailChannel:
    name = "email"

    def __init__(self, sender: "EmailSender", recipients: Sequence[str]) -> None:
        self._sender = sender
        self._recipients = list(recipients)

    def send(self, message: AlertMessage) -> None:
        """Try every recipient; one bad address doesn't stop the rest."""
        body = f"<pre style='font: 14px/1.5 ui-monospace, monospace'>{html.escape(message.text)}</pre>"
        failed = 0
        for recipient in self._recipients:
            try:
                self._sender.send(to=recipient, subject=f"[thestill] {message.title}", html=body, text=message.text)
            except Exception:
                failed += 1
                logger.error("admin_alert_email_failed", alert=message.kind, exc_info=True)
        if failed:
            raise AlertDeliveryError(f"{failed} of {len(self._recipients)} recipients failed")


class WebhookChannel:
    name = "webhook"

    def __init__(self, url: str, *, post: Callable[..., Any] = httpx.post) -> None:
        self._url = url
        self._post = post

    def send(self, message: AlertMessage) -> None:
        try:
            response = self._post(
                self._url,
                json={
                    "text": message.text,
                    "content": message.text,
                    "title": message.title,
                    "alert": message.kind,
                    "details": message.details,
                },
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            # httpx error text quotes the URL, which is the secret.
            raise AlertDeliveryError(f"webhook request failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise AlertDeliveryError(f"webhook returned HTTP {response.status_code}")


def build_alert_channels(config: "Config", email_sender: Optional["EmailSender"]) -> List[AlertChannel]:
    channels: List[AlertChannel] = [LogChannel()]
    if email_sender is not None and config.admin_alert_email:
        channels.append(EmailChannel(email_sender, config.admin_alert_email))
    if config.alert_webhook_url:
        channels.append(WebhookChannel(config.alert_webhook_url))
    return channels


def deliver(channels: Sequence[AlertChannel], message: AlertMessage) -> Dict[str, bool]:
    """Send to every channel; one failing never stops the others. Returns
    per-channel success."""
    results = {}
    for channel in channels:
        try:
            channel.send(message)
            results[channel.name] = True
        except AlertDeliveryError as exc:
            logger.error("admin_alert_delivery_failed", channel=channel.name, alert=message.kind, error=str(exc))
            results[channel.name] = False
        except Exception:  # noqa: BLE001 — alerts must never raise
            logger.error("admin_alert_delivery_failed", channel=channel.name, alert=message.kind, exc_info=True)
            results[channel.name] = False
    return results
