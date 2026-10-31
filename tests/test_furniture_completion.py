"""Deterministic interleavings at the furniture producer's EOF boundary."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft.pipeline.pdf import streaming


class FurnitureCompletionTests(IsolatedAsyncioTestCase):
    async def run_producer(self, *, finish_during_read=False, unresolved=False, error=None):
        runner = asyncio.get_running_loop().create_future()
        schedule = SimpleNamespace(runner=runner, furniture_changed=asyncio.Event(), furniture=None)
        reads, records = [], []
        if not finish_during_read:
            runner.set_result(None)

        def snapshot(_plan, index, *_args):
            reads.append(index)
            return None if unresolved or not runner.done() else []

        def write(path, value):
            records.append((path.parent.name, path.name, value))

        async def io(function, *args):
            result = function(*args)
            if function is snapshot and not runner.done():
                if error is None:
                    runner.set_result(None)
                else:
                    runner.set_exception(error)
            return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slots = {7: [(11, SimpleNamespace(bbox=(1, 2, 3, 4), furniture_identity=(7, "1")))]}
            with patch.object(streaming, 'page_furniture', snapshot), \
                 patch.object(streaming, 'write_record', write), \
                 patch.object(streaming.IO_DOMAIN, 'run', io):
                await streaming.produce_furniture(schedule, slots, root / 'feed', root / 'previews',
                                                  {}, 300, root, {})
        return reads, records

    async def test_completion_during_snapshot_rescans_and_delivers_once(self):
        reads, records = await self.run_producer(finish_during_read=True)
        self.assertEqual(reads, [7, 7])
        self.assertEqual(records, [('feed', '11', None), ('previews', '11', None)])

    async def test_already_completed_producer_delivers_without_rescan(self):
        reads, records = await self.run_producer()
        self.assertEqual(reads, [7])
        self.assertEqual(len(records), 2)

    async def test_truly_unresolved_dependency_still_fails(self):
        for interleaved in (False, True):
            with self.subTest(interleaved=interleaved):
                with self.assertRaisesRegex(RuntimeError, 'unresolved page dependencies'):
                    await self.run_producer(finish_during_read=interleaved, unresolved=True)

    async def test_producer_failure_during_snapshot_keeps_original_error(self):
        error = ValueError('producer failed')
        with self.assertRaises(ValueError) as caught:
            await self.run_producer(finish_during_read=True, error=error)
        self.assertIs(caught.exception, error)
