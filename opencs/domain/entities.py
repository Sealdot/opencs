"""Provider-neutral domain records. References are checked by repositories."""

from dataclasses import dataclass, field
from typing import Any

from .validation import json_value, nonempty


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str = ""

    def __post_init__(self) -> None:
        nonempty(self.id, "id")
        if not isinstance(self.name, str):
            raise ValueError("name must be a string")


@dataclass(frozen=True)
class Organization:
    tenant_id: str
    id: str
    name: str = ""

    def __post_init__(self) -> None:
        nonempty(self.tenant_id, "tenant_id")
        nonempty(self.id, "id")
        if not isinstance(self.name, str):
            raise ValueError("name must be a string")


@dataclass(frozen=True)
class Customer:
    tenant_id: str
    id: str
    organization_id: str | None = None
    name: str = ""

    def __post_init__(self) -> None:
        nonempty(self.tenant_id, "tenant_id")
        nonempty(self.id, "id")
        if self.organization_id is not None:
            nonempty(self.organization_id, "organization_id")
        if not isinstance(self.name, str):
            raise ValueError("name must be a string")


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    id: str
    customer_id: str
    type: str
    value: str
    provider: str = ""

    def __post_init__(self) -> None:
        for key in ("tenant_id", "id", "customer_id", "type", "value"):
            nonempty(getattr(self, key), key)
        if not isinstance(self.provider, str):
            raise ValueError("provider must be a string")
        if self.type == "external_id":
            nonempty(self.provider, "provider")


@dataclass(frozen=True)
class Conversation:
    tenant_id: str
    id: str
    customer_id: str
    ai_paused: bool = False
    resolution: str | None = None

    def __post_init__(self) -> None:
        for key in ("tenant_id", "id", "customer_id"):
            nonempty(getattr(self, key), key)
        if type(self.ai_paused) is not bool:
            raise ValueError("ai_paused must be boolean")
        if self.resolution is not None:
            nonempty(self.resolution, "resolution")


@dataclass(frozen=True)
class Interaction:
    tenant_id: str
    id: str
    conversation_id: str
    customer_id: str
    kind: str
    content: str | dict[str, Any] | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("tenant_id", "id", "conversation_id", "customer_id", "kind"):
            nonempty(getattr(self, key), key)
        if self.content is not None and not isinstance(self.content, (str, dict)):
            raise ValueError("content must be a string, object or null")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be an object")
        json_value(self.content)
        json_value(self.metadata)
