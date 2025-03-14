import unittest

from pdf_craft.analyser.common import TextInfo, TextIncision
from pdf_craft.analyser.splitter import group, allocate_segments
from pdf_craft.analyser.splitter.group import Group
from pdf_craft.analyser.splitter.segment import Segment


class TestGroup(unittest.TestCase):
  def test_uniform_texts(self):
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(1, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(2, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(3, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(4, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    groups = list(group(
      items=allocate_segments(text_infos, 1000),
      max_tokens=400,
      gap_rate=0.25,
      tail_rate=0.5,
    ))
    self.assertListEqual(
      [_group_to_json(group) for group in groups],
      [{
        "head": [],
        "head_remain": 0,
        "body": ["T[0]100", "T[1]100"],
        "tail": ["T[2]100"],
        "tail_remain": 100,
      }, {
        "head": ["T[1]100"],
        "head_remain": 100,
        "body": ["T[2]100", "T[3]100"],
        "tail": ["T[4]100"],
        "tail_remain": 100,
      }, {
        "head": ["T[3]100"],
        "head_remain": 100,
        "body": ["T[4]100"],
        "tail": [],
        "tail_remain": 0,
      }],
    )

  def test_huge_fragment_barrier(self):
    text_infos = [
      TextInfo(0, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(1, 300, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(2, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(3, 100, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    groups = list(group(
      items=allocate_segments(text_infos, 1000),
      max_tokens=400,
      gap_rate=0.25,
      tail_rate=0.5,
    ))
    self.assertListEqual(
      [_group_to_json(group) for group in groups],
      [{
        "head": [],
        "head_remain": 0,
        "body": ["T[0]100"],
        "tail": ["T[1]300"],
        "tail_remain": 300,
      }, {
        "head": ["T[0]100"],
        "head_remain": 50,
        "body": ["T[1]300"],
        "tail": ["T[2]100"],
        "tail_remain": 50,
      }, {
        "head": ["T[1]300"],
        "head_remain": 200,
        "body": ["T[2]100", "T[3]100"],
        "tail": [],
        "tail_remain": 0,
      }],
    )

  def test_distribute_between_head_and_tail(self):
    text_infos = [
      TextInfo(0, 400, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(1, 200, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(2, 400, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    groups = list(group(
      items=allocate_segments(text_infos, 1000),
      max_tokens=400,
      gap_rate=0.25,
      tail_rate=0.8,
    ))
    self.assertListEqual(
      [_group_to_json(group) for group in groups],
      [{
        "head": [],
        "head_remain": 0,
        "body": ["T[0]400"],
        "tail": [],
        "tail_remain": 0,
      }, {
        "head": ["T[0]400"],
        "head_remain": 40,
        "body": ["T[1]200"],
        "tail": ["T[2]400"],
        "tail_remain": 160,
      }, {
        "head": [],
        "head_remain": 0,
        "body": ["T[2]400"],
        "tail": [],
        "tail_remain": 0,
      }],
    )

  def test_distribute_all_to_tail(self):
    text_infos = [
      TextInfo(0, 400, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(1, 200, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
      TextInfo(2, 400, TextIncision.IMPOSSIBLE, TextIncision.IMPOSSIBLE),
    ]
    groups = list(group(
      items=allocate_segments(text_infos, 1000),
      max_tokens=400,
      gap_rate=0.25,
      tail_rate=1.0,
    ))
    self.assertListEqual(
      [_group_to_json(group) for group in groups],
      [{
        "head": [],
        "head_remain": 0,
        "body": ["T[0]400"],
        "tail": [],
        "tail_remain": 0,
      }, {
        "head": [],
        "head_remain": 0,
        "body": ["T[1]200"],
        "tail": ["T[2]400"],
        "tail_remain": 200,
      }, {
        "head": [],
        "head_remain": 0,
        "body": ["T[2]400"],
        "tail": [],
        "tail_remain": 0,
      }],
    )

def _group_to_json(item: Group) -> dict:
  return {
    "head_remain": item.head_remain_tokens,
    "tail_remain": item.tail_remain_tokens,
    "head": [_item_to_json(item) for item in item.head],
    "body": [_item_to_json(item) for item in item.body],
    "tail": [_item_to_json(item) for item in item.tail],
  }

def _item_to_json(item: TextInfo | Segment) -> str:
  letter: str
  if isinstance(item, TextInfo):
    letter = "T"
  else:
    letter = "S"
  return f"{letter}[{item.page_index}]{item.tokens}"