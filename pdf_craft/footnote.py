"""Public configuration for footnote extraction and optional refinement."""

from dataclasses import dataclass

from .jev import JEV
from .llm import LLM
from .concurrency import AsyncExecutor, ConcurrentExecutor, FixedCapacity


@dataclass(frozen=True)
class FootnoteRefinement:
    """Use JEV to route uncertain footnote pages to an LLM."""

    jev: JEV
    llm: LLM
    risk_threshold: float = 0.70
    max_retries: int = 4
    max_output_tokens: int = 16000
    executor: AsyncExecutor | None = None
    jev_executor: AsyncExecutor | None = None
    llm_executor: AsyncExecutor | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.risk_threshold <= 1:
            raise ValueError("footnote refinement risk_threshold must be between 0 and 1")
        if self.max_retries < 0:
            raise ValueError("footnote refinement max_retries cannot be negative")
        if self.max_output_tokens < 1:
            raise ValueError("footnote refinement max_output_tokens must be at least 1")

    def resolved_jev_executor(self) -> AsyncExecutor:
        executor = self.jev_executor or self.executor
        return executor or ConcurrentExecutor(FixedCapacity(self.jev.concurrency))

    def resolved_llm_executor(self) -> AsyncExecutor:
        executor = self.llm_executor or self.executor
        return executor or ConcurrentExecutor(FixedCapacity(1))


@dataclass(frozen=True)
class FootnoteOptions:
    """Enable algorithmic footnote extraction with optional AI refinement."""

    refinement: FootnoteRefinement | None = None
