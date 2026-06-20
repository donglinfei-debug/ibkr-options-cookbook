"""
Notification Sender — DingTalk / Webhook with HMAC-SHA256

A lightweight notifier that sends messages via DingTalk robot webhooks
with HMAC-SHA256 signature.  The same pattern applies to any webhook
endpoint that supports signature verification.

Usage:
    notifier = WebhookNotifier(webhook_url="https://...", secret="your-secret")
    notifier.send("Trade executed: Iron Condor filled at -2.50")
"""

import datetime
import hmac
import hashlib
import urllib.parse
import base64
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class WebhookNotifier:
    """
    Send signed text messages via DingTalk robot webhook.

    The webhook URL and secret should be supplied via environment
    variables or a config file — never hard-coded.

    Parameters
    ----------
    webhook_url:
        Full DingTalk robot webhook URL (including access_token).
    secret:
        HMAC-SHA256 signing secret.
    """

    def __init__(self, webhook_url: str, secret: str):
        self._webhook_url = webhook_url
        self._secret = secret

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def _sign(self) -> tuple:
        """
        Generate a timestamp-based HMAC-SHA256 signature.

        Returns
        -------
        (timestamp_str, signed_url)
        """
        timestamp = str(round(datetime.datetime.now().timestamp() * 1000))
        string_to_sign = f"{timestamp}\n{self._secret}"
        hmac_code = hmac.new(
            self._secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        signed_url = f"{self._webhook_url}&timestamp={timestamp}&sign={sign}"
        return timestamp, signed_url

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def send(self, message: str) -> bool:
        """
        Send a text message to the webhook.

        Parameters
        ----------
        message:
            Plain-text message body.

        Returns
        -------
        True if the server responded with HTTP 2xx.
        """
        _, signed_url = self._sign()
        payload = {"msgtype": "text", "text": {"content": message}}

        try:
            resp = requests.post(signed_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Notification sent (HTTP %s): %s", resp.status_code, message[:60])
            return True
        except requests.RequestException as exc:
            logger.error("Notification failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Message templates (extend these for different trade types)
    # ------------------------------------------------------------------

    def send_trade_update(
        self,
        title: str,
        execution_time: str,
        details: str,
    ) -> bool:
        """
        Convenience method for trade execution notifications.

        Parameters
        ----------
        title:
            e.g. "Iron Condor Filled"
        execution_time:
            e.g. "2025-09-03 15:45:00 ET"
        details:
            Multi-line string with leg / price / market data details.

        Returns
        -------
        bool
        """
        message = f"{title}\n\nTime: {execution_time}\n\n{details}"
        return self.send(message)
