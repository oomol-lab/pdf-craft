import os

from typing import Iterable
from ..pdf import Block
from .llm import LLM
from .preliminary import preliminary_analyse
from .secondary import SecondaryAnalyser

def analyse(
    llm: LLM,
    analysing_dir_path: str,
    output_dir_path: str,
    blocks_matrix: Iterable[list[Block]],
  ):
  page_dir_path = os.path.join(analysing_dir_path, "pages")
  assets_dir_path = os.path.join(output_dir_path, "assets")

  for dir_path in (page_dir_path, assets_dir_path):
    os.makedirs(dir_path, exist_ok=True)

  preliminary_analyse(
    llm=llm,
    page_dir_path=page_dir_path,
    assets_dir_path=assets_dir_path,
    blocks_matrix=blocks_matrix,
  )
  secondary_analyser = SecondaryAnalyser(
    llm=llm,
    assets_dir_path=assets_dir_path,
    output_dir_path=output_dir_path,
    analysing_dir_path=analysing_dir_path,
  )
  secondary_analyser.analyse_citations(8000, 0.15)
  secondary_analyser.analyse_main_texts(10000, 0.1)
  secondary_analyser.analyse_chapters()