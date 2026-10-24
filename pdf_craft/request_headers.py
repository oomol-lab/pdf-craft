from collections.abc import Mapping
from types import MappingProxyType

# Credentials and payload encoding stay owned by the client that sends the request.
_RESERVED_HEADERS = frozenset({"authorization", "content-type"})


def request_headers(headers: Mapping[str, str] | None) -> Mapping[str, str]:
    """Copy caller-supplied extra HTTP headers into an immutable mapping.

    Hosts use these headers to correlate provider calls with their own request,
    for example a gateway trace id. Reserved headers cannot be overridden.
    """
    result: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("request header names and values must be strings")
        if name.strip().lower() in _RESERVED_HEADERS:
            raise ValueError(f"request header {name!r} is managed by pdf-craft")
        result[name] = value
    return MappingProxyType(result)
