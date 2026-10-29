"""Shared observed-resource value; aggregation and request delivery are separate."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RequestUsage:
    """Observed resources; cached input is a subset of input_tokens."""
    requests: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def _add(self, other: RequestUsage) -> None:
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens

