import unittest

from typing import Iterable
from pdf_craft.analyser.splitter import allocate_segments
from pdf_craft.analyser.splitter.segment import Segment
from pdf_craft.analyser.common import TextInfo, TextIncision


class TestSegment(unittest.TestCase):
  def test_no_segments(self):
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    self.assertEqual(
      _to_json(allocate_segments(text_infos, 100)),
      _to_json(text_infos),
    )

  def test_one_segment(self):
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    self.assertEqual(
      _to_json(allocate_segments(text_infos, 1000)),
      _to_json([
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
        Segment(
          tokens=300,
          text_infos = [
            TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
          ],
        ),
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      ]),
    )

  def test_2_segments(self) -> None:
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MUST_BE),
      TextInfo(0, 100, TextIncision.MUST_BE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    self.assertEqual(
      _to_json(allocate_segments(text_infos, 1000)),
      _to_json([
        Segment(
          tokens=200,
          text_infos = [
            TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
          ],
        ),
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
        Segment(
          tokens=200,
          text_infos = [
            TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MUST_BE),
            TextInfo(0, 100, TextIncision.MUST_BE, TextIncision.IMPOSSIBLE),
          ],
        ),
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      ]),
    )

  def test_forced_splitted_segments(self) -> None:
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
      TextInfo(0, 250, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    self.assertEqual(
      _to_json(allocate_segments(text_infos, 400)),
      _to_json([
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
        Segment(
          tokens=200,
          text_infos = [
            TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
          ],
        ),
        Segment(
          tokens=350,
          text_infos = [
            TextInfo(0, 250, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
          ],
        ),
        TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      ]),
    )

  def test_forced_splitted_segments_with_multi_levels(self) -> None:
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MUST_BE),
      TextInfo(0, 100, TextIncision.MUST_BE, TextIncision.MOST_LIKELY),
      TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    self.assertEqual(
      _to_json(allocate_segments(text_infos, 300)),
      _to_json([
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
        Segment(
          tokens=200,
          text_infos = [
            TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MOST_LIKELY),
          ],
        ),
        Segment(
          tokens=300,
          text_infos = [
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.MUST_BE),
            TextInfo(0, 100, TextIncision.MUST_BE, TextIncision.MOST_LIKELY),
            TextInfo(0, 100, TextIncision.MOST_LIKELY, TextIncision.IMPOSSIBLE),
          ],
        ),
        TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      ]),
    )

def _to_json(items: Iterable[TextInfo | Segment]) -> list[dict]:
  json_list: list[dict] = []
  for item in items:
    if isinstance(item, TextInfo):
      json_list.append(_text_info_to_json(item))
    elif isinstance(item, Segment):
      json_list.append({
        "tokens": item.tokens,
        "text_infos": [_text_info_to_json(t) for t in item.text_infos],
      })
    else:
      raise ValueError(f"Unexpected: {item}")

  # print("# JSON List")
  # for i, item in enumerate(json_list):
  #   print(i, item)
  return json_list

def _text_info_to_json(text_info: TextInfo) -> list[dict]:
  return {
    "tokens": text_info.tokens,
    "start": text_info.start_incision.name,
    "end": text_info.end_incision.name,
  }