from typing import Literal
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL.Image import Image
from doc_page_extractor import clip, Rectangle, Layout, LayoutClass, OCRFragment, ExtractedResult
from .document import DocumentExtractor


class TextKind(Enum):
  TITLE = 0
  PLAIN_TEXT = 1
  ABANDON = 2

@dataclass
class Text:
  content: str
  rank: float
  rect: Rectangle

@dataclass
class TextBlock:
  rect: Rectangle
  kind: TextKind
  texts: list[Text]
  has_paragraph_indentation: bool = False
  last_line_touch_end: bool = False

@dataclass
class AssetBlock:
  rect: Rectangle
  image: Image
  texts: list[Text]

Block = TextBlock | AssetBlock

class PDFPageExtractor:
  def __init__(
      self,
      device: Literal["cpu", "cuda"],
      model_dir_path: str,
      debug_dir_path: str | None = None,
    ):
    self._doc_extractor: DocumentExtractor = DocumentExtractor(
      device=device,
      model_dir_path=model_dir_path,
      debug_dir_path=debug_dir_path,
    )

  def extract(self, pdf_file_path: str) -> Generator[list[Block], None, None]:
    for result, layouts in self._doc_extractor.extract(pdf_file_path):
      blocks = self._convert_to_blocks(result, layouts)
      page_range = self._texts_range(blocks)

      for block in blocks:
        if not isinstance(block, TextBlock) or \
           block.kind == TextKind.ABANDON:
          continue

        if len(block.texts) == 1:
          mean_line_height, x1, x2 = page_range
        else:
          mean_line_height, x1, x2 = self._texts_range(blocks)

        first_text = block.texts[0]
        last_text = block.texts[-1]
        first_delta_x = (first_text.rect.lt[0] + first_text.rect.lb[0]) / 2 - x1
        last_delta_x = x2 - (last_text.rect.rt[0] + last_text.rect.rb[0]) / 2
        block.has_paragraph_indentation = first_delta_x > mean_line_height
        block.last_line_touch_end = last_delta_x < mean_line_height

      yield blocks

  def _convert_to_blocks(self, result: ExtractedResult, layouts: list[Layout]) -> list[Block]:
    store: list[tuple[LayoutClass, Block]] = []
    def previous_block(cls: LayoutClass) -> Block | None:
      for i in range(len(store) - 1, -1, -1):
        block_cls, block = store[i]
        if cls == block_cls:
          return block
        if cls != LayoutClass.ABANDON:
          return None
      return None

    for layout in layouts:
      cls = layout.cls
      if cls == LayoutClass.TITLE:
        store.append((cls, TextBlock(
          rect=layout.rect,
          kind=TextKind.TITLE,
          texts=self._convert_to_text(layout.fragments),
        )))
      elif cls == LayoutClass.PLAIN_TEXT:
        store.append((cls, TextBlock(
          rect=layout.rect,
          kind=TextKind.PLAIN_TEXT,
          texts=self._convert_to_text(layout.fragments),
        )))
      elif cls == LayoutClass.ABANDON:
        store.append((cls, TextBlock(
          rect=layout.rect,
          kind=TextKind.ABANDON,
          texts=self._convert_to_text(layout.fragments),
        )))
      elif cls == LayoutClass.FIGURE or \
           cls == LayoutClass.TABLE or \
           cls == LayoutClass.ISOLATE_FORMULA:
        store.append((cls, AssetBlock(
          rect=layout.rect,
          texts=[],
          image=clip(result, layout),
        )))
      elif cls == LayoutClass.FIGURE_CAPTION:
        block = previous_block(LayoutClass.FIGURE)
        if block is not None:
          assert isinstance(block, AssetBlock)
          block.texts.extend(self._convert_to_text(layout.fragments))
      elif cls == LayoutClass.TABLE_CAPTION or \
           cls == LayoutClass.TABLE_FOOTNOTE:
        block = previous_block(LayoutClass.TABLE)
        if block is not None:
          assert isinstance(block, AssetBlock)
          block.texts.extend(self._convert_to_text(layout.fragments))
      elif cls == LayoutClass.FORMULA_CAPTION:
        block = previous_block(LayoutClass.ISOLATE_FORMULA)
        if block is not None:
          assert isinstance(block, AssetBlock)
          block.texts.extend(self._convert_to_text(layout.fragments))

    return [block for _, block in store]

  def _texts_range(self, blocks: Iterable[Block]) -> tuple[float, float, float]:
    mean_line_height: float = 0.0
    texts_count: int = 0
    x1: float = float("inf")
    x2: float = float("-inf")

    for block in blocks:
      if not isinstance(block, TextBlock):
        continue
      if block.kind == TextKind.ABANDON:
        continue
      for text in block.texts:
        mean_line_height += text.rect.size[0]
        texts_count += 1
        for x, _ in text.rect:
          x1 = min(x1, x)
          x2 = max(x2, x)

    if texts_count == 0:
      return 0.0, 0.0, 0.0
    return mean_line_height, x1, x2

  def _convert_to_text(self, fragments: list[OCRFragment]) -> list[Text]:
    return [
      Text(
        content=f.text,
        rank=f.rank,
        rect=f.rect,
      )
      for f in fragments
    ]