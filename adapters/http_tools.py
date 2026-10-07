"""Static generic HTTP tool bindings; business policy remains in the Core."""

from dataclasses import dataclass
from typing import Any

from .chatwoot import checked_url
from .http import AdapterError, HttpTransport, TransportError


@dataclass(frozen=True)
class ToolDefinition:
    url: str
    method: str
    allowed_tenants: frozenset[str]

    def __post_init__(self) -> None:
        checked_url(self.url)
        if self.method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise AdapterError("Unsupported configured HTTP method")
        if not isinstance(self.allowed_tenants, frozenset) or not self.allowed_tenants:
            raise AdapterError("Explicit immutable tenant allowlist is required")


class GenericHttpToolAdapter:
    def __init__(self, *, registry: dict[str, ToolDefinition], transport: HttpTransport) -> None:
        self._registry = dict(registry)
        self.transport = transport

    def execute(
        self, name: str, arguments: dict[str, Any], *, tenant_id: str, action_id: str
    ) -> dict[str, Any]:
        definition = self._registry.get(name)
        if definition is None or tenant_id not in definition.allowed_tenants:
            raise AdapterError("Unknown tool or unauthorized tenant")
        if not action_id or not isinstance(arguments, dict):
            raise AdapterError("Action id and object arguments are required")
        # Tenant and destination come from authenticated config, never arguments.
        try:
            response = self.transport.request(
                definition.method,
                definition.url,
                {
                    "Content-Type": "application/json",
                    "X-OpenCS-Tenant": tenant_id,
                    "X-OpenCS-Action": action_id,
                },
                {"arguments": arguments},
            )
        except TransportError:
            # Runtime treats TimeoutError as an uncertain tool outcome and stops.
            # A malformed response/connection loss can follow a successful action.
            raise TimeoutError("Tool delivery is uncertain") from None
        if not 200 <= response.status < 300:
            raise AdapterError(f"Tool HTTP status {response.status}")
        if not isinstance(response.body, dict):
            raise AdapterError("Tool response must be a JSON object")
        return response.body
