"""Printed markers survive OCR Markdown/LaTeX transport wrappers."""
import unittest
from copy import deepcopy
from xml.etree.ElementTree import fromstring

from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import InlineExpression
from pdf_craft.extractor.chapter.reference import extract_head_mark
from pdf_craft.extractor.chapter.content import Content
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.pdf import decode


class FootnoteMarkerTests(unittest.TestCase):
    def test_escaped_star_is_a_definition_marker_without_changing_its_source(self):
        content: Content = [r'\* An actual explanatory note.']
        before = deepcopy(content)
        mark, rest = extract_head_mark(content)
        self.assertEqual(mark, '*')
        self.assertEqual(rest, ['An actual explanatory note.'])
        self.assertEqual(content, before)

    def test_exact_inline_circle_is_a_marker_but_math_is_not(self):
        content = [InlineExpression(ExpressionKind.INLINE_PAREN, '①'), ' An explanatory note.']
        before = deepcopy(content)
        mark, rest = extract_head_mark(content)
        self.assertEqual(str(mark), '①')
        self.assertEqual(rest, ['An explanatory note.'])
        self.assertEqual(content, before)
        formula = [InlineExpression(ExpressionKind.INLINE_PAREN, 'x+1'), ' is positive.']
        self.assertIsNone(extract_head_mark(formula)[0])

    def test_initial_analysis_resolves_wrapped_definitions_and_body_reference(self):
        for head in (r'\(①\)', r'$①$'):
            with self.subTest(head=head):
                raw = decode(fromstring('<page index="1"><body><layout ref="text" '
                    'det="0,0,100,30">A sentence①.</layout></body><footnotes>'
                    f'<layout ref="text" det="0,80,100,95">{head} A note.</layout>'
                    '</footnotes></page>'))
                before = raw.footnotes_layouts[0].text
                target = _project_window([None, raw, None], True)[1]
                assert target is not None
                self.assertEqual(len(target.citations), 1)
                self.assertEqual(target.citations[0].index, 1)
                self.assertEqual(getattr(target.citations[0].mark, 'char', None), '①')
                self.assertEqual(len(target.layouts[0].references), 1)
                self.assertEqual(raw.footnotes_layouts[0].text, before)
                disabled = _project_window([None, raw, None], False)[1]
                assert disabled is not None
                self.assertFalse(disabled.citations)
                self.assertFalse(any(layout.references for layout in disabled.layouts))

    def test_body_math_wrappers_and_escaped_stars_resolve_only_existing_definitions(self):
        for body, note in ((r'A sentence\(①\).', '① A note.'),
                           (r'A sentence\*.', r'\* A note.')):
            with self.subTest(body=body):
                raw = decode(fromstring('<page index="1"><body><layout ref="text" '
                    f'det="0,0,100,30">{body}</layout></body><footnotes>'
                    f'<layout ref="text" det="0,80,100,95">{note}</layout>'
                    '</footnotes></page>'))
                target = _project_window([None, raw, None], True)[1]
                assert target is not None
                self.assertEqual(len(target.layouts[0].references), 1)
                self.assertFalse(any(isinstance(part, str) and "\\" in part
                                     for part in target.layouts[0].source.content))
                no_definition = decode(fromstring('<page index="1"><body><layout ref="text" '
                    f'det="0,0,100,30">{body}</layout></body><footnotes/></page>'))
                untouched = _project_window([None, no_definition, None], True)[1]
                assert untouched is not None
                self.assertFalse(untouched.layouts[0].references)

    def test_unmarked_continuation_is_not_a_new_definition(self):
        for text in ('An unmarked continuation.', '2024 was an important year.', '3 + 2 = 5'):
            self.assertIsNone(extract_head_mark([text])[0])
