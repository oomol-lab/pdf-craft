"""Stable source-work units across languages, XML and arbitrary chunks."""
import unittest
from typing import Any, cast
from xml.etree.ElementTree import fromstring

from pdf_craft import MAX_WORD_LENGTH, WordCounter, count_text_words
from pdf_craft.transformer.word_count import count_xml_words


class WordTests(unittest.TestCase):
    def test_inherits_wiki_graph_unicode_rules(self):
        for text, expected in [('Alpha beta 123', 3), ('朱元璋占据应天', 7),
                               ('Alpha朱元璋 beta', 5), ('  中文，English!  ', 3),
                               (' \n\t ', 0), ('e\u0301clair', 1), ('عَرَبِيّ', 1),
                               ('\u0301', 0), ('\U00031350\U00020000々〇', 4),
                               ('あいう 한글', 2), ('a-b can\'t', 4)]:
            with self.subTest(text=text):
                self.assertEqual(count_text_words(text), expected)

    def test_long_words_and_marks(self):
        for length, expected in [(MAX_WORD_LENGTH, 1), (MAX_WORD_LENGTH + 1, 2),
                                 (3 * MAX_WORD_LENGTH, 3), (3 * MAX_WORD_LENGTH + 1, 4)]:
            self.assertEqual(count_text_words('a' * length), expected)
            self.assertEqual(count_text_words('1' * length), expected)
            self.assertEqual(count_text_words('e\u0301' * length), expected)
        for invalid in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                WordCounter(cast(Any, invalid))

    def test_every_split_and_one_character_feeds_agree(self):
        text = 'he\u0301llo中文 ' + 'a' * (3 * MAX_WORD_LENGTH + 1) + ',123-عَرَبِيّ'
        expected = count_text_words(text)
        for split in range(len(text) + 1):
            counter = WordCounter()
            counter.feed(text[:split])
            self.assertEqual(counter.feed(text[split:]), expected)
        counter = WordCounter()
        for character in text:
            counter.feed(character)
        self.assertEqual(counter.words, expected)
        counter = WordCounter()
        counter.feed('hel')
        counter.boundary()
        self.assertEqual(counter.feed('lo'), 2)

    def test_xml_inline_and_paragraph_boundaries(self):
        self.assertEqual(count_xml_words(fromstring('<body><p>hel<b>lo</b> world</p><p>new</p></body>')), 3)
        self.assertEqual(count_xml_words(fromstring('<body><p><b>hello</b> <i>world</i></p></body>')), 2)
        self.assertEqual(count_xml_words(fromstring('<chapter><flow><text><fragment>hel</fragment><fragment>lo</fragment></text><text><fragment>new</fragment></text></flow></chapter>')), 2)
        self.assertEqual(count_xml_words(fromstring('<asset><translation-context>unbilled context</translation-context><caption>caption</caption></asset>')), 1)
