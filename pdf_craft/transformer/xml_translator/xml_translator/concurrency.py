from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

P = TypeVar("P")
R = TypeVar("R")


def run_concurrency(
    parameters: Iterable[P],
    execute: Callable[[P], R],
    concurrency: int,
) -> Iterable[R]:
    assert concurrency >= 1, "the concurrency must be at least 1"
    # Fast path: concurrency == 1, no thread overhead
    if concurrency == 1:
        for param in parameters:
            yield execute(param)
        return

    executor = ThreadPoolExecutor(max_workers=concurrency)
    did_shutdown = False
    try:
        futures: deque[Future[R]] = deque()
        params_iter = iter(parameters)
        for _ in range(concurrency):
            try:
                param = next(params_iter)
                future = executor.submit(execute, param)
                futures.append(future)
            except StopIteration:
                break

        while futures:
            future = futures.popleft()
            yield future.result()
            try:
                param = next(params_iter)
                new_future = executor.submit(execute, param)
                futures.append(new_future)
            except StopIteration:
                pass

    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        did_shutdown = True
        raise

    finally:
        if not did_shutdown:
            executor.shutdown(wait=True)
