from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Chapter:
  name: str
  children: list[Chapter]

@dataclass
class Contents:
  prefaces: list[Chapter]
  chapters: list[Chapter]
