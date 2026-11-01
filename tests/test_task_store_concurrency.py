"""Task contexts must not cross between progress and EPUB mapping IO workers."""
# pylint: disable=protected-access
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft.pipeline.epub.cfi import ContentCFILocator
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator.source_store import TaskStore
from tests.test_epub_cfi import endpoints


class ObservedLock:
    """Signal contention without relying on a timeout to schedule another reader."""
    def __init__(self, lock, attempted):
        self.lock, self.attempted = lock, attempted

    def __enter__(self):
        if current_thread().name.endswith('_1'):
            self.attempted.set()
        self.lock.acquire()

    def __exit__(self, *args):
        self.lock.release()


class TaskStoreConcurrencyTests(TestCase):
    def _interleave(self, first_operation, second_operation, second_identity=2):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root)
            trees, segments = {}, {}
            for identity, text in ((1, 'First 😀 source'), (2, 'Second &amp; source')):
                tree = fromstring(f'<html><head/><body><p>{text}</p></body></html>')
                trees[identity] = tree
                segments[identity] = list(search_text_segments(tree, track_origins=True))
                store.save(identity, SimpleNamespace(identity=identity, locator=ContentCFILocator(tree)))
            at_close, release, competing = Event(), Event(), Event()
            # Gate after unpickling but before leaving the file context. In the
            # old store, another worker could replace cache here, then the first
            # worker published its identity and returned that other task.
            original_open = Path.open

            @contextmanager
            def open_file(path, *args, **kwargs):
                with original_open(path, *args, **kwargs) as file:
                    yield file
                if path == root / '1' and current_thread().name.endswith('_0'):
                    at_close.set()
                    if not release.wait(5):
                        raise AssertionError('first reader was not released')

            # The fixed implementation signals the competing acquisition; old
            # implementations without a lock signal after the competing read.
            # Both schedules are event-driven, not "sleep and hope" races.
            lock_context = (patch.object(store, '_lock', ObservedLock(store._lock, competing))
                            if hasattr(store, '_lock') else nullcontext())

            def second():
                try:
                    return getattr(store, second_operation)(second_identity)
                finally:
                    competing.set()

            with lock_context, patch.object(Path, 'open', open_file), ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix='task-store') as executor:
                first = executor.submit(getattr(store, first_operation), 1)
                try:
                    self.assertTrue(at_close.wait(5), 'first reader did not reach gate')
                    other = executor.submit(second)
                    self.assertTrue(competing.wait(5), 'second worker did not contend')
                finally:
                    release.set()
                first_task, second_task = first.result(timeout=5), other.result(timeout=5)
            for identity, task in ((1, first_task), (second_identity, second_task)):
                self.assertIsNotNone(task)
                self.assertEqual(task.identity, identity)
                cfi = task.locator.capture(segments[identity]).cfi('/6/2')
                start, end = endpoints(trees[identity], cfi)
                expected = ''.join(trees[identity].itertext())
                self.assertEqual(start, (expected, ''))
                self.assertEqual(end, (expected, expected))
            self.assertEqual((root / '1').exists(), first_operation != 'pop' and not (
                second_operation == 'pop' and second_identity == 1))
            self.assertEqual((root / '2').exists(), not (second_operation == 'pop' and second_identity == 2))

    def test_competing_loads_keep_each_tasks_cfi(self):
        self._interleave('load', 'load')

    def test_pop_of_another_task_cannot_clear_inflight_load(self):
        self._interleave('load', 'pop')

    def test_pop_cannot_return_a_different_task(self):
        self._interleave('pop', 'load')

    def test_load_then_pop_same_identity_share_one_context(self):
        self._interleave('load', 'pop', 1)

    def test_cache_is_bounded_and_replaced_task_is_not_stale(self):
        with TemporaryDirectory() as directory:
            store = TaskStore(Path(directory))
            a, b = store.allocate(), store.allocate()
            store.save(a, SimpleNamespace(value='first'))
            first = store.load(a)
            self.assertIs(store.load(a), first)
            store.save(b, SimpleNamespace(value='second'))
            store.load(b)
            self.assertIsNot(store.load(a), first)
            store.save(a, SimpleNamespace(value='updated'))
            self.assertEqual(store.pop(a).value, 'updated')
            with self.assertRaises(FileNotFoundError):
                store.load(a)
            self.assertEqual(store.pop(b).value, 'second')

    def test_failed_read_releases_lock_and_keeps_other_task(self):
        with TemporaryDirectory() as directory:
            store = TaskStore(Path(directory))
            store.save(1, 'original')
            self.assertEqual(store.load(1), 'original')
            with self.assertRaises(FileNotFoundError):
                store.load(2)
            self.assertEqual(store.pop(1), 'original')

    def test_failed_delete_preserves_context_and_releases_lock(self):
        with TemporaryDirectory() as directory:
            store = TaskStore(Path(directory))
            store.save(1, 'original')
            with patch.object(Path, 'unlink', side_effect=OSError('delete failed')):
                with self.assertRaisesRegex(OSError, 'delete failed'):
                    store.pop(1)
            self.assertEqual(store.load(1), 'original')
            self.assertEqual(store.pop(1), 'original')
