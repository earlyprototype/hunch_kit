"""Echo provider — reference implementation for testing.

Returns the input as output, optionally with a configurable delay.
Useful for verifying the experiment pipeline without external dependencies.
"""

from __future__ import annotations

import time
from typing import Any

from .base import BaseProvider, ProviderResult


class EchoProvider(BaseProvider):
    """Provider that echoes its input back as output."""

    name = "echo"

    def run(self, input_text: str, config: dict[str, Any] | None = None) -> ProviderResult:
        config = config or {}
        delay = float(config.get("delay", 0))

        start = time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elapsed = time.monotonic() - start

        prefix = config.get("prefix", "")
        output = f"{prefix}{input_text}" if prefix else input_text

        return ProviderResult(
            output=output,
            status="success",
            duration_seconds=round(elapsed, 3),
            metadata={
                "provider": self.name,
                "input_length": len(input_text),
                "delay_applied": delay,
            },
        )
