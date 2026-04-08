"""Minimal local provider — demonstrates the campaign `providers/` pattern."""

from __future__ import annotations

from typing import Any

from hunch_kit.providers.base import BaseProvider, ProviderResult


class StubLocalProvider(BaseProvider):
    """Echo with a fixed prefix; use provider name ``stub_local`` in the manifest."""

    name = "stub_local"

    def run(self, input_text: str, config: dict[str, Any] | None = None) -> ProviderResult:
        config = config or {}
        prefix = config.get("prefix", "[stub_local] ")
        return ProviderResult(
            output=f"{prefix}{input_text}",
            status="success",
            metadata={"provider": self.name},
        )
