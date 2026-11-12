"""Final asset ownership decisions must be executable, lossless and source-local."""
from tests.repair_plan_fixtures import expanded_packet
# pylint: disable=protected-access
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from typing import Any
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceAsset, StandaloneAsset, TextFlowItem, encode, decode,
)
from pdf_craft.extractor.chapter.page_repair import (
    PageCandidate, LayoutCandidate, build_page_repair_messages, _compile_repaired_page,
)
from pdf_craft.extractor.chapter.page_analysis import restore_streams
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.chapter.asset_editing import editable_asset_sources
from pdf_craft.extractor.chapter.analysis_types import AssetAttachment
from pdf_craft.markdown.render.layouts import render_layouts
from pdf_craft.pdf import Page, PageLayout


def scene(*items):
    raw = Page(1, None, [PageLayout(kind, bbox, text, i, 'asset' if kind == 'image' else None)
                        for i, (kind, bbox, text) in enumerate(items)], [], 0, 0)
    source, = project_window((raw,))
    assert source is not None
    return source


def candidate(source) -> dict[str, Any]:
    messages = build_page_repair_messages(previous_page=None, target_page=source, next_page=None,
                                         previous_pass_probability=None, next_pass_probability=None)
    packet = expanded_packet(json.loads(messages[1].message)['target_page'])
    fields = set(LayoutCandidate.model_fields)
    entries = []
    for item in packet['layouts']:
        data = {key: value for key, value in {**item, **item['initial_proposal']}.items() if key in fields}
        data.update(ownership_evidence='Fixture ownership.', paragraph_evidence='Fixture paragraph.',
                    continues_from_previous=False, continues_to_next=None)
        entries.append(data)
    return dict(page_index=source.page_index, layouts=entries, citations=packet['citations'],
                references=packet['references'])


def compile_page(source, data):
    return _compile_repaired_page(source, PageCandidate.model_validate(data))


def roundtrip(page):
    restored = restore_streams([page])
    chapter = Chapter(None, -1, [StandaloneAsset(item) if isinstance(item, SourceAsset) else item
                               for item in restored.paragraphs], restored.citations)
    return decode(fromstring(tostring(encode(chapter))))


def assets(chapter):
    return [item.asset for item in chapter.flow_items if isinstance(item, StandaloneAsset)] + [
        child for item in chapter.flow_items if isinstance(item, TextFlowItem)
        for child in item.children if isinstance(child, SourceAsset)]


class AssetEditingTests(unittest.TestCase):
    def test_new_link_keeps_image_and_skips_caption_in_main_continuation(self):
        # Reduced service #373/page77: the caption was ordinary OCR text.
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('text', (0, 31, 100, 45), 'Note: chart explanation.'),
                       ('text', (0, 46, 100, 60), 'would have increased sharply.'),
                       ('text', (0, 65, 100, 80), 'Under this system.'))
        before = deepcopy(source)
        data = candidate(source)
        image, caption, body = (data['layouts'][i] for i in range(3))
        caption['asset_attachment'] = dict(asset_id=image['layout_id'], slot='caption')
        body['continues_from_previous'] = True
        compiled = compile_page(source, data)
        chapter = roundtrip(compiled)
        self.assertEqual(assets(chapter)[0].caption, ['Note: chart explanation.'])
        self.assertEqual(sum(isinstance(x, TextFlowItem) for x in chapter.flow_items), 2)
        self.assertEqual(source, before)
        self.assertTrue(next(x for x in compiled.layouts if x.source_order == 2).continues_from_previous)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'asset.png').write_bytes(b'fixture-image')
            out = root / 'output'
            out.mkdir()
            rendered = ''.join(render_layouts(chapter.flow_items, root, out, Path('assets'), 0))
        self.assertEqual(rendered.count('Note: chart explanation.'), 1)
        self.assertEqual(rendered.count('would have increased sharply.'), 1)
        self.assertIn('![', rendered)

    def test_redirect_change_slot_detach_and_same_bbox_distinct_asset_ids(self):
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('image', (0, 0, 100, 30), ''),
                       ('text', (0, 40, 100, 50), 'A label.'))
        data = candidate(source)
        first, second, label = (data['layouts'][i] for i in range(3))
        label['asset_attachment'] = dict(asset_id=second['layout_id'], slot='title')
        result = roundtrip(compile_page(source, data))
        self.assertEqual([x.title for x in assets(result)], [[], ['A label.']])
        # Re-editing uses the same source-complete page, never materialized copies.
        adopted = compile_page(source, data)
        changed = candidate(adopted)
        changed['layouts'][2]['asset_attachment'] = dict(asset_id=first['layout_id'], slot='caption')
        result = roundtrip(compile_page(adopted, changed))
        self.assertEqual([x.caption for x in assets(result)], [['A label.'], []])
        changed['layouts'][2]['asset_attachment'] = None
        result = roundtrip(compile_page(adopted, changed))
        self.assertTrue(all(not x.caption and not x.title for x in assets(result)))
        self.assertIn('A label.', str(result.flow_items))

    def test_mixed_source_and_multiple_caption_sources_preserve_order_and_bbox(self):
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('text', (0, 35, 100, 55), 'Caption. Body.'),
                       ('text', (0, 60, 100, 75), 'Second caption paragraph.'))
        data = candidate(source)
        image, mixed, tail = (data['layouts'][i] for i in range(3))
        cut = dict(left='Caption. ', right='Body.')
        caption = dict(mixed, source_range=dict(start='source_start', end=cut),
                       asset_attachment=dict(asset_id=image['layout_id'], slot='caption'))
        body = dict(mixed, source_range=dict(start=cut, end='source_end'), asset_attachment=None)
        tail['asset_attachment'] = dict(asset_id=image['layout_id'], slot='caption')
        data['layouts'] = [image, caption, body, tail]
        adopted = compile_page(source, data)
        self.assertEqual([x.bbox for x in adopted.layouts if x.source_order == 1],
                         [(0, 35, 100, 55)] * 2)
        chapter = roundtrip(adopted)
        self.assertEqual(assets(chapter)[0].caption, ['Caption. \n\nSecond caption paragraph.'])
        self.assertEqual(str(chapter).count('Body.'), 1)
        # The same destination can also join pieces of one printed paragraph.
        tail['continues_from_previous'] = True
        joined = roundtrip(compile_page(source, data))
        self.assertEqual(assets(joined)[0].caption, ['Caption.  Second caption paragraph.'])

    def test_intrinsic_fields_can_become_body_or_footnote_without_duplicating_asset(self):
        source = scene(('image', (0, 0, 100, 30), 'Intrinsic explanation.'))
        # Explicitly supply actual intrinsic slots; image.content stays private.
        image = source.layouts[0]
        source.layouts[0] = replace(image, source=replace(image.source,
            title=('Mistaken title.',), caption=('Unnumbered note.',), content=('PRIVATE',)))
        projected = editable_asset_sources(source)
        self.assertEqual(editable_asset_sources(projected), projected)
        data = candidate(source)
        self.assertEqual(len(data['layouts']), 3)
        title = next(x for x in data['layouts'] if x['layout_id'].endswith('/title'))
        caption = next(x for x in data['layouts'] if x['layout_id'].endswith('/caption'))
        title['asset_attachment'] = None
        caption.update(asset_attachment=None, ownership='citation', citation_id='note', citation_index=1)
        data['citations'] = [dict(citation_id='note', stream_order=0, index=1, mark='*')]
        chapter = roundtrip(compile_page(source, data))
        self.assertEqual(assets(chapter)[0].title, [])
        self.assertEqual(assets(chapter)[0].caption, [])
        self.assertEqual(assets(chapter)[0].content, ['PRIVATE'])
        self.assertIn('Mistaken title.', str(chapter.flow_items))
        self.assertIn('Unnumbered note.', str(chapter.definitions))
        self.assertNotIn('PRIVATE', str(data))

    def test_independent_description_does_not_consume_main_body_cursor(self):
        source = scene(('text', (0, 0, 100, 15), 'The sentence'),
                       ('text', (0, 20, 100, 35), 'Independent description.'),
                       ('text', (0, 40, 100, 55), 'continues here.'))
        data = candidate(source)
        data['layouts'][1]['independent_stream'] = 'description'
        data['layouts'][2]['continues_from_previous'] = True
        chapter = roundtrip(compile_page(source, data))
        paragraphs = [x for x in chapter.flow_items if isinstance(x, TextFlowItem)]
        self.assertEqual(len(paragraphs), 2)
        self.assertIn('continues here.', str(paragraphs[0]))
        self.assertNotIn('Independent description.', str(paragraphs[0]))
        self.assertIn('Independent description.', str(paragraphs[1]))

    def test_invalid_submitted_destinations_and_local_boundaries_are_rejected(self):
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('text', (0, 40, 100, 55), 'A label.'))
        for case in ('missing', 'ignored-asset', 'nonasset', 'image-content',
                     'wrong-stream', 'independent-and-attached', 'incoming', 'outgoing'):
            with self.subTest(case=case):
                data = candidate(source)
                image, label = (data['layouts'][i] for i in range(2))
                label['asset_attachment'] = dict(asset_id=image['layout_id'], slot='caption')
                if case == 'missing': label['asset_attachment']['asset_id'] = 'missing'
                if case == 'nonasset': label['asset_attachment']['asset_id'] = label['layout_id']
                if case == 'ignored-asset': image.update(ownership='ignored')
                if case == 'image-content': label['asset_attachment']['slot'] = 'content'
                if case == 'wrong-stream':
                    label.update(ownership='citation', citation_id='note', citation_index=1)
                    data['citations'] = [dict(citation_id='note', stream_order=0, index=1, mark='*')]
                if case == 'independent-and-attached': label['independent_stream'] = 'caption'
                if case == 'incoming': label['continues_from_previous'] = True
                if case == 'outgoing': label['continues_to_next'] = True
                with self.assertRaises(ValueError):
                    compile_page(source, data)

    def test_validation_is_final_page_only_and_cannot_silently_detach_bad_target(self):
        from pdf_craft.extractor.chapter.page_validation import validate_page
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('text', (0, 40, 100, 55), 'A label.'))
        data = candidate(source)
        data['layouts'][1]['asset_attachment'] = dict(asset_id=data['layouts'][0]['layout_id'], slot='caption')
        adopted = compile_page(source, data)
        adopted.layouts[1].annotations.asset_attachment = AssetAttachment('unknown', 'caption')
        with self.assertRaisesRegex(ValueError, 'ASSET_TEXT'):
            validate_page(adopted)

    def test_fixed_final_attachment_does_not_depend_on_initial_ownership(self):
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('text', (0, 40, 100, 55), 'A label.'))
        data = candidate(source)
        data['layouts'][1]['asset_attachment'] = dict(asset_id=data['layouts'][0]['layout_id'], slot='caption')
        expected = roundtrip(compile_page(source, data))
        changed = deepcopy(source)
        for item in changed.layouts:
            item.annotations.ownership = 'ignored'
            item.annotations.paragraph_role = None
            item.annotations.paragraph_level = None
            item.annotations.citation_id = None
            item.annotations.citation_index = None
            item.annotations.continues_from_previous = False
            item.annotations.continues_to_next = False
        self.assertEqual(roundtrip(compile_page(changed, data)), expected)

    def test_caption_reference_and_note_owned_asset_survive_materialization(self):
        source = scene(('image', (0, 0, 100, 30), ''),
                       ('text', (0, 40, 100, 55), 'Caption 7.'),
                       ('text', (0, 60, 100, 75), 'Note text.'))
        data = candidate(source)
        image, label, note = (data['layouts'][i] for i in range(3))
        label['asset_attachment'] = dict(asset_id=image['layout_id'], slot='caption')
        note.update(ownership='citation', citation_id='note', citation_index=1)
        data['citations'] = [dict(citation_id='note', stream_order=0, index=1, mark='7')]
        data['references'] = [dict(layout_id=label['layout_id'], citation_index=1,
                                  anchor=dict(before='Caption ', mark='7', after='.'))]
        chapter = roundtrip(compile_page(source, data))
        from pdf_craft.extractor.chapter.chapter import Reference
        self.assertTrue(any(isinstance(part, Reference) for part in assets(chapter)[0].caption))
        self.assertEqual(len(chapter.definitions), 1)
        # The same asset and its explanation can instead live inside a note.
        image.update(ownership='citation', citation_id='note', citation_index=1)
        label.update(ownership='citation', citation_id='note', citation_index=1)
        data['references'] = []
        chapter = roundtrip(compile_page(source, data))
        note_assets = [part.asset for part in chapter.definitions[0].flow_items
                       if isinstance(part, StandaloneAsset)]
        self.assertEqual(len(note_assets), 1)
        self.assertEqual(note_assets[0].caption, ['Caption 7.'])
        self.assertFalse(chapter.flow_items)
