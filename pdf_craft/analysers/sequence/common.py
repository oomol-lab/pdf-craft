from typing import TypedDict
from strenum import StrEnum
from xml.etree.ElementTree import Element


class Phase(StrEnum):
  EXTRACTION = "extraction"
  TEXT_JOINT = "text-joint"
  FOOTNOTE_JOINT = "footnote-joint"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  max_data_tokens: int
  max_verify_paragraph_tokens: int
  max_verify_paragraphs_count: int
  completed_ranges: list[list[int]]

class SequenceType(StrEnum):
  TEXT = "text"
  FOOTNOTE = "footnote"

class Truncation(StrEnum):
  YES = "truncated"
  NO = "not-truncated"
  PROBABLY = "probably"
  UNCERTAIN = "uncertain"

def get_truncation_attr(element: Element, attr_name: str) -> Truncation:
  value = element.get(attr_name, None)
  if value is not None:
    try:
      return Truncation(value)
    except Exception:
      pass
  return Truncation.UNCERTAIN