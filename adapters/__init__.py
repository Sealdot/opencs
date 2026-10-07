"""Dependency-free provider mapping contracts; transport must be explicitly injected."""

from .chatwoot import ChatwootAdapter
from .chatwoot_signing import ChatwootWebhookVerifier, WebhookAuthenticationError
from .http import AdapterError, FakeTransport, HttpRequest, HttpResponse, HttpTransport
from .http_tools import GenericHttpToolAdapter, ToolDefinition
from .openai import OpenAIResponsesAdapter

__all__ = [
    "AdapterError",
    "ChatwootAdapter",
    "ChatwootWebhookVerifier",
    "FakeTransport",
    "GenericHttpToolAdapter",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "OpenAIResponsesAdapter",
    "ToolDefinition",
    "WebhookAuthenticationError",
]
