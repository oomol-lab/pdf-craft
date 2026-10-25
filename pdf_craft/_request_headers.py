"""Generic operation-local HTTP headers for configured model services only."""
from collections.abc import Mapping
import re
from typing import TypedDict

# Authentication, routing and body framing remain owned by each HTTP client.
_RESERVED = frozenset({
    'authorization', 'content-type', 'host', 'content-length', 'transfer-encoding',
})
_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def snapshot(headers: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Validate and detach headers before the operation starts doing work."""
    if not isinstance(headers, Mapping):
        raise TypeError('request_headers must be a mapping or None')
    seen = set()
    result = []
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError('request header names and values must be strings')
        lower = name.lower()
        if lower in _RESERVED:
            raise ValueError(f'request header {name!r} is managed by the HTTP client')
        if not _NAME.fullmatch(name) or any(
            ord(char) < 32 and char != '\t' or ord(char) == 127 for char in value
        ):
            raise ValueError('invalid request header name or value')
        if lower in seen:
            raise ValueError('duplicate case-insensitive request header')
        try:
            value.encode('ascii')
        except UnicodeEncodeError as error:
            raise ValueError('request header values must be ASCII') from error
        seen.add(lower)
        result.append((name, value))
    return tuple(result)


def current_headers() -> dict[str, str]:
    """Read at send time; never install operation headers on a shared client."""
    from ._policy import current  # avoid a policy/concurrency import cycle
    return dict(current().request_headers)


class _HeaderArguments(TypedDict, total=False):
    extra_headers: Mapping[str, str]


def extra_header_arguments() -> _HeaderArguments:
    """Omit the optional SDK argument when no headers were supplied."""
    headers = current_headers()
    return {"extra_headers": headers} if headers else {}
