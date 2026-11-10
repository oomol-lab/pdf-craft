"""Keep group evidence through detached mapping and all owning submissions."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ....llm.failure_scope import FailureScope
from .source_store import source_identity


class FillDiagnostics:
    """Per-operation ledger; identities, not source DOMs, cross the reorder queue."""

    def __init__(self):
        self.pending: dict[int, tuple[FailureScope, set[int]]] = {}
        self.next_id = 0

    @asynccontextmanager
    async def group(self, segments) -> AsyncIterator[int]:
        self.next_id += 1
        key = self.next_id
        scope = FailureScope()
        roots = {source_identity(segment.head.root) for segment in segments
                 if not segment._translation_context}  # pylint: disable=protected-access
        self.pending[key] = scope, roots
        try:
            with scope.scope():
                yield key
        except BaseException as error:
            await self.fail(key, error)
            raise
        finally:
            if not scope.logs:
                self.pending.pop(key, None)

    async def fail(self, key: int, error: BaseException) -> None:
        pending = self.pending.pop(key, None)
        if pending is not None:
            await pending[0].close(error)

    @asynccontextmanager
    async def processing(self, key: int) -> AsyncIterator[None]:
        try:
            yield
        except GeneratorExit:
            # Closing the mapper after a consumer failed is not a new failure
            # in whichever unrelated group happened to yield that root.
            raise
        except BaseException as error:
            await self.fail(key, error)
            raise

    @asynccontextmanager
    async def submission(self, root) -> AsyncIterator[None]:
        identity = source_identity(root)  # Before strip_source changes the root.
        keys = [key for key, (_, roots) in self.pending.items() if identity in roots]
        try:
            yield
        except BaseException as error:
            for key in keys:
                await self.fail(key, error)
            raise
        else:
            for key in keys:
                pending = self.pending.get(key)
                if pending is not None:
                    scope, roots = pending
                    roots.discard(identity)
                    if not roots:
                        self.pending.pop(key)
                        await scope.close()

    async def close(self) -> None:
        # Unsubmitted siblings cannot be attributed to the active failure.
        # Stop their staging without turning them into failed business groups.
        pending, self.pending = self.pending, {}
        for scope, _ in pending.values():
            await scope.close()
