"""Request-local, disk-backed evidence retained only on protocol exhaustion."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import parse_qsl, urlsplit
import uuid

from ..runtime import IO_DOMAIN

_ACTIVE: ContextVar[FailureLog | None] = ContextVar('llm_failure_log', default=None)
_URL = re.compile(r'https?://[^\s<>\'"`]+')
_AUTH = re.compile(r'(?im)(authorization["\']?\s*[:=]\s*["\']?)[^\r\n\'"<>]+')
_KEY = re.compile(r'(?i)(api[_-]?key["\']?\s*[:=]\s*["\']?)[^\s,;\'"<>]+')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def active_log() -> FailureLog | None:
    return _ACTIVE.get()


def _private_url(match: re.Match[str]) -> str:
    url = match.group()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return '[REDACTED_URL]'
    sensitive = ('signature', 'token', 'credential', 'accesskey', 'authorization', 'security-token')
    if parsed.username or parsed.password or any(
            key.lower() in {'sig', 'key', 'auth', 'auth_key', 'password', 'jwt'}
            or any(part in key.lower() for part in sensitive) for key, _ in parse_qsl(parsed.query)):
        return '[REDACTED_URL]'
    return url


class FailureLog:
    """One loop; at most the current attempt is held in memory."""

    def __init__(self, path: Path | None = None, context: dict[str, Any] | None = None):
        self.path = path
        self.context = {'stage': 'protocol_repair', **(context or {})}
        self.identifier = uuid.uuid4().hex
        self.started = _now()
        self.pending: Path | None = None
        self.record: dict[str, Any] = {}
        self.secrets: set[str] = set()
        self.complete = True
        self.retained = False

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _ACTIVE.set(self)
        try:
            yield
        finally:
            _ACTIVE.reset(token)

    def _redact(self, value):
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            value = _URL.sub(_private_url, value)
            value = _AUTH.sub(lambda m: m[1] + '[REDACTED]', value)
            return _KEY.sub(lambda m: m[1] + '[REDACTED]', value)
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    async def _io(self, action: Callable[[], Any]):
        try:
            return await IO_DOMAIN.run(action)
        except Exception as error:  # Evidence failures must not replace business results.
            self.complete = False
            logging.getLogger(__name__).warning('LLM failure evidence incomplete (%s)', type(error).__name__)
            return None

    def _directory(self) -> Path:
        assert self.path is not None
        if self.pending is None:
            self.path.mkdir(parents=True, exist_ok=True)
            self.pending = Path(tempfile.mkdtemp(prefix='.pending-', dir=self.path))
        return self.pending

    def _append(self, filename: str, record: dict) -> None:
        with (self._directory() / filename).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(self._redact(record), ensure_ascii=True) + '\n')

    def begin(self, attempt: int, messages) -> None:
        self.record = {'attempt': attempt, 'started_at': _now(), 'messages': [
            {'role': message.role.name.lower(), 'content': message.message} for message in messages]}

    def configure(self, config, messages, parameters: dict, request_id: str) -> None:
        if self.path is None:
            self.path = getattr(config, 'failure_log_path', None)
        if self.path is None:
            return
        if config.key:
            self.secrets.add(config.key)
        self.record.pop('provider_request_id', None)
        self.context['model'] = config.model
        self.record.update(model=config.model, parameters=parameters, local_request_id=request_id,
                           messages=[{'role': m.role.name.lower(), 'content': m.message} for m in messages])

    async def request(self, config, messages, parameters: dict, request_id: str) -> None:
        self.configure(config, messages, parameters, request_id)
        if self.path is None:
            return
        await self._io(lambda: self._append('transports.jsonl', {
            'attempt': self.record['attempt'], 'event': 'request', 'time': _now(),
            'model': config.model, 'parameters': parameters, 'local_request_id': request_id,
            'messages': self.record['messages']}))

    def provider_request(self, identifier: str | None) -> None:
        if identifier:
            self.record['provider_request_id'] = identifier

    async def response(self, text: str) -> None:
        if self.path is not None:
            await self._io(lambda: self._append('transports.jsonl', {
                'attempt': self.record['attempt'], 'event': 'response', 'time': _now(), 'response': text,
                'provider_request_id': self.record.get('provider_request_id')}))

    async def validated(self, response: str, result) -> None:
        if self.path is None:
            return
        self.record.update(response=response, completed_at=_now(), validation=type(result).__name__,
                           feedback=getattr(result, 'feedback', None))
        await self._io(lambda: self._append('attempts.jsonl', self.record))

    async def retain(self, error: Exception, attempts: int) -> str | None:
        if self.path is None:
            return None
        destination = self.path
        def publish():
            directory = self._directory()
            summary = {**self.context, 'format_version': 1, 'failure_log_id': self.identifier,
                       'started_at': self.started, 'completed_at': _now(), 'attempts': attempts,
                       'complete': self.complete, 'error': {'type': type(error).__name__, 'message': str(error)}}
            (directory / 'summary.json').write_text(
                json.dumps(self._redact(summary), ensure_ascii=True, indent=2), encoding='utf-8')
            directory.rename(destination / self.identifier)
            self.retained = True
            return self.identifier
        return await self._io(publish)

    async def close(self) -> None:
        pending = self.pending
        if pending is not None and not self.retained:
            await self._io(lambda: shutil.rmtree(pending))
        self.record.clear()
        self.secrets.clear()
