"""Official asynchronous JEV service configuration and transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy


@dataclass(frozen=True)
class JEV:
    """Declarative configuration for TypeSafe AI's JEV service."""

    key: str = field(repr=False)
    model: str = "jev-latest"
    url: str = "https://api.typesafe.ai"
    timeout: float | None = 60.0
    retry_times: int = 2
    concurrency: int = 4

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("JEV API key cannot be empty")
        if not self.model.strip():
            raise ValueError("JEV model cannot be empty")
        if self.retry_times < 0:
            raise ValueError("JEV retry_times cannot be negative")
        if self.concurrency < 1:
            raise ValueError("JEV concurrency must be at least 1")


class JEVRuntime:
    """One event-loop-bound official JEV client reused for a review run."""

    def __init__(self, config: JEV) -> None:
        self.config = config
        self._client: AsyncTypeSafeClient | None = None

    async def __aenter__(self) -> Self:
        self._client = AsyncTypeSafeClient(
            api_key=self.config.key,
            model=self.config.model,
            base_url=self.config.url,
            timeout=self.config.timeout,
            retry=RetryPolicy(max_retries=self.config.retry_times),
            transport=httpx2.AsyncHTTPTransport(local_address="0.0.0.0"),
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.__aexit__(exc_type, exc_value, traceback)

    async def evaluate(self, page_index: int, request: dict[str, Any]) -> float:
        del page_index
        if self._client is None:
            raise RuntimeError("JEVRuntime must be entered before evaluation")
        response = await self._client.system_one(
            state=request["state"],
            questions=request["questions"],
        )
        return float(response.nouls["page_passes_strict_standard"].noul)
