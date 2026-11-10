"""CSS Text transport subset and algorithmic recovery (no model requests)."""
# pylint: disable=protected-access
from unittest import TestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.transformer.whitespace import WhiteSpace, normalize, mode_for, can_indent
from pdf_craft.transformer.xml_translator.xml.friendly import encode_friendly, decode_friendly


class WhiteSpaceStandardsTests(TestCase):
    def test_modes_match_css_text_whitespace_table(self):
        text = ' a\t  b \r\n  c '
        for mode, expected in (
                (WhiteSpace.NORMAL, ' a b c '), (WhiteSpace.NOWRAP, ' a b c '),
                (WhiteSpace.PRE_LINE, ' a b\nc '),
                (WhiteSpace.PRE, ' a\t  b \n  c '),
                (WhiteSpace.PRE_WRAP, ' a\t  b \n  c '),
                (WhiteSpace.BREAK_SPACES, ' a\t  b \n  c ')):
            with self.subTest(mode=mode):
                self.assertEqual(normalize(text, mode), expected)
                self.assertEqual(normalize(expected, mode), expected)

    def test_special_spaces_in_normal_prose_are_normalized(self):
        text = 'a\u00a0\u2009\u202f\u3000b'
        self.assertEqual(normalize(text, trim=True), 'a b')
        self.assertEqual(normalize('\u00a0x\u00a0', trim=True), 'x')

    def test_context_inheritance_overrides_and_html_defaults(self):
        self.assertEqual(mode_for(fromstring('<pre/>')), WhiteSpace.PRE)
        self.assertEqual(mode_for(fromstring('<textarea/>')), WhiteSpace.PRE_WRAP)
        self.assertEqual(mode_for(fromstring('<span/>'), WhiteSpace.PRE), WhiteSpace.PRE)
        self.assertEqual(mode_for(fromstring('<span style="white-space: normal"/>'), WhiteSpace.PRE), WhiteSpace.NORMAL)
        self.assertEqual(mode_for(fromstring('<p style="white-space: pre !important; white-space: normal"/>')), WhiteSpace.PRE)
        self.assertEqual(mode_for(fromstring('<p xml:space="preserve"/>')), WhiteSpace.PRE)

    def test_only_block_only_containers_get_indentation(self):
        root = fromstring('<div><div><p>first</p><p>second</p></div><p>last</p></div>')
        rendered = encode_friendly(root)
        self.assertIn('\n  <div>\n    <p>first</p>\n    <p>second</p>\n  </div>', rendered)
        for value in ('<div>hello<span>world</span></div>',
                      '<span><p>one</p><p>two</p></span>',
                      '<div display="inline"><p>one</p><p>two</p></div>',
                      '<div style="white-space: pre"><p>one</p><p>two</p></div>'):
            element = fromstring(value)
            self.assertFalse(can_indent(element, mode_for(element)))
            self.assertNotIn('\n', encode_friendly(element))

    def test_friendly_normalizes_without_creating_inline_separators(self):
        root = fromstring('<div>hello   <span> world</span>\n again<span>!</span></div>')
        rendered = encode_friendly(root)
        self.assertIn('hello <span> world</span> again<span>!</span>', rendered)
        decoded = next(decode_friendly(rendered, tags='div'))
        self.assertEqual(normalize(''.join(root.itertext()), trim=True),
                         normalize(''.join(decoded.itertext()), trim=True))
        pre = fromstring('<pre>hello   <span> world</span>\n again</pre>')
        self.assertEqual(''.join(next(decode_friendly(encode_friendly(pre), tags='pre')).itertext()),
                         ''.join(pre.itertext()))
        self.assertEqual(encode_friendly(fromstring('<p>hello<span>world</span></p>')),
                         '<p>hello<span>world</span></p>')
        preserved = fromstring('<div xml:space="preserve">a  <span>b</span>\n c</div>')
        decoded = next(decode_friendly(encode_friendly(preserved), tags='div'))
        self.assertEqual(tostring(decoded), tostring(preserved))
