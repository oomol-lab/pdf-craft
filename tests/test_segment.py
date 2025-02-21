import unittest

from typing import Iterable
from pdf_craft.analyser.segment import allocate_segments, Segment
from pdf_craft.analyser.secondary import TextInfo, TextIncision

class TestSplitter(unittest.TestCase):
  def test_no_segments(self):
    text_infos = [
      TextInfo(100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    self.assertEqual(
      _to_json(allocate_segments(text_infos, 100)),
      _to_json(text_infos),
    )

def _to_json(items: Iterable[TextInfo | Segment]) -> list[dict]:
  json_list: list[dict] = []
  for item in items:
    if isinstance(item, TextInfo):
      json_list.append(item.__dict__)
    elif isinstance(item, Segment):
      json_list.append({
        **item.__dict__,
        "text_infos": _to_json(item.text_infos),
      })
  return json_list