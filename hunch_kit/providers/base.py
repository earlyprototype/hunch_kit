"""Abstract provider interface.

A provider is anything that takes an input and produces an output:
a text generator, an image pipeline, a slide deck builder, a config
evaluator — any input-to-output workflow.

Providers implement a synchronous ``run()`` method. For long-running or
network-bound backends, override ``async_run()`` instead and the runner
will await it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderResult:
    """Outcome of a single provider execution."""

    output: str
    status: str = "success"  # "success" | "failed" | "timeout"
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0
    error: str = ""


class BaseProvider(abc.ABC):
    """Abstract base for all hunch_kit providers."""

    name: str = "base"

    @abc.abstractmethod
    def run(self, input_text: str, config: dict[str, Any] | None = None) -> ProviderResult:
        """Execute the provider synchronously.

        Args:
            input_text: The experiment input (prompt, config, style spec, etc.).
            config: Optional provider-specific configuration.

        Returns:
            A ``ProviderResult`` with the output and execution metadata.
        """

    async def async_run(
        self, input_text: str, config: dict[str, Any] | None = None
    ) -> ProviderResult:
        """Execute the provider asynchronously.

        The default implementation delegates to the synchronous ``run()``.
        Override this for providers that benefit from async I/O.
        """
        return self.run(input_text, config)
