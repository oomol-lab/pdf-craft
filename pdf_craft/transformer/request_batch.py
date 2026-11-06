"""Bounded, identity-preserving transport for independent translation/fill calls.

Semantic units own their validation and retry budgets. This layer only packs
ready conversations; it never holds model executor capacity while gathering.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from functools import wraps
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
from typing import Any, TypeVar, cast

from .._policy import current
from ..common import safe_text
from ..concurrency import settle_tasks
from ..llm import Message, MessageRole
from ..llm.failure_log import active_log, batch_log_scope
from ..usage import current_usage_scope, shared_request_usage

MAX_BATCH_UNITS = 64
READY_DELAY = 0.01


_REQUEST_WINDOW: ContextVar[asyncio.Semaphore | None] = ContextVar('translation_request_window', default=None)
_F = TypeVar('_F', bound=Callable[..., Awaitable[Any]])


@contextmanager
def request_window(window: int):
    """One operation's request slots, inherited across content adapters."""
    if window < 1:
        raise ValueError('window must be at least 1')
    if _REQUEST_WINDOW.get() is not None:
        yield
        return
    token = _REQUEST_WINDOW.set(asyncio.Semaphore(window))
    try:
        yield
    finally:
        _REQUEST_WINDOW.reset(token)


def with_request_window(function: _F) -> _F:
    """Keep a method's window as transport capacity, outside ready collection."""
    signature = inspect.signature(function)
    @wraps(function)
    async def invoke(*args, **kwargs):
        window = signature.bind_partial(*args, **kwargs).arguments.get('window', 1)
        with request_window(window):
            return await function(*args, **kwargs)
    return cast(_F, invoke)


class _IncompleteBatch(Exception):
    """Roll back a partial envelope's cache without discarding valid siblings."""


@dataclass
class _ResponseObject:
    # Keep duplicate fields until all competing identities have been examined.
    pairs: list[tuple[str, Any]]


@dataclass(eq=False)
class _Call:
    messages: list[Message]
    context: Any
    runtime: Any
    options: dict
    future: asyncio.Future
    log: Any
    usage: Any
    tokens: int
    execution_context: Any
    source_math: Any = None
    source_text: str | None = None


@dataclass
class _Batch:
    overhead: int = 0
    calls: list[_Call] = field(default_factory=list)
    task: asyncio.Task | None = None


class RequestBatcher:
    """One translator's ready calls, isolated by operation and request settings.

    The mapper supplies explicit bounded content cohorts. Their members flush
    when ready, so file/template IO cannot arbitrarily fragment requests. Calls
    outside a content cohort retain a short sparse-input deadline. Each batch
    holds at most 64 conversations and the existing token budget; oversize
    semantic units retain their single-call path. LLMRuntime owns execution.
    """

    def __init__(self, max_score: int, *, fill_runtime=None):
        self.fill_runtime = fill_runtime
        self.budget = max(4096, max_score * 4)
        self.pending: dict[tuple, _Batch] = {}
        self.timers: dict[tuple, asyncio.TimerHandle] = {}

    async def request(self, context, messages, *, runtime, stage, source_math=None, source_text=None, **options):
        if stage == 'translation' and self.fill_runtime is None:
            raise ValueError('Free translation requires an engineering fill runtime')
        encoding = await runtime.encoding()
        # Shared system instructions are transmitted and charged once. Reserve
        # output proportional to each unit's actual payload, not N copies of
        # the shared prompt. Repair histories remain private to each unit.
        shared = messages[0].message if messages and messages[0].role == MessageRole.SYSTEM else ''
        payload = self._payload(messages, '0', shared)
        tokens = len(encoding.encode(json.dumps(payload, ensure_ascii=False), disallowed_special=())) * 2
        instruction = (await runtime.template('batch')).render(shared=shared)
        overhead = len(encoding.encode(instruction, disallowed_special=())) + 16
        scope = current_usage_scope()
        policy = current()
        from .group_requests import current_group
        group = current_group()
        key = (group, policy.task_id, policy.request_headers, id(scope.collector) if scope else None,
               shared, stage, tuple(sorted(options.items())))
        loop = asyncio.get_running_loop()
        call = _Call(messages, context, runtime, options, loop.create_future(), active_log(), scope, tokens, copy_context(), source_math, source_text)
        batch = self.pending.get(key)
        if batch is not None and (len(batch.calls) == MAX_BATCH_UNITS or
                                  batch.overhead + sum(item.tokens for item in batch.calls) + tokens > self.budget):
            self._launch(key)
            batch = None
        if batch is None:
            batch = self.pending[key] = _Batch(overhead=overhead)
            if group is None:
                self.timers[key] = loop.call_later(READY_DELAY, self._launch, key)
        batch.calls.append(call)
        if overhead + tokens >= self.budget or len(batch.calls) >= MAX_BATCH_UNITS:
            self._launch(key)
        try:
            with group.request(lambda: self._launch(key)) if group is not None else nullcontext():
                return await call.future
        finally:
            # A cancelled consumer cannot cancel siblings' shared request.
            # The last consumer owns and awaits transport cancellation/cleanup.
            if all(item.future.done() for item in batch.calls):
                if self.pending.get(key) is batch:
                    self.pending.pop(key)
                    timer = self.timers.pop(key, None)
                    if timer is not None:
                        timer.cancel()
                if batch.task is not None and not batch.task.done():
                    batch.task.cancel()
                    await settle_tasks([batch.task])

    @staticmethod
    def _payload(messages, identity, shared=''):
        if shared:
            messages = messages[1:]
        return {'id': identity, 'messages': [
            {'role': message.role.name.lower(), 'content': message.message} for message in messages]}

    def _launch(self, key):
        batch = self.pending.pop(key, None)
        timer = self.timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        if batch is not None:
            live = [call for call in batch.calls if not call.future.done()]
            if live:
                batch.task = asyncio.create_task(self._execute(batch, key[-2]), context=live[0].execution_context)

    async def _execute(self, batch, stage):
        try:
            capacity = _REQUEST_WINDOW.get()
            if capacity is None:
                await self._deliver(batch, stage)
            else:
                async with capacity:
                    await self._deliver(batch, stage)
        except asyncio.CancelledError:
            # Includes cancellation while queued for a transport slot.
            for call in batch.calls:
                call.future.cancel()
            raise

    async def _deliver(self, batch, stage):
        calls = [call for call in batch.calls if not call.future.done()]
        if not calls:
            return
        first = calls[0]
        try:
            if stage == 'translation' and self.fill_runtime is not None:
                from .free_translation import fill_translation_group
                shared = first.messages[0]
                messages = [shared, Message(MessageRole.USER, '\n\n'.join(call.messages[-1].message for call in calls))]
                with shared_request_usage([call.usage for call in calls]), batch_log_scope(lambda: [call.log for call in calls if not call.future.done()]):
                    async with first.runtime.context(cache_seed_content=getattr(first.context, 'cache_seed_content', None)) as context:
                        raw = await context.request(messages, allow_empty=True, **first.options)
                    answers = ({'0': raw} if len(calls) == 1 and first.source_math is None
                               else await fill_translation_group(self.fill_runtime, calls, raw))
            elif len(calls) == 1:
                response = await first.context.request(first.messages, **first.options)
                answers = {'0': response}
            else:
                shared = first.messages[0].message if first.messages and first.messages[0].role == MessageRole.SYSTEM else ''
                messages = [Message(MessageRole.SYSTEM, (await first.runtime.template('batch')).render(shared=shared)),
                            Message(MessageRole.USER, json.dumps([
                                self._payload(call.messages, str(index), shared) for index, call in enumerate(calls)
                            ], ensure_ascii=False))]
                with shared_request_usage([call.usage for call in calls]), batch_log_scope(lambda: [call.log for call in calls if not call.future.done()]):
                    # A cancelled first consumer may close its context while
                    # siblings still need this request. The batch owns cache
                    # commit/cleanup independently of any one consumer.
                    answers = {}
                    try:
                        async with first.runtime.context(cache_seed_content=getattr(first.context, 'cache_seed_content', None)) as context:
                            response = await context.request(messages, **first.options)
                            answers = self._answers(response, len(calls))
                            if len(answers) != len(calls):
                                raise _IncompleteBatch()
                    except _IncompleteBatch:
                        pass
            for index, call in enumerate(calls):
                if call.future.done():
                    continue
                answer = answers.get(str(index))
                if answer is not None:
                    call.future.set_result(answer)
                else:
                    # Missing engineering output consumes only this member's
                    # existing fill attempt; successful siblings remain adopted.
                    call.future.set_result('')
        except asyncio.CancelledError:
            for call in calls:
                call.future.cancel()
            raise
        except Exception as error:
            for call in calls:
                if not call.future.done():
                    call.future.set_exception(error)

    @staticmethod
    def _answers(response, count):
        response = response.strip()
        if response.startswith('```json\n') and response.endswith('\n```'):
            response = response[8:-4]
        elif response.startswith('```\n') and response.endswith('\n```'):
            response = response[4:-4]
        try:
            decoded = json.loads(response, object_pairs_hook=_ResponseObject)
        except (ValueError, TypeError):
            return {}
        if not isinstance(decoded, list):
            return {}
        answers = {}
        expected = {str(index) for index in range(count)}
        seen, ambiguous = set(), set()
        for item in decoded:
            if not isinstance(item, _ResponseObject):
                continue
            identities = {value for key, value in item.pairs
                          if key == 'id' and isinstance(value, str) and value in expected}
            ambiguous.update(seen & identities)
            seen.update(identities)
            fields = dict(item.pairs)
            if len(fields) != len(item.pairs):
                # A malformed object's IDs still conflict with valid entries,
                # in either order. Duplicate id fields can taint multiple IDs.
                ambiguous.update(identities)
                continue
            if (set(fields) != {'id', 'text'} or len(identities) != 1
                    or not isinstance(fields['text'], str)):
                continue
            identity = next(iter(identities))
            # JSON escapes become characters only here; match the ordinary
            # LLM ingress after decoding rather than letting escaped control
            # characters bypass the runtime's safe_text normalization.
            text = safe_text(fields['text'])
            if text.strip():
                answers[identity] = text
        return {identity: answer for identity, answer in answers.items() if identity not in ambiguous}
