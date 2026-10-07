"""Chatwoot v4.17.1 HMAC verification over timestamp + '.' + raw body."""

import hashlib
import hmac
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class WebhookAuthenticationError(ValueError):
    """Missing, malformed, stale or invalid signature (never includes secrets)."""


@dataclass(frozen=True)
class ChatwootWebhookVerifier:
    secret: str = field(repr=False)
    max_age_seconds: int = 300
    future_skew_seconds: int = 30
    clock: Callable[[], float] = field(default=time.time, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.secret, str) or not self.secret.strip():
            raise ValueError("A native Chatwoot signing secret is required")
        for value in (self.max_age_seconds, self.future_skew_seconds):
            if type(value) is not int or value < 0:
                raise ValueError("Timestamp tolerances must be nonnegative integer seconds")
        if not callable(self.clock):
            raise ValueError("A clock is required")

    def verify(self, body: bytes, *, timestamp: str, signature: str) -> None:
        if (
            not isinstance(body, bytes)
            or not isinstance(timestamp, str)
            or re.fullmatch(r"[0-9]{1,20}", timestamp) is None
            or not isinstance(signature, str)
            or re.fullmatch(r"sha256=[0-9a-f]{64}", signature) is None
        ):
            raise WebhookAuthenticationError("Invalid webhook authentication")
        now = self.clock()
        age = now - int(timestamp)
        if not math.isfinite(now) or not -self.future_skew_seconds <= age <= self.max_age_seconds:
            raise WebhookAuthenticationError("Invalid webhook authentication")
        digest = hmac.new(
            self.secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, "sha256=" + digest):
            raise WebhookAuthenticationError("Invalid webhook authentication")
