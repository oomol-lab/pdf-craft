from pathlib import Path

from ...llm import LLM
from ..utils import Context
from .common import State, Corrector


class SingleCorrector(Corrector):
  def __init__(self, llm: LLM, context: Context[State]):
    super().__init__()
    self._llm: LLM = llm
    self._ctx: Context[State] = context

  def do(self, from_path: Path, request_path: Path, is_footnote: bool) -> None:
    pass