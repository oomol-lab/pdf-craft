from typing import TypedDict
from strenum import StrEnum


class Phase(StrEnum):
  EXTRACTION = "extraction"
  TEXT_JOINT = "text-joint"
  FOOTNOTE_JOINT = "footnote-joint"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  max_data_tokens: int
  completed_ranges: list[list[int]]

class PageType(StrEnum):
  TEXT = "text"
  CONTENTS = "contents"
  REFERENCES = "references"
  COPYRIGHT = "copyright"

class SequenceType(StrEnum):
  TEXT = "text"
  FOOTNOTE = "footnote"

class Truncation(StrEnum):
  YES = "truncated"
  NO = "not-truncated"
  PROBABLY = "probably"
  UNCERTAIN = "uncertain"