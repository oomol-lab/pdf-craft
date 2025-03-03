import os

from typing import Iterable
from ..pdf import Block
from .llm import LLM
from .preliminary import preliminary_analyse
from .secondary import SecondaryAnalyser

def analyse(llm: LLM, analysing_dir_path: str, blocks_matrix: Iterable[list[Block]]):
  secondary_analyser = SecondaryAnalyser(llm, analysing_dir_path)
  page_dir_path = os.path.join(analysing_dir_path, "pages")
  assets_dir_path = secondary_analyser.assets_dir_path

  for dir_path in (page_dir_path, assets_dir_path):
    os.makedirs(dir_path, exist_ok=True)

  preliminary_analyse(
    llm=llm,
    page_dir_path=page_dir_path,
    assets_dir_path=assets_dir_path,
    blocks_matrix=blocks_matrix,
  )
  secondary_analyser.analyse_citations(10000, 0.15)
  secondary_analyser.analyse_main_texts(11500, 0.1)
  secondary_analyser.analyse_chapters()