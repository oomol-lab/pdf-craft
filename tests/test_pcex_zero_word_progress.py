"""PCEX pre-scan and real mapper agree on symbol-only narrative units."""
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import tostring

from pdf_craft import AsyncPDFCraft, ProgressEventKind, UnitKind, UnitOutcome, XMLTranslator
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, encode
from tests.extraction_helpers import make_extraction
from tests.test_translation_word_progress import identity_fill, translator


class ZeroWordProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_symbol_only_and_mixed_content_finish_with_exact_counts(self):
        for texts in (['◆'], ['Hello world', '◆', '!!!', '   ']):
            with self.subTest(texts=texts), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                extraction = make_extraction(root / 'source')
                chapter = Chapter(1, 0, [TextFlowItem('body', 0, [
                    SourceTextFragment(1, index, (0, index * 10, 90, index * 10 + 10), [text])
                ]) for index, text in enumerate(texts)])
                (root / 'source/chapters/chapter_1.xml').write_text(tostring(encode(chapter), encoding='unicode'))
                events = []

                async def echo(_self, text, **_kwargs):
                    return text

                with patch.object(XMLTranslator, '_translate_text_async', echo), \
                     patch.object(XMLTranslator, '_request_and_submit_async', identity_fill), \
                     patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
                    await AsyncPDFCraft().translate_extraction(
                        extraction, root / 'output.pcex', translator(), target_language='fr',
                        with_anchored=False, on_translation_event=events.append)
                started, finished = events[0], events[-1]
                expected_count = sum(bool(text.strip()) for text in texts)
                expected_words = 2 if len(texts) > 1 else 0
                self.assertEqual(started.unit_counts[UnitKind.CONTENT], expected_count)
                self.assertEqual(started.total_words, expected_words)
                self.assertEqual(finished.kind, ProgressEventKind.COMPLETED)
                self.assertIsNone(finished.unit_id)
                self.assertEqual(finished.completed_words, expected_words)
                completed = [event for event in events if event.kind == ProgressEventKind.COMPLETED
                             and event.unit_kind == UnitKind.CONTENT]
                self.assertEqual(len(completed), expected_count)
                self.assertEqual(len({event.unit_id for event in completed}), expected_count)
                self.assertTrue(all(event.outcome in (UnitOutcome.PROCESSED, UnitOutcome.SKIPPED)
                                    for event in completed))
                with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
                    result = await AsyncPDFCraft().open_extraction(root / 'output.pcex')
                    rendered = root / 'output.md'
                    await AsyncPDFCraft().render_markdown(result, rendered)
                self.assertIn('◆', rendered.read_text())
