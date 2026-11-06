"""Failures distinguish semantic slices while retaining their physical source."""
# pylint: disable=protected-access
from unittest import TestCase
from typing import cast
from xml.etree.ElementTree import fromstring

from pdf_craft import FailureLocation, PageGeometry
from pdf_craft._failure_sources import (
    analysis_location, chapter_locations, element_locations, page_sources,
    segment_locations, tag_locations,
)
from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, encode
from pdf_craft.extractor.chapter.analysis_types import _AnalysedTextSource
from pdf_craft.extractor.chapter.page_analysis import restore_streams
from pdf_craft.pipeline.pdf.models import PDFReplacement, PDFReplacementRegion
from pdf_craft.pipeline.pdf.patcher import _check_ignore_error
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from tests.test_source_range_repair import page, box, split_notes, compiled


def shared_notes():
    source = page(box('Body.'), box('6 First note.\n7 Second note.'))
    data, _ = split_notes(source)
    candidate = compiled(source, data)
    streams = restore_streams([candidate])
    return candidate, Chapter(None, -1, [cast(TextFlowItem, item) for item in streams.paragraphs], streams.citations)


class FragmentFailureSourceTests(TestCase):
    def test_shared_notes_keep_distinct_failure_identities_through_json(self):
        _, chapter = shared_notes()
        with page_sources({1: PageGeometry(1, 200, 200, 72)}):
            locations = chapter_locations(chapter)
        first, second = locations[1], locations[2]
        self.assertNotEqual(first.unit_id, second.unit_id)
        self.assertEqual(first.unit_id, 'footnote:1:1')
        self.assertEqual(first.regions[0].source_order_in_page, second.regions[0].source_order_in_page)
        self.assertEqual(first.regions[0].bbox, second.regions[0].bbox)
        self.assertNotEqual(first.regions[0].fragment_id, second.regions[0].fragment_id)
        root = encode(chapter)
        tag_locations(root, locations)
        self.assertEqual(element_locations(root), locations)

    def test_actual_segments_select_only_the_failing_note(self):
        _, chapter = shared_notes()
        with page_sources({1: PageGeometry(1, 200, 200, 72)}):
            locations = chapter_locations(chapter)
        root = encode(chapter)
        tag_locations(root, locations)
        selected = []
        for segment in search_inline_segments(search_text_segments(root)):
            if 'Second note.' in ''.join(part.text for part in segment):
                selected.append(segment)
        self.assertTrue(selected)
        self.assertEqual(segment_locations(selected), (locations[2],))

    def test_analysis_failure_regions_retain_both_shared_source_slices(self):
        candidate, _ = shared_notes()
        with page_sources({1: PageGeometry(1, 200, 200, 72)}):
            location = analysis_location(candidate)
        first, second = location.regions[1:]
        self.assertEqual(first.source_order_in_page, second.source_order_in_page)
        self.assertNotEqual(first.fragment_id, second.fragment_id)

    def test_generic_xml_fragment_tags_do_not_require_pcex_identities(self):
        root = fromstring('<chapter><text><fragment>Generic text.</fragment></text></chapter>')
        for location in (FailureLocation('document', unit_id='document'),
                         FailureLocation('fragment', member_path='chapter.xhtml', channel='body')):
            with self.subTest(scope=location.scope):
                tag_locations(root, (location,))
                segments = list(search_inline_segments(search_text_segments(root)))
                self.assertEqual(segment_locations(segments), (location,))

    def test_pdf_policy_receives_the_failed_slice_identity(self):
        candidate, _ = shared_notes()
        identities = [layout.source.fragment_id for layout in candidate.layouts[1:]
                      if isinstance(layout.source, _AnalysedTextSource)]
        seen = []
        for identity in identities:
            region = PDFReplacementRegion(1, (10, 10, 190, 150), (200, 200), 72,
                                          reading_order=1, fragment_id=identity)
            replacement = PDFReplacement(1, region.bbox, 'Translated note.', (200, 200),
                                         regions=(region,))
            self.assertTrue(_check_ignore_error(lambda context: seen.append(context) or True,
                                               ValueError('Cannot fill'), replacement=replacement))
        first, second = (context.targets[0] for context in seen)
        self.assertEqual(first.unit_id, f'paragraph:1:{identities[0]}')
        self.assertEqual(second.unit_id, f'paragraph:1:{identities[1]}')
        self.assertNotEqual(first.unit_id, second.unit_id)
        self.assertEqual([first.regions[0].fragment_id, second.regions[0].fragment_id], identities)
        self.assertEqual(first.regions[0].source_order_in_page, 1)

    def test_unsliced_pdf_replacement_keeps_its_existing_unit_id(self):
        seen = []
        replacement = PDFReplacement(1, (0, 0, 100, 100), 'Text.', (200, 200), reading_order=3)
        _check_ignore_error(lambda context: seen.append(context) or True,
                            ValueError('Cannot fill'), replacement=replacement)
        self.assertEqual(seen[0].targets[0].unit_id, 'paragraph:1:3')
