"""Public configuration for optional page-semantic refinement."""

from dataclasses import KW_ONLY, dataclass

from .jev import JEV
from .llm import LLM
from .concurrency import AsyncExecutor


@dataclass(frozen=True)
class Refinement:
    """Use JEV to select evidenced structure errors for LLM page repair."""

    jev: JEV
    llm: LLM
    _: KW_ONLY
    max_output_tokens: int = 16000
    executor: AsyncExecutor | None = None
    jev_executor: AsyncExecutor | None = None
    llm_executor: AsyncExecutor | None = None

    def __post_init__(self) -> None:
        if self.jev is None or self.llm is None:
            raise ValueError("Refinement requires both JEV and LLM")
        if self.max_output_tokens < 1:
            raise ValueError("page refinement max_output_tokens must be at least 1")
        if self.jev_executor is None and self.executor is None:
            raise ValueError("Page refinement requires a JEV executor")
        if self.llm_executor is None and self.executor is None:
            raise ValueError("Page refinement requires an LLM executor")

    def resolved_jev_executor(self) -> AsyncExecutor:
        executor = self.jev_executor or self.executor
        assert executor is not None
        return executor

    def resolved_llm_executor(self) -> AsyncExecutor:
        executor = self.llm_executor or self.executor
        assert executor is not None
        return executor
