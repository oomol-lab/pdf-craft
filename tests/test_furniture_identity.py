"""Same geometry is not the identity of native PDF furniture (service #276)."""
# pylint: disable=protected-access,unused-argument,cell-var-from-loop
import asyncio
from copy import deepcopy
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pypdf import PdfWriter

from pdf_craft import AsyncPDFCraft, ExtractionOptions
from pdf_craft.common import read_xml, save_xml
from pdf_craft.document import ExtractionPaths
from pdf_craft.document.package import _validate_furnitures, _validate_translation
from pdf_craft.document.validation import FurnitureValidationError
from pdf_craft.extractor.chapter.chapter import Chapter, encode
from pdf_craft.extractor.recovery import recoverable_structure
from pdf_craft.pdf.furniture import extract_furnitures
from pdf_craft.pipeline.pdf.feed import read_record
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.pipeline.pdf.streaming import produce_furniture, source_furniture_coverage
from pdf_craft.transformer.furniture_plan import FurniturePlan
from pdf_craft.transformer.package import FurnitureExtractionTransformer
from pdf_craft.transformer.ready_queue import ReadyQueue
from pdf_craft.transformer.translation_coverage import FurnitureSectionCoverage, TranslationCoverage, read_coverage
from tests.extraction_helpers import make_extraction
from tests.test_toc_identity import RefinedEngine
from tests.test_policies import translator


BOX = "10,20,80,30"


def furniture(root, *, ids=True):
    source = make_extraction(root)
    save_xml(encode(Chapter(None, -1, [])), root / "chapters/chapter_head.xml")
    sections = ''
    for index, text in enumerate(('First', 'Second'), 1):
        identity = f'id="{index}" ' if ids else ''
        sections += f'<section {identity}det="{BOX}">{text}</section>'
    save_xml(fromstring(f'<furnitures><patterns/><pages><page index="1">{sections}'
                        '</page></pages></furnitures>'), root / 'furnitures.xml')
    return source


class PartialTranslator:
    def __init__(self, partial=False):
        self.partial = partial

    async def transform_position(self, position):
        return "T:" + position.content

    async def transform_sections(self, page_index, sections):
        return [None if self.partial and section.section_id == '2' else 'T:' + section.content
                for section in sections]


class FurnitureIdentityTests(TestCase):
    def test_title_reuse_and_ordinary_writeback_target_separate_same_box_nodes(self):
        root = fromstring(f'<furnitures><patterns/><pages><page index="1">'
                          f'<section id="1" det="{BOX}">First</section>'
                          f'<section id="2" det="{BOX}" toc_id="7">Second</section>'
                          '</page></pages></furnitures>')
        first, second = [deepcopy(s) for s in root.findall('pages/page/section')]
        first.text, second.text = 'Translated first', 'Translated title'
        rows = [(pickle.dumps(('sections', '1', [(first, None)],
                               [FurnitureSectionCoverage(1, BOX, 'translated', '1')])),),
                (pickle.dumps(('reuse', '1', second, 'translated')),)]
        result, _, states = FurniturePlan._apply(root, rows)
        self.assertEqual([s.text for s in result.findall('pages/page/section')],
                         ['Translated first', 'Translated title'])
        self.assertEqual([s.identity for s in states], [(1, '1'), (1, '2')])

    def test_native_float_boxes_round_to_same_geometry_without_losing_text(self):
        # Neutral text and geometry model the production characteristic, with no
        # customer PDF/text committed. Supply actual Poppler-shaped input to the
        # normal native reader and furniture generator, not prebuilt sections.
        lines = ''.join(
            f'<line xMin="{left}" yMin="20" xMax="40.65" yMax="30">'
            f'<word xMin="{left}" yMin="20" xMax="40.65" yMax="30">{text}</word></line>'
            for left, text in (("33.207824", "Alpha"), ("33.210000", "Beta"))
        )
        native = f'<doc><page width="100" height="100">{lines}</page></doc>'.encode()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'source.pdf'
            writer = PdfWriter()
            writer.add_blank_page(100, 100)
            writer.write(pdf)
            ocr = root / 'ocr'
            ocr.mkdir()
            # The empty PDF is only a path fixture; provide matching visible
            # Poppler text so this test isolates rounded geometry and identities.
            visible = (b'<pdf2xml><page number="1" width="100" height="100">'
                       b'<text left="33.20" top="20" width="7.45" height="10">Alpha Beta</text>'
                       b'</page></pdf2xml>')
            with patch('pdf_craft.pdf.furniture.run_subprocess_sync', return_value=(visible, b'')):
                result = extract_furnitures(pdf, ocr, native_pdf_text=native, native_pdf_text_prepared=True)
            path = root / 'furnitures.xml'
            save_xml(result, path)
            _validate_furnitures(path, {1: (417, 417)}, set())
            sections = read_xml(path).findall('pages/page/section')
            self.assertEqual([s.text for s in sections], ['Alpha', 'Beta'])
            self.assertEqual([s.get('id') for s in sections], ['1', '2'])
            self.assertEqual(len({s.get('det') for s in sections}), 1)

    def test_explicit_identity_roundtrip_and_invalid_or_duplicate_ids(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = furniture(root / 'source')
            archive = source._export(root / 'book.pcex')
            with archive._materialize() as paths:
                sections = read_xml(paths.furnitures).findall('pages/page/section')
                self.assertEqual([s.get('id') for s in sections], ['1', '2'])
                self.assertEqual([s.text for s in sections], ['First', 'Second'])
            for bad in ('1', '01', '0', '', BOX):
                sections = read_xml(root / 'source/furnitures.xml')
                sections.findall('pages/page/section')[1].set('id', bad)
                save_xml(sections, root / 'bad.xml')
                with self.subTest(id=bad), self.assertRaises(FurnitureValidationError):
                    _validate_furnitures(root / 'bad.xml', {1: (100, 100)}, set())

    def test_legacy_geometry_identity_remains_readable_but_ambiguous_input_fails(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            furniture(root / 'source', ids=False)
            path = root / 'source/furnitures.xml'
            with self.assertRaises(FurnitureValidationError):
                _validate_furnitures(path, {1: (100, 100)}, set())
            xml = read_xml(path)
            xml.findall('pages/page/section')[1].set('det', '10,40,80,50')
            save_xml(xml, path)
            _validate_furnitures(path, {1: (100, 100)}, set())
            (root / 'coverage.xml').write_text(
                f'<translation><furnitures><section page_index="1" det="{BOX}" '
                'state="translated"/></furnitures></translation>')
            _validate_translation(root / 'coverage.xml', path, set(), set())
            self.assertEqual(read_coverage(root / 'coverage.xml').sections, {(1, BOX): 'translated'})

    def test_coverage_rejects_unknown_identity_or_wrong_geometry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            furniture(root / 'source')
            for identifier, box in [('3', BOX), ('1', '1,1,2,2'), (BOX, BOX)]:
                (root / 'coverage.xml').write_text(
                    f'<translation><furnitures><section page_index="1" section_id="{identifier}" '
                    f'det="{box}" state="translated"/></furnitures></translation>')
                with self.subTest(id=identifier, box=box), self.assertRaises(ValueError):
                    _validate_translation(root / 'coverage.xml', root / 'source/furnitures.xml', set(), set())


class FurnitureIdentityAsyncTests(IsolatedAsyncioTestCase):
    async def test_standard_scheduler_exports_explicit_ids_in_translation_layer(self):
        async def translate(task, **kwargs):
            for child in task.element:
                if child.text:
                    child.text = 'T:' + child.text
            return task.element, task.payload

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = furniture(root / 'source')
            value = translator()
            with patch.object(value, 'translate_element', side_effect=translate):
                result = await AsyncPDFCraft().translate_extraction(
                    source, root / 'translated.pcex', value, with_furniture=True,
                    with_anchored=False, translation_id='test-layer', target_language='en')
            with result._materialize() as paths:
                layer = paths.translations / 'test-layer'
                self.assertEqual(read_coverage(layer / 'coverage.xml').sections,
                                 {(1, '1'): 'translated', (1, '2'): 'translated'})
                self.assertEqual([s.text for s in read_xml(layer / 'furnitures.xml').findall('pages/page/section')],
                                 ['T:First', 'T:Second'])
                self.assertEqual([s.text for s in read_xml(paths.furnitures).findall('pages/page/section')],
                                 ['First', 'Second'])

    async def test_batch_translation_and_coverage_survive_export_with_same_box(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = furniture(root / 'source')
            result = await FurnitureExtractionTransformer(PartialTranslator(partial=True)).transform(
                source, root / 'target.pcex')
            with result._materialize() as paths:
                self.assertEqual([s.text for s in read_xml(paths.furnitures).findall('pages/page/section')],
                                 ['T:First', 'Second'])
                coverage = read_coverage(paths.translation)
                self.assertEqual(coverage.sections, {(1, '1'): 'translated', (1, '2'): 'preserved'})
                replacements = list(PDFTranslationPipeline()._iter_covered_replacements(
                    [], paths.furnitures, coverage, {1: (100, 100)}, 300))
                self.assertEqual([r.text for r in replacements], ['T:First'])

    async def test_streaming_snapshots_and_pdf_slots_join_by_id_not_geometry(self):
        for partial in (False, True):
            with self.subTest(partial=partial), TemporaryDirectory() as directory:
                root = Path(directory)
                furniture(root / 'source')
                output = root / 'out'
                output.mkdir()
                paths = ExtractionPaths.at(root / 'source')
                queue = ReadyQueue(root / 'queue.sqlite')
                plan = FurniturePlan(paths, output, queue)
                while job := queue.take(['furniture']):
                    identifier, _, payload, _ = job
                    queue.finish(identifier, await plan.execute(payload, PartialTranslator(partial)))
                result = plan.page_snapshot(1)
                assert result is not None
                snapshot, _, states = result
                self.assertEqual([s.text for s in snapshot.findall('pages/page/section')],
                                 ['T:First', 'Second' if partial else 'T:Second'])
                self.assertEqual([state.identity for state in states], [(1, '1'), (1, '2')])
                coverage = source_furniture_coverage(paths.furnitures, TranslationCoverage({}, {}, {}, {}))
                prototypes = list(PDFTranslationPipeline()._iter_covered_replacements(
                    [], paths.furnitures, coverage, {1: (100, 100)}, 300))
                self.assertEqual(len(prototypes), 2)
                runner = asyncio.get_running_loop().create_future()
                runner.set_result(None)
                schedule = SimpleNamespace(runner=runner, furniture_changed=asyncio.Event(), furniture=plan)
                for folder in ('feed', 'previews'):
                    (root / folder).mkdir()
                await produce_furniture(schedule, {1: list(enumerate(prototypes))}, root / 'feed',
                                        root / 'previews', {1: (100, 100)}, 300, root, {})
                records = [read_record(root / 'feed' / str(index)) for index in range(2)]
                self.assertEqual([r.text if r else None for r in records],
                                 ['T:First', None if partial else 'T:Second'])
                plan.finish()
                _validate_translation(output / 'coverage.xml', output / 'furnitures.xml', set(), set())
                self.assertEqual(read_coverage(output / 'coverage.xml').sections,
                                 {(1, '1'): 'translated', (1, '2'): 'preserved' if partial else 'translated'})

    async def test_furniture_failure_never_discards_adopted_body(self):
        for invalid_output in (False, True):
            with self.subTest(invalid_output=invalid_output), TemporaryDirectory() as directory:
                root = Path(directory)
                seen = []

                def fail(_pdf, _pages, destination, **_kwargs):
                    destination.write_text('<furnitures><patterns/><pages/></furnitures>')
                    if invalid_output:
                        destination.write_text('<furnitures><invalid/></furnitures>')
                    else:
                        raise ValueError('native furniture failed')

                def allow(failure):
                    seen.append(failure)
                    return True

                with patch('pdf_craft.transform.write_furnitures', fail), \
                     patch('pdf_craft.extractor.recovery.recover_chapters', side_effect=AssertionError('body recovery')):
                    await AsyncPDFCraft.from_engine(RefinedEngine('body')).extract_pdf(
                        root / 'source.pdf', root / 'book.pcex', ExtractionOptions(footnotes=False),
                        analysing_path=root / 'analysis', error_policy=allow)
                self.assertEqual([(f.stage, f.fallback) for f in seen], [('furniture', 'omit_furniture')])
                self.assertFalse((root / 'analysis/extraction/furnitures.xml').exists())
                text = ''.join(p.read_text() for p in (root / 'analysis/extraction/chapters').glob('*.xml'))
                self.assertIn('Page 6 body.', text)
                self.assertNotIn('standalone-asset', text)
                self.assertFalse(recoverable_structure(FurnitureValidationError('invalid furniture')))

    async def test_strict_furniture_failure_and_storage_error_propagate_without_body_recovery(self):
        for error in (ValueError('bad furniture'), OSError('disk failed')):
            with self.subTest(error=error), TemporaryDirectory() as directory:
                root = Path(directory)
                with patch('pdf_craft.transform.write_furnitures', side_effect=error), \
                     patch('pdf_craft.extractor.recovery.recover_chapters', side_effect=AssertionError('body recovery')):
                    with self.assertRaises(type(error)) as caught:
                        await AsyncPDFCraft.from_engine(RefinedEngine('body')).extract_pdf(
                            root / 'source.pdf', root / 'book.pcex', ExtractionOptions(footnotes=False),
                            analysing_path=root / 'analysis', error_policy=False)
                self.assertIs(caught.exception, error)
