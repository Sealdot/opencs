"""Explicit tool permission checks; no business thresholds hidden in prompts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolPolicy:
    allowed_tools: frozenset[str]
    forbidden_tools: frozenset[str]
    allowed_tenants: frozenset[str]

    def permits(self, tenant_id: str, tool: str) -> bool:
        return (
            tenant_id in self.allowed_tenants
            and tool in self.allowed_tools
            and tool not in self.forbidden_tools
        )
