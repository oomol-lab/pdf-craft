"""Numbered main text must be expressible without semantic program gates."""
import json
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy
from pdf_craft.pdf import decode
from pdf_craft.extractor.chapter.primary import PrimaryPages
from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm
from pdf_craft.extractor.chapter.page_analysis import restore_streams
from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, encode, decode as decode_chapter
from tests.test_page_repair import _response_from_message, _page_with_reference


class NoteOwnershipRepairTests(IsolatedAsyncioTestCase):
    async def test_numbered_question_can_retract_mistaken_note_as_one_candidate(self):
        # Sanitized service #367 pattern: the initial algorithm put an entire
        # numbered exercise in the note stream; the model must undo that group.
        raw = decode(fromstring(
            '<page index="1"><body><layout ref="text" det="1,1,100,20">'
            'Exercises</layout></body><footnotes>'
            '<layout ref="text" det="1,30,100,50">17. Solve the equation.</layout>'
            '<layout ref="text" det="1,60,100,80">(1) Find x.</layout>'
            '</footnotes></page>'))
        source = PrimaryPages().append(raw)
        original = deepcopy(source)
        attempts = []

        async def request(messages, index, _maximum):
            attempts.append(index)
            data = _response_from_message(messages[1])
            members = [x for x in data['layouts'] if x['ownership'] == 'citation']
            self.assertTrue(members)
            if index == 0:
                data['references'] = [dict(layout_id=members[0]['layout_id'],
                    citation_index=1, anchor=dict(before='', mark='17', after='. Solve the equation.'))]
            else:
                feedback = messages[-1].message
                self.assertIn('[REF_LAYOUT_OWNERSHIP]', feedback)
                self.assertIn('submitted ownership is citation', feedback)
                self.assertIn(members[0]['layout_id'], feedback)
                self.assertIn('If the note was misidentified', feedback)
                self.assertIn('If only the reference was invented', feedback)
                for member in members:
                    member.update(ownership='paragraph', citation_id=None,
                                  citation_index=None)
                data['citations'] = []
                data['references'] = []
                # No program audit of the model's natural-language reasoning.
                data['stream_assessment'] = 'Opaque submitted reading assessment.'
            return json.dumps(data)

        result = await repair_page_with_llm(previous_page=None, target_page=source,
            next_page=None, previous_pass_probability=None, next_pass_probability=None,
            request=request, retry_policy=RetryPolicy(analysis=1))
        self.assertEqual(attempts, [0, 1])
        self.assertEqual(source, original)
        self.assertFalse(result.citations)
        self.assertTrue(all(x.ownership == 'paragraph' and not x.references for x in result.layouts))
        restored = restore_streams([result])
        self.assertFalse(restored.citations)
        chapter = Chapter(None, -1, [x for x in restored.paragraphs if isinstance(x, TextFlowItem)], [])
        serialized = tostring(encode(chapter))
        self.assertEqual(decode_chapter(fromstring(serialized)), chapter)
        text = ''.join(fromstring(serialized).itertext())
        for expected in ('17. Solve the equation.', '(1) Find x.'):
            self.assertEqual(text.count(expected), 1)

    async def test_retracting_only_a_false_reference_preserves_a_real_note(self):
        source = _page_with_reference()
        attempts = []

        async def request(messages, index, _maximum):
            attempts.append(index)
            data = _response_from_message(messages[1])
            if index == 0:
                note = next(x for x in data['layouts'] if x['ownership'] == 'citation')
                data['references'].append(dict(layout_id=note['layout_id'], citation_index=1,
                    anchor=dict(before='', mark='', after='Note.')))
            return json.dumps(data)

        result = await repair_page_with_llm(previous_page=None, target_page=source,
            next_page=None, previous_pass_probability=None, next_pass_probability=None,
            request=request, retry_policy=RetryPolicy(analysis=1))
        self.assertEqual(attempts, [0, 1])
        restored = restore_streams([result])
        self.assertEqual(len(restored.citations), 1)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(sum(len(x.references) for x in result.layouts), 1)
