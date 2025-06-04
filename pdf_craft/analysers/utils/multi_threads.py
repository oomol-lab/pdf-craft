from typing import Generic, TypeVar, Callable
from threading import Lock, Thread


T = TypeVar("T")
P = TypeVar("P")

class MultiThreads:
  def __init__(self, threads_count: int):
    self._threads_count: int = threads_count
    if threads_count <= 0:
      raise ValueError("threads_count must be greater than 0")

  def run(
        self,
        next_task: Callable[[], T | None],
        thread_payload: Callable[[], P],
        invoke: Callable[[P, T], None],
      ) -> None:

    if self._threads_count == 1:
      payload = thread_payload()
      while True:
        task = next_task()
        if task is None:
          break
        invoke(payload, task)
    else:
      invoker: _Invoker[P, T] = _Invoker(
        threads_count=self._threads_count,
        next_task=next_task,
        thread_payload=thread_payload,
        invoke=invoke,
      )
      invoker.do()

class _Invoker(Generic[P, T]):
  def __init__(
        self,
        threads_count: int,
        next_task: Callable[[], T | None],
        thread_payload: Callable[[], P],
        invoke: Callable[[T], None],
      ) -> None:

    self._threads_count: int = threads_count
    self._thread_payload: Callable[[], P] = thread_payload
    self._next_task: Callable[[], T | None] = next_task
    self._invoke: Callable[[T], None] = invoke
    self._threads: list[Thread] = []
    self._task_lock: Lock = Lock()
    self._done: bool = False
    self._error: Exception | None = None

  def do(self):
    for _ in range(self._threads_count):
      payload = self._thread_payload()
      self._threads.append(Thread(
        target=self._run_thread_loop,
        args=(payload,),
      ))
    for thread in self._threads:
      thread.start()
    for thread in self._threads:
      thread.join()
    if self._error is not None:
      raise self._error

  def _run_thread_loop(self, payload: P):
    while True:
      task = self._get_next_task()
      if task is None:
        break
      try:
        self._invoke(payload, task)
      except Exception as error:
        with self._task_lock:
          if self._error is not None:
            break
          self._error = error

  def _get_next_task(self) -> T | None:
    with self._task_lock:
      if self._error is not None:
        return None
      if self._done:
        return None
      task = self._next_task()
      if task is None:
        self._done = True
      return task
