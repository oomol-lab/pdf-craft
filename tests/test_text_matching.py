import unittest

from pdf_craft.pdf.text_matcher import check_texts_matching_rate, split_into_words

class TextTextMatching(unittest.TestCase):

  def test_texts_matching(self):
    self.assertEqual(
      check_texts_matching_rate("Hello, world!", "Hello, world!"),
      (1.0, 4),
    )
    self.assertEqual(
      check_texts_matching_rate("围点打援", "围点打援！"),
      (4 / 5, 5),
    )
    self.assertEqual(
      check_texts_matching_rate("围点打援", "围点打缓"),
      (3 / 4, 4),
    )
    self.assertEqual(
      check_texts_matching_rate("围点打援", "援点打围"),
      (0.75, 4),
    )
    self.assertEqual(
      check_texts_matching_rate("围点Foobar打援", "围点打援"),
      (4 / 5, 5),
    )

  def test_splitting_into_words(self):
    self.assertEqual(
      list(split_into_words("Hello, world!")),
      ["Hello", ",", "world", "!"]
    )
    self.assertEqual(
      list(split_into_words("Люди мира едины.")),
      ["Люди", "мира", "едины", "."],
    )
    self.assertEqual(
      list(split_into_words("各个国家都有各个国家的国歌。")),
      [c for c in "各个国家都有各个国家的国歌。"]
    )
    self.assertEqual(
      list(split_into_words("获取class 的意义")),
      ["获", "取", "class", "的", "意", "义"]
    )