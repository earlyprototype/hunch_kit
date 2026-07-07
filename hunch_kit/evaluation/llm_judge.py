"""LLM-as-judge interface — hook point for automated scoring.

This module defines the abstract interface for LLM-based evaluation.
No concrete implementation is provided; custom judges are built by
subclassing ``LLMJudge`` and invoking them directly from your own code.

The interface is intentionally minimal: given an output and a rubric
dimension, produce a score with reasoning.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from ..rubric import Dimension


@dataclass
class JudgeResult:
    """Outcome of a single LLM judge evaluation."""

    dimension: str
    score: float
    reasoning: str = ""
    model: str = ""
    tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMJudge(abc.ABC):
    """Abstract base for LLM-based evaluation judges.

    To implement a judge:
    1. Subclass ``LLMJudge``
    2. Implement ``score()`` to call your preferred LLM API
    3. Instantiate it and call ``score()`` for each rubric dimension,
       writing results into the manifest's ``automated_scores``

    Example::

        class GeminiJudge(LLMJudge):
            name = "gemini"

            def score(self, output, dimension, config=None):
                # Call Gemini API with the rubric dimension as prompt
                ...
                return JudgeResult(
                    dimension=dimension.name,
                    score=7.5,
                    reasoning="The output demonstrates...",
                    model="gemini-2.5-flash",
                )

        judge = GeminiJudge()
        result = judge.score(output_text, dimension)
        manifest.automated_scores[result.dimension] = result.score
        manifest.save(experiment_dir)
    """

    name: str = "base"

    @abc.abstractmethod
    def score(
        self,
        output: str,
        dimension: Dimension,
        config: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Score an experiment output against a single rubric dimension.

        Args:
            output: The experiment output text to evaluate.
            dimension: The rubric dimension to score against, including
                its description, scale, and any anchors.
            config: Optional judge-specific configuration (API keys,
                model selection, temperature, etc.).

        Returns:
            A ``JudgeResult`` with the score and reasoning.
        """

    async def async_score(
        self,
        output: str,
        dimension: Dimension,
        config: dict[str, Any] | None = None,
    ) -> JudgeResult:
        """Async variant — defaults to delegating to the sync method."""
        return self.score(output, dimension, config)
