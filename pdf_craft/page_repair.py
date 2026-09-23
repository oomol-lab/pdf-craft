"""Public configuration for optional JEV-routed page repair."""

from dataclasses import dataclass

from .jev import JEV
from .llm import LLM


@dataclass(frozen=True)
class PageRepairOptions:
    """Review reversible pages with JEV and repair selected pages with an LLM."""

    jev: JEV
    llm: LLM
    risk_threshold: float = 0.70
    max_retries: int = 4
    max_output_tokens: int = 16000

    def __post_init__(self) -> None:
        if not 0 <= self.risk_threshold <= 1:
            raise ValueError("page repair risk_threshold must be between 0 and 1")
        if self.max_retries < 0:
            raise ValueError("page repair max_retries cannot be negative")
        if self.max_output_tokens < 1:
            raise ValueError("page repair max_output_tokens must be at least 1")
