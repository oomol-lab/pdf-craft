import unittest

from typing import Iterable
from doc_page_extractor import Rectangle, Layout, LayoutClass, OCRFragment
from pdf_craft.pdf.section import Section
from pdf_craft.pdf.text_matcher import check_texts_matching_rate, split_into_words


_Rect = tuple[float, float, float, float]

class TextFramework(unittest.TestCase):

  def test_framework_generation(self):
    layouts1 = [
      _layout((20.0, 20.0, 100.0, 20.0), (
        ((1.5, 2.3, 197.0, 11.0), "the Little Prince"),
      )),
      _layout((160.0, 30.0, 30.0, 40.0), (
        ((5.0, 5.0, 20.0, 15.0), "Antoine de"),
        ((4.0, 21.0, 22.0, 14.8), "Saint-Exupéry"),
      )),
      _layout((23.0, 50.0, 130.0, 50.0), (
        ((0.0, 0.0, 135.0, 15.0), "I believe that for his escape he took advantage of"),
        ((0.0, 17.0, 135.0, 15.0), "the migration of a flock of wild birds. On the morning"),
        ((0.0, 34.0, 135.0, 15.0), "of his departure he put his planet in perfect order. "),
      )),
      _layout((23.0, 120.0, 170.0, 230.0), (
        ((0.0, 0.0, 165.0, 20.0), "He carefully cleaned out his active volcanoes. He possessed"),
        ((0.0, 25.0, 165.0, 20.0), "two active volcanoes; and they were very convenient for"),
        ((0.0, 50.0, 165.0, 20.0), "heating his breakfast in the morning. He also had one"),
        ((0.0, 75.0, 165.0, 20.0), "volcano that was extinct. But, as he said, \"One never knows!\","),
        ((0.0, 100.0, 165.0, 20.0), "So he cleaned out the extinct volcano, too. If they are well cleaned out,"),
        ((0.0, 125.0, 165.0, 20.0), "volcanoes burn slowly and steadily, without any eruptions."),
        ((0.0, 150.0, 165.0, 20.0), "Volcanic eruptions are like fires in a chimney."),
      )),
      _layout((90.0, 370.0, 40.0, 20.0), (
        ((1.0, 2.0, 35.0, 16.5), "36"),
      )),
    ]
    layouts2 = [
      _layout((160.3, 30.7, 30.2, 40.3), (
        ((5.0, 5.0, 20.0, 15.0), "Antoine  de"),
        ((4.0, 21.0, 22.0, 14.8), "Saint-Exupéry"),
      )),
      _layout((20.0, 20.0, 100.0, 20.0), (
        ((1.7, 2.2, 200.0, 11.2), "the Little Prince"),
      )),
      _layout((90.2, 370.2, 40.3, 20.2), (
        ((1.0, 2.0, 35.0, 16.5), "37"),
      )),
      _layout((23.7, 50.2, 130.3, 50.1), (
        ((0.0, 0.0, 135.0, 15.0), "The first of them was inhabited by a king."),
        ((0.0, 17.0, 135.0, 15.0), "Clad in royal purple and ermine, he was seated"),
        ((0.0, 34.0, 135.0, 15.0), "upon a throne which was at the same time both simple and majestic."),
      )),
    ]
    # Scanned book pages often shift, this is normal and needs to be fixed.
    delta2 = (27.35, 48.12)
    layouts2 = [
      Layout(
        cls=layout.cls,
        rect=_move_rect(layout.rect, delta2),
        fragments=[
          OCRFragment(
            order=fragment.order,
            text=fragment.text,
            rank=fragment.rank,
            rect=_move_rect(fragment.rect, delta2),
          )
          for fragment in layout.fragments
        ],
      )
      for layout in layouts2
    ]
    session = Section(0, layouts1)
    session.link_next(Section(0, layouts2), 1)
    framework_indexes = sorted([layouts1.index(layout) for layout in session.framework()])

    self.assertListEqual(framework_indexes, [0, 1, 4])

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

def _layout(rect: _Rect, fragments: Iterable[tuple[_Rect, str]]):
  origin = (rect[0], rect[1])
  return Layout(
    cls=LayoutClass.PLAIN_TEXT,
    rect=_rect(rect),
    fragments=[
      OCRFragment(
        order=i,
        text=text,
        rank=1.0,
        rect=_move_rect(_rect(rect), origin),
      )
      for i, (rect, text) in enumerate(fragments)
    ],
  )

def _rect(rect: _Rect) -> Rectangle:
  x0, y0, width, height = rect
  return Rectangle(
    lt=(x0, y0),
    lb=(x0, y0 + height),
    rt=(x0 + width, y0),
    rb=(x0 + width, y0 + height),
  )

def _move_rect(rect: Rectangle, delta: tuple[float, float]) -> Rectangle:
  return Rectangle(
    lt=(rect.lt[0] + delta[0], rect.lt[1] + delta[1]),
    lb=(rect.lb[0] + delta[0], rect.lb[1] + delta[1]),
    rt=(rect.rt[0] + delta[0], rect.rt[1] + delta[1]),
    rb=(rect.rb[0] + delta[0], rect.rb[1] + delta[1]),
  )