"""Asset association must not erase editable OCR sources or review evidence."""
from copy import deepcopy
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
import json
from xml.etree.ElementTree import tostring

from pdf_craft.extractor.chapter.chapter import SourceAsset, TextFlowItem
from pdf_craft.extractor.chapter.jointer import _is_table_title_text, _is_table_caption_text
from pdf_craft.extractor.chapter.page_analysis import restore_streams, LayoutAnnotations, AnalysedCitation
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.page_repair import RepairedLayout, RepairedPage, _compile_repaired_page, build_page_repair_messages
from pdf_craft.extractor.chapter.page_review import build_jev_review_states
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.pdf import Page, PageLayout


def page(*items):
    return Page(1, None, [PageLayout(kind, box, text, i, None)
                         for i, (kind, box, text) in enumerate(items)], [], 0, 0)


class AssetSourceIntegrityTests(unittest.TestCase):
    def test_preceding_caption_is_not_dropped_in_either_mode(self):
        raw = page(('table_caption', (10, 10, 90, 20), 'Table 1. Test'),
                   ('table', (10, 30, 90, 90), '<table><tr><td>5</td></tr></table>'))
        for enabled in (False, True):
            with self.subTest(footnotes=enabled):
                analysis, = project_window((raw,), enabled)
                assert analysis is not None
                self.assertIn(0, [x.source_order for x in analysis.layouts])
                state = next(iter(build_jev_review_states([raw], [analysis], {1: (100, 100)},
                                 includes_footnotes=enabled)))[1]
                self.assertIn('Table 1. Test', str(state))

    def test_associated_text_preserves_its_identity_and_original_geometry(self):
        raw = page(('text', (10, 10, 90, 20), 'Table 1. Results'),
                   ('table', (10, 25, 90, 75), '<table><tr><td>5</td></tr></table>'),
                   ('text', (10, 80, 90, 90), 'Notes: an explanation.'))
        for enabled in (False, True):
            with self.subTest(footnotes=enabled):
                analysis, = project_window((raw,), enabled)
                assert analysis is not None
                sources = {x.source_order: x for x in analysis.layouts if x.source_order is not None}
                self.assertEqual(set(sources), {0, 2})
                self.assertEqual(sources[0].bbox, (10, 10, 90, 20))
                self.assertEqual(sources[2].bbox, (10, 80, 90, 90))
                restored = restore_streams([analysis])
                assets = [x for x in restored.paragraphs if isinstance(x, SourceAsset)]
                self.assertEqual(len(assets), 1)
                self.assertEqual(assets[0].title, ['Table 1. Results'])
                self.assertEqual(assets[0].caption, ['Notes: an explanation.'])
                self.assertFalse(any(isinstance(x, TextFlowItem) for x in restored.paragraphs))

    def test_table_intrinsic_title_and_caption_are_visible_to_review(self):
        raw = page(('table', (10, 10, 90, 90),
                    'Table 1. Results\n<table><tr><td>5</td></tr></table>\nNotes: intrinsic.'))
        analysis, = project_window((raw,), False)
        assert analysis is not None
        state = next(iter(build_jev_review_states([raw], [analysis], {1: (100, 100)},
                         includes_footnotes=False)))[1]
        text = state['target_page']['layouts'][0]['raw_text']
        self.assertIn('Table 1. Results', text)
        self.assertIn('Notes: intrinsic.', text)

    def test_multiline_paragraphs_are_not_table_labels(self):
        for separator in ('\n\n', '\r\n\r\n', '\n \t\n'):
            self.assertFalse(_is_table_title_text('A separate paragraph.' + separator + 'Important:'))
            self.assertFalse(_is_table_caption_text('Notes: a paragraph.' + separator + 'Another paragraph.'))

    def test_detaching_or_ignoring_a_member_cannot_reappear_in_asset_output(self):
        raw = page(('table', (10, 10, 90, 70), '<table><tr><td>5</td></tr></table>'),
                   ('text', (10, 75, 90, 85), 'Notes: independent prose.'))
        original, = project_window((raw,), False)
        assert original is not None
        snapshot = deepcopy(original)
        for ignored in (False, True):
            candidate = deepcopy(original)
            member = next(x for x in candidate.layouts if x.source_order == 1)
            member.annotations.keep_asset_attachment = False
            if ignored:
                candidate.layouts[candidate.layouts.index(member)] = replace(
                    member, annotations=LayoutAnnotations('ignored', False, False))
            result = restore_streams([candidate])
            asset = next(x for x in result.paragraphs if isinstance(x, SourceAsset))
            self.assertEqual(asset.caption, [])
            paragraphs = [x for x in result.paragraphs if isinstance(x, TextFlowItem)]
            self.assertEqual(len(paragraphs), 0 if ignored else 1)
            self.assertEqual(original, snapshot)

    def test_repair_can_detach_a_provisional_link_and_keep_every_source(self):
        raw = page(('table', (10, 10, 90, 70), '<table><tr><td>5</td></tr></table>'),
                   ('text', (10, 75, 90, 85), 'Notes: independent prose.'))
        original, = project_window((raw,), False)
        assert original is not None
        proposed = [RepairedLayout(layout_id=f'layout-{x.document_order}',
                    ownership=x.ownership, continues_from_previous=False,
                    paragraph_role='body' if x.source_order is not None else None,
                    paragraph_level=-1 if x.source_order is not None else None,
                    citation_id=None, keep_asset_attachment=False if x.source_order == 1 else None)
                    for x in original.layouts]
        repaired = _compile_repaired_page(original, RepairedPage(
            page_index=1, layouts=proposed, citations=[], references=[]))
        self.assertEqual(len(repaired.layouts), len(original.layouts))
        restored = restore_streams([repaired])
        self.assertEqual(next(x for x in restored.paragraphs if isinstance(x, SourceAsset)).caption, [])
        self.assertTrue(any(isinstance(x, TextFlowItem) for x in restored.paragraphs))

    def test_stream_assembly_emits_adopted_caption_once_and_keeps_page_provenance(self):
        raw = page(('text', (10, 10, 90, 20), 'Table 1. Results'),
                   ('table', (10, 25, 90, 75), '<table><tr><td>5</td></tr></table>'),
                   ('text', (10, 80, 90, 90), 'Notes: explanation.'))
        analysis, = project_window((raw,), False)
        assert analysis is not None
        snapshot = deepcopy(analysis)
        events = []
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory) / 'assembly.sqlite', on_body=events.append)
            assembly.append(analysis)
            assembly.finish()
        text = ''.join(tostring(x, encoding='unicode')
                       for x in events)
        self.assertEqual(text.count('Table 1. Results'), 1)
        self.assertEqual(text.count('Notes: explanation.'), 1)
        self.assertEqual(analysis, snapshot)

    def test_text_reassigned_to_a_note_is_not_copied_back_to_the_table(self):
        raw = page(('table', (10, 10, 90, 70), '<table><tr><td>5</td></tr></table>'),
                   ('text', (10, 75, 90, 85), 'Notes: a genuine footnote.'))
        analysis, = project_window((raw,), True)
        assert analysis is not None
        member = next(x for x in analysis.layouts if x.source_order == 1)
        member.annotations.ownership = 'citation'
        member.annotations.citation_id = 'new-1-1'
        member.annotations.citation_index = 1
        analysis.citations.append(AnalysedCitation('new-1-1', 1, 1, '①', 0, 1))
        restored = restore_streams([analysis])
        self.assertEqual(next(x for x in restored.paragraphs if isinstance(x, SourceAsset)).caption, [])
        self.assertEqual(len(restored.citations), 1)
        self.assertIn('genuine footnote', str(restored.citations[0].flow_items))

    def test_legacy_noop_repair_preserves_the_link_when_optional_field_is_omitted(self):
        raw = page(('text', (10, 10, 90, 20), 'Table 1. Results'),
                   ('table', (10, 25, 90, 75), '<table><tr><td>5</td></tr></table>'),
                   ('text', (10, 80, 90, 90), 'Notes: explanation.'))
        original, = project_window((raw,), False)
        assert original is not None
        proposed = [RepairedLayout(layout_id=f'layout-{x.document_order}', ownership=x.ownership,
                    continues_from_previous=x.continues_from_previous,
                    paragraph_role='body' if x.source_order is not None else None,
                    paragraph_level=-1 if x.source_order is not None else None, citation_id=None)
                    for x in original.layouts]
        repaired = _compile_repaired_page(original, RepairedPage(
            page_index=1, layouts=proposed, citations=[], references=[]))
        asset = next(x for x in restore_streams([repaired]).paragraphs if isinstance(x, SourceAsset))
        self.assertEqual(asset.title, ['Table 1. Results'])
        self.assertEqual(asset.caption, ['Notes: explanation.'])

    def test_repair_schema_offers_attachment_control_only_for_associated_sources(self):
        for linked in (False, True):
            raw = page(('table', (10, 10, 90, 70), '<table><tr><td>5</td></tr></table>'),
                       ('text', (10, 75, 90, 85), 'Notes: explanation.' if linked else 'Ordinary prose.'))
            analysis, = project_window((raw,), False)
            assert analysis is not None
            messages = build_page_repair_messages(previous_page=None, target_page=analysis,
                next_page=None, previous_pass_probability=None, next_pass_probability=None,
                includes_footnotes=False)
            data = json.loads(messages[1].message)
            properties = data['output_contract']['$defs']['RepairedLayout']['properties']
            self.assertEqual('keep_asset_attachment' in properties, linked)
            self.assertEqual('asset_text_note' in data['target_page'], linked)
