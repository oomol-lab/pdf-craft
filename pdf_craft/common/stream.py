"""Private, disk-backed streams. EOF follows all committed records."""

import asyncio
import pickle
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Condition
from typing import Generic, TypeVar

from ..runtime import IO_DOMAIN, invoke_callback

T = TypeVar("T")


class DiskStream(Generic[T]):
    """One writer, replayable readers; backlog consumes disk rather than RAM."""

    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._condition = Condition()
        self._count = 0
        self._closed = False
        self._error: BaseException | None = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._available = asyncio.Event()

    def _notify(self):
        self._condition.notify_all()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._available.set)

    def append(self, value: T) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("stream is closed")
            with (self.path / str(self._count)).open("wb") as file:
                pickle.dump(value, file, protocol=pickle.HIGHEST_PROTOCOL)
            self._count += 1
            self._notify()

    def close(self, error: BaseException | None = None) -> None:
        with self._condition:
            self._error = error
            self._closed = True
            self._notify()

    def read(self) -> Iterator[T]:
        index = 0
        while True:
            with self._condition:
                self._condition.wait_for(lambda: index < self._count or self._closed)
                if self._error is not None:
                    raise self._error
                if index == self._count:
                    return
            with (self.path / str(index)).open("rb") as file:
                yield pickle.load(file)
            index += 1

    async def aread(self):
        """Wait without occupying a worker; cancellation never waits for input."""
        index = 0
        while True:
            self._available.clear()
            with self._condition:
                count, closed, error = self._count, self._closed, self._error
            if error is not None:
                raise error
            if index < count:
                def load(index=index):
                    with (self.path / str(index)).open("rb") as file:
                        return pickle.load(file)
                yield await IO_DOMAIN.run(load)
                index += 1
            elif closed:
                return
            else:
                await self._available.wait()


class DiskDelivery(Generic[T]):
    """Await callbacks in order without occupying the producer's resource pool."""

    def __init__(self, path: Path, callback: Callable[[T], object]) -> None:
        self._stream: DiskStream[T] = DiskStream(path)
        self._callback = callback
        self._task = asyncio.create_task(self._deliver())

    async def append(self, value: T) -> None:
        if self._task.done():
            await self._task
        await IO_DOMAIN.run(self._stream.append, value)

    async def _deliver(self) -> None:
        async for value in self._stream.aread():
            await invoke_callback(self._callback, value)

    async def finish(self) -> None:
        self._stream.close()
        await self._task

    async def cancel(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
