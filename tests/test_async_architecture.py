# pylint: disable=protected-access
"""Guard the single async implementation and its outer synchronous facade."""

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from pdf_craft import AsyncPDFCraft, PDFCraft
from pdf_craft.llm import guaranteed, loop, runtime


class TestAsyncArchitecture(unittest.TestCase):
    def test_only_public_sync_facade_can_start_an_event_loop(self):
        package = Path(__file__).resolve().parents[1] / "pdf_craft"
        for path in package.rglob("*.py"):
            if path.parent == package / "sync":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                with self.subTest(path=str(path), line=node.lineno):
                    self.assertNotEqual(name, "run_sync")
                    if path.name != "runtime.py":
                        self.assertNotIn(name, {"run_until_complete", "new_event_loop"})

    def test_blocking_llm_and_repair_paths_are_absent(self):
        self.assertFalse(hasattr(guaranteed, "request_guaranteed_json_blocking"))
        self.assertFalse(hasattr(loop, "_run_repair_loop_blocking"))
        self.assertFalse(hasattr(runtime.LLMRuntime, "_request_blocking"))
        self.assertFalse(hasattr(runtime.LLMContext, "__enter__"))
        self.assertTrue(inspect.iscoroutinefunction(runtime.LLMRuntime.request))

    def test_repair_protocol_exposes_only_async_execution(self):
        self.assertFalse(hasattr(loop, "ResponseProtocol"))
        for method in ("validate", "empty", "exhausted"):
            with self.subTest(method=method):
                self.assertTrue(inspect.iscoroutinefunction(
                    getattr(loop.AsyncResponseProtocol, method),
                ))

    def test_every_sync_operation_awaits_its_corresponding_async_operation(self):
        facade = PDFCraft()
        for name, method in inspect.getmembers(PDFCraft, inspect.isfunction):
            if name.startswith("_"):
                continue
            with self.subTest(operation=name):
                self.assertTrue(inspect.iscoroutinefunction(getattr(AsyncPDFCraft, name)))
                operation = AsyncMock()
                # Only the delegated operation runs; no I/O or conversion is needed.
                facade._async_craft = SimpleNamespace(**{name: operation})  # type: ignore[assignment]
                arguments = {
                    key: object() for key, parameter in inspect.signature(method).parameters.items()
                    if key != "self" and parameter.default is inspect.Parameter.empty
                    and parameter.kind not in {
                        inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD,
                    }
                }
                getattr(facade, name)(**arguments)
                operation.assert_awaited_once()
