"""Private one-shot worker for isolated native execution domains."""

from __future__ import annotations

import importlib
import pickle
import sys
import traceback
from typing import Any


def _resolve(module_name: str, qualified_name: str) -> Any:
    if not module_name.startswith("pdf_craft.") or "<locals>" in qualified_name:
        raise ValueError("isolated workers only execute module-level pdf_craft callables")
    value: Any = importlib.import_module(module_name)
    for component in qualified_name.split("."):
        value = getattr(value, component)
    return value


def main() -> None:
    del sys.argv[1:]  # The domain name is diagnostic context for the parent.
    try:
        module_name, qualified_name, args, kwargs = pickle.loads(sys.stdin.buffer.read())
        result = _resolve(module_name, qualified_name)(*args, **kwargs)
        response = ("ok", result, "")
    except BaseException as error:  # The parent must receive native worker failures.
        try:
            pickle.dumps(error)
            value = error
        except (pickle.PickleError, TypeError, AttributeError):
            value = f"{type(error).__name__}: {error}"
        response = ("error", value, traceback.format_exc())
    sys.stdout.buffer.write(pickle.dumps(response, protocol=pickle.HIGHEST_PROTOCOL))


if __name__ == "__main__":
    main()
