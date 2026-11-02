"""Bounded, identity-preserving transport for independent translation/fill calls.

Semantic units own their validation and retry budgets. This layer only packs
ready conversations; it never holds model executor capacity while gathering.
"""
from __future__ import annotations

import asyncio
from contextvars import copy_context
from dataclasses import dataclass, field
import json
from typing import Any

from .._policy import current
from ..concurrency import settle_tasks
from ..llm import Message, MessageRole
from ..llm.failure_log import active_log, batch_log_scope
from ..usage import current_usage_scope, shared_request_usage

MAX_BATCH_UNITS = 8
READY_DELAY = 0.01
_BATCH_INSTRUCTION = '''Execute each independent conversation in the JSON array below.
Every entry has an id and messages (role/content). Follow each conversation's
instructions independently. Preserve all XML IDs and math references within its
own conversation; the same local ID in another conversation is unrelated.
Return ONLY a JSON array of objects with exactly id and text. Copy every input
id exactly once. text is that conversation's complete answer as a JSON string,
including its XML when requested. Never merge, omit or exchange answers.'''


class BatchResponseError(ValueError):
    """A translation response could not be bound to its explicit input identity."""


class _IncompleteBatch(Exception):
    """Roll back a partial envelope's cache without discarding valid siblings."""


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


@dataclass
class _Batch:
    calls: list[_Call] = field(default_factory=list)
    task: asyncio.Task | None = None


class RequestBatcher:
    """One translator's ready calls, isolated by operation and request settings.

    The caller's existing bounded scheduling window limits producers. A queue
    holds at most eight conversations and a 10ms readiness deadline. Oversize
    conversations keep the established single-call path rather than truncating
    a semantic unit. Executor concurrency remains owned by LLMRuntime.
    """

    def __init__(self, max_score: int):
        self.budget = max(4096, max_score * 4)
        self.pending: dict[tuple, _Batch] = {}
        self.timers: dict[tuple, asyncio.TimerHandle] = {}

    async def request(self, context, messages, *, runtime, stage, **options):
        encoding = await runtime.encoding()
        # Include the envelope, prompt, repair history, and an output allowance
        # equal to the input size. This is deliberately more conservative than
        # the mapper's source-only score.
        payload = self._payload(messages, '0')
        tokens = (len(encoding.encode(json.dumps(payload, ensure_ascii=False), disallowed_special=())) * 2
                  + len(encoding.encode(_BATCH_INSTRUCTION)))
        scope = current_usage_scope()
        policy = current()
        key = (policy.task_id, policy.request_headers, id(scope.collector) if scope else None,
               stage, tuple(sorted(options.items())))
        loop = asyncio.get_running_loop()
        call = _Call(messages, context, runtime, options, loop.create_future(), active_log(), scope, tokens, copy_context())
        batch = self.pending.get(key)
        if batch is not None and (len(batch.calls) == MAX_BATCH_UNITS or
                                  sum(item.tokens for item in batch.calls) + tokens > self.budget):
            self._launch(key)
            batch = None
        if batch is None:
            batch = self.pending[key] = _Batch()
            self.timers[key] = loop.call_later(READY_DELAY, self._launch, key)
        batch.calls.append(call)
        if tokens >= self.budget or len(batch.calls) >= MAX_BATCH_UNITS:
            self._launch(key)
        try:
            return await call.future
        finally:
            # A cancelled consumer cannot cancel siblings' shared request.
            # The last consumer owns and awaits transport cancellation/cleanup.
            if all(item.future.done() for item in batch.calls):
                if self.pending.get(key) is batch:
                    self.pending.pop(key)
                    self.timers.pop(key).cancel()
                if batch.task is not None and not batch.task.done():
                    batch.task.cancel()
                    await settle_tasks([batch.task])

    @staticmethod
    def _payload(messages, identity):
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
        calls = [call for call in batch.calls if not call.future.done()]
        if not calls:
            return
        first = calls[0]
        try:
            if len(calls) == 1:
                response = await first.context.request(first.messages, **first.options)
                answers = {'0': response}
            else:
                messages = [Message(MessageRole.SYSTEM, _BATCH_INSTRUCTION),
                            Message(MessageRole.USER, json.dumps([
                                self._payload(call.messages, str(index)) for index, call in enumerate(calls)
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
                elif stage == 'fill':
                    # Consume this unit's existing fill attempt, not a hidden
                    # batch-to-single retry. Accepted siblings have already left.
                    call.future.set_result('')
                else:
                    call.future.set_exception(BatchResponseError(f'Missing translation batch id={index}'))
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
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON field')
                result[key] = value
            return result
        try:
            decoded = json.loads(response, object_pairs_hook=unique_object)
        except (ValueError, TypeError):
            return {}
        if not isinstance(decoded, list):
            return {}
        answers = {}
        expected = {str(index) for index in range(count)}
        for item in decoded:
            if (not isinstance(item, dict) or set(item) != {'id', 'text'} or
                    not isinstance(item['id'], str) or item['id'] not in expected or
                    item['id'] in answers or not isinstance(item['text'], str)):
                return {}
            answers[item['id']] = item['text']
        return answers
