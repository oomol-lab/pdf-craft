"""Reading order is decided before joining; recovery isolates failed pages."""
# pylint: disable=protected-access,cell-var-from-loop,unused-argument
from collections import Counter
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, parse, tostring

from pdf_craft._policy import policy_operation
from pdf_craft.common import save_xml
from pdf_craft.document import ExtractionPaths
from pdf_craft.document.content import chapter_paths
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment, StandaloneAsset, decode as decode_chapter
from pdf_craft.extractor.chapter.jointer import Jointer
from pdf_craft.extractor.chapter.primary import PrimaryPages, project_window
from pdf_craft.extractor.chapter.page_validation import validate_page
from pdf_craft.extractor.chapter.source import capture_sources
from pdf_craft.extractor.recovery import recover_chapters
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.pdf import decode, encode
from tests.test_anchored_page_recovery import RecoveryOCR


def page(index):
    return decode(fromstring(f'''<page index="{index}"><body>
      <layout ref="text" det="10,10,190,40">Body {index}.</layout>
    </body><footnotes>
      <layout ref="text" det="10,150,190,180">Note {index}.</layout>
    </footnotes></page>'''))


class ReadingOrderTests(TestCase):
    def test_joiner_consumes_the_page_projection_order_without_changing_source_identity(self):
        raw = decode(fromstring('''<page index="1"><body>
          <layout ref="text" det="0,0,200,20">Body.</layout>
        </body><footnotes>
          <layout ref="text" det="0,60,200,80">Last note.</layout>
          <layout ref="text" det="0,30,200,50">First note.</layout>
        </footnotes></page>'''))
        original = deepcopy(raw)
        sources = capture_sources([raw])
        items = list(Jointer([(1, raw.footnotes_layouts)], sources).execute())
        fragments = [child for item in items if isinstance(item, TextFlowItem)
                     for child in item.children if isinstance(child, SourceTextFragment)]
        self.assertEqual([child.source_order for child in fragments], [2, 1])
        self.assertEqual([child.content for child in fragments], [['First note.'], ['Last note.']])
        self.assertEqual(raw, original)
        validate_page(PrimaryPages().append(raw))
        validate_page(project_window([raw])[0])

    def test_narrow_numeric_field_does_not_get_overtaken_by_a_wider_field(self):
        # Cropped/desensitized geometry of service #392 page 30: the earlier
        # narrow value must still count as a member of the later value's column.
        raw = decode(fromstring('''<page index="30"><body>
          <layout ref="text" det="1362,2930,2000,2979">Field A.</layout>
          <layout ref="text" det="1362,2975,2000,3025">Field B.</layout>
        </body><footnotes>
          <layout ref="text" det="2025,2937,2190,2979">Alpha.</layout>
          <layout ref="text" det="2025,3021,2062,3060">Beta.</layout>
          <layout ref="text" det="2025,3091,2122,3134">Gamma.</layout>
        </footnotes></page>'''))
        sources = capture_sources([raw])
        positions = [sources[(30, id(item))][1].reading_order for item in raw.footnotes_layouts]
        self.assertEqual(positions, sorted(positions))
        validate_page(PrimaryPages().append(raw))

    def test_column_order_is_not_replaced_by_global_vertical_sort(self):
        raw = decode(fromstring('''<page index="1"><body>
          <layout ref="text" det="0,0,90,20">Left top.</layout>
          <layout ref="text" det="0,60,90,80">Left bottom.</layout>
          <layout ref="text" det="110,0,200,20">Right top.</layout>
          <layout ref="text" det="110,60,200,80">Right bottom.</layout>
        </body></page>'''))
        projected = PrimaryPages().append(raw)
        self.assertEqual([layout.source_order for layout in projected.layouts], [0, 1, 2, 3])
        validate_page(projected)


class LocalRecoveryTests(IsolatedAsyncioTestCase):
    async def test_located_failures_preserve_other_pages_and_do_not_replay_callbacks(self):
        for failed, adopted in (({2}, True), ({1, 3, 6}, True), ({2, 5}, True), ({2, 5}, False)):
            with self.subTest(failed=failed, adopted=adopted), TemporaryDirectory() as folder:
                root = Path(folder)
                pages = root / 'ocr'
                pages.mkdir()
                database = root / 'adopted.sqlite'
                callbacks, analyses = [], []
                assembly = PageAssembly(database, on_body=callbacks.append)
                primary = PrimaryPages()
                for index in range(1, 7):
                    raw = page(index)
                    save_xml(encode(raw), pages / f'page_{index}.xml')
                    candidate = primary.append(raw)
                    if adopted and candidate.citations:
                        candidate.citations[0].continues_previous = index > 1
                    analyses.append(deepcopy(candidate))
                    assembly.append(candidate)
                assembly.finish()
                notifications = [tostring(value) for value in callbacks]
                events, attempts = [], Counter()
                original = PageAssembly._append

                def append(current, candidate):
                    attempts[candidate.page_index] += 1
                    original(current, candidate)
                    if candidate.page_index in failed:
                        # Raise within the real page transaction after writes:
                        # recovery must not leak any part of this failed page.
                        raise ValueError('injected page-local structure failure')

                draft = SimpleNamespace(pdf_path=root / 'source.pdf', pages_path=pages,
                                        extraction_paths=ExtractionPaths.at(root / 'extraction'),
                                        toc=TocInfo([], []), dpi=300, includes_footnotes=True)
                # A located initial failure starts recovery without sacrificing
                # any healthy neighbour. The rest exercise a failed rebuild.
                from pdf_craft.extractor.chapter.analysis_types import PageStructureError
                initial = min(failed)
                @policy_operation
                async def located(*, error_policy):
                    return await recover_chapters(draft, RecoveryOCR(),
                        PageStructureError('initial page failure', initial), root / 'recovered',
                        adopted_database=database if adopted else None,
                        analyses=analyses, aborted=lambda: False)

                def reject_raw_reanalysis(*_args):
                    raise AssertionError('do not reanalyse adopted pages')
                from contextlib import nullcontext
                guard = (patch.object(PrimaryPages, 'append', side_effect=reject_raw_reanalysis)
                         if adopted else nullcontext())
                with patch.object(PageAssembly, '_append', append), guard:
                    result = await located(error_policy=lambda event: events.append(event) or True)
                self.assertEqual([tostring(value) for value in callbacks], notifications)
                affected = [p for event in events for location in event.affected for p in location.page_indexes]
                self.assertEqual(Counter(affected), Counter(failed))
                self.assertTrue(all(value == 1 for value in attempts.values()))
                self.assertEqual(set(attempts), set(range(1, 7)) - {initial})
                chapters = [decode_chapter(parse(p).getroot()) for p in chapter_paths(result.chapters)]
                items = [item for chapter in chapters for item in chapter.flow_items]
                self.assertEqual({item.asset.page_index for item in items if isinstance(item, StandaloneAsset)}, failed)
                texts = [part for item in items if isinstance(item, TextFlowItem)
                         for fragment in item.children if isinstance(fragment, SourceTextFragment)
                         for part in fragment.content if isinstance(part, str)]
                expected = [text for index in range(1, 7) if index not in failed
                            for text in (f'Body {index}.', f'Note {index}.')]
                self.assertEqual(Counter(texts), Counter(expected))
                # No paragraph crosses an isolated failed page, even when its
                # note declares an incoming connection without a cursor.
                for item in items:
                    if isinstance(item, TextFlowItem):
                        indexes = [child.page_index for child in item.children]
                        self.assertFalse(any(min(indexes) < p < max(indexes) for p in failed))
