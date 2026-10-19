"""Review counterexamples exercised through source projection and LLM repair."""
# pylint: disable=protected-access
from copy import deepcopy
import json
import unittest
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.chapter import Reference, SourceTextFragment, TextFlowItem
from pdf_craft.extractor.chapter.generation import _resolve_pages
from pdf_craft.extractor.chapter.page_analysis import analyse_pages, restore_streams
from pdf_craft.extractor.chapter.page_repair import (
    AllPageLlmRepairProcessor, RepairedPage, _compile_repaired_page,
)
from pdf_craft.llm.guaranteed import GuaranteedBusinessError
from pdf_craft.pdf import decode
from tests.test_footnote_contracts import fragment, response


def reclassify_sources(footnotes: str, *, footer: str = '') -> list[SourceTextFragment]:
    """Run the real OCR -> analysis -> ownership edit -> assembly boundary."""
    raw = decode(fromstring(
        '<page index="1"><body>'
        '<layout ref="text" det="0,0,100,20">Left A.</layout>'
        '<layout ref="text" det="0,70,100,90">Left C.</layout>'
        '<layout ref="text" det="140,0,240,20">Right D.</layout>'
        '<layout ref="text" det="140,70,240,90">Right F.</layout>'
        f'{footer}</body><footnotes>{footnotes}</footnotes></page>'))
    paragraphs, citations = _resolve_pages([raw])
    page = analyse_pages([1], paragraphs, citations)[0]
    proposed = response(page)
    proposed['citations'], proposed['references'] = [], []
    for layout in proposed['layouts']:
        layout.update(ownership='paragraph', citation_id=None, citation_index=None,
                      continues_from_previous=False, continues_to_next=False)
    repaired = _compile_repaired_page(page, RepairedPage.model_validate(proposed))
    return [child for item in restore_streams([repaired]).paragraphs
            if isinstance(item, TextFlowItem) for child in item.children
            if isinstance(child, SourceTextFragment)]


def shared_definition_pages():
    note = Reference(1, 1, '①', [TextFlowItem('body', -1, [
        fragment(1, 2, 'START'), fragment(1, 3, 'LOST'), fragment(2, 2, 'TAIL')])])
    return analyse_pages([1, 2], [], [note])


def move_shared_member(page, ownership):
    proposed = response(page)
    for layout in proposed['layouts']:
        layout.pop('continues_to_next')
    member = proposed['layouts'][1]
    member.update(ownership=ownership, citation_id=None, citation_index=None,
                  continues_from_previous=False)
    if ownership == 'ignored':
        member.update(paragraph_role=None, paragraph_level=None)
    elif ownership == 'citation':
        proposed['citations'].append(dict(citation_id='new-1-2', index=2, mark='②', stream_order=1))
        member.update(citation_id='new-1-2', citation_index=2)
    return proposed


class FootnoteReviewContractsTests(unittest.IsolatedAsyncioTestCase):
    def test_left_column_tail_stays_before_right_column(self):
        sources = reclassify_sources(
            '<layout ref="text" det="0,100,100,120">① Left last.</layout>')
        self.assertEqual([child.content for child in sources],
                         [['Left A.'], ['Left C.'], ['① Left last.'], ['Right D.'], ['Right F.']])
        self.assertEqual([child.source_order for child in sources], [0, 1, 4, 2, 3])
        self.assertEqual(sources[2].bbox, (0, 100, 100, 120))
        origin = sources[2].source
        assert origin is not None
        self.assertEqual(origin.text, '① Left last.')

    def test_multiple_column_tail_members_keep_stable_order(self):
        # OCR stream order is not a global y order; equal-height sources retain
        # their stream order, and later insertions can precede an earlier one.
        sources = reclassify_sources(
            '<layout ref="text" det="0,140,100,160">① Left last.</layout>'
            '<layout ref="text" det="140,100,240,120">② Right last.</layout>'
            '<layout ref="text" det="0,100,100,120">③ Left middle.</layout>'
            '<layout ref="text" det="0,100,100,120">④ Left tied.</layout>')
        self.assertEqual([child.content for child in sources], [
            ['Left A.'], ['Left C.'], ['③ Left middle.'], ['④ Left tied.'],
            ['① Left last.'], ['Right D.'], ['Right F.'], ['② Right last.']])
        self.assertEqual([child.source_order for child in sources], [0, 1, 6, 7, 4, 2, 3, 5])

    def test_column_tail_respects_full_width_band_boundary(self):
        sources = reclassify_sources(
            '<layout ref="text" det="0,100,100,120">① Left last.</layout>',
            footer='<layout ref="text" det="0,150,240,170">Full width.</layout>')
        self.assertEqual([child.content for child in sources], [
            ['Left A.'], ['Left C.'], ['① Left last.'],
            ['Right D.'], ['Right F.'], ['Full width.']])

    async def test_shared_member_cannot_be_ignored(self):
        await self.assert_shared_member_rejected('ignored')

    async def test_shared_member_cannot_be_moved_to_body(self):
        await self.assert_shared_member_rejected('paragraph')

    async def test_shared_member_cannot_be_regrouped_to_another_definition(self):
        await self.assert_shared_member_rejected('citation')

    async def assert_shared_member_rejected(self, ownership):
        pages = shared_definition_pages()
        original = deepcopy(pages)

        async def request(_messages, _index, _maximum):
            return json.dumps(move_shared_member(pages[0], ownership))

        with self.assertRaises(GuaranteedBusinessError) as caught:
            await AllPageLlmRepairProcessor(request, page_indexes=[1], max_retries=0)(
                [], pages, {})
        self.assertIn('[CROSS_PAGE_MEMBERSHIP]', str(caught.exception.__cause__))
        self.assertEqual(pages, original)
        text = restore_streams(pages).citations[0].flow_items[0]
        assert isinstance(text, TextFlowItem)
        self.assertEqual([child.content for child in text.children],
                         [['START'], ['LOST'], ['TAIL']])

    async def test_shared_member_rejection_feedback_allows_valid_retry(self):
        pages = shared_definition_pages()
        original = deepcopy(pages)
        attempts = []

        async def request(messages, index, _maximum):
            attempts.append(index)
            self.assertEqual(pages, original)
            if index == 0:
                return json.dumps(move_shared_member(pages[0], 'ignored'))
            self.assertIn('[CROSS_PAGE_MEMBERSHIP]', messages[-1].message)
            return json.dumps(response(pages[0]))

        repaired = await AllPageLlmRepairProcessor(request, page_indexes=[1], max_retries=1)(
            [], pages, {})
        self.assertEqual(attempts, [0, 1])
        self.assertEqual(pages, original)
        restored = restore_streams(repaired)
        self.assertEqual(len(restored.citations), 1)
        text = restored.citations[0].flow_items[0]
        assert isinstance(text, TextFlowItem)
        self.assertEqual([child.content for child in text.children],
                         [['START'], ['LOST'], ['TAIL']])
