from typing import Literal
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL.Image import Image
from fitz import Document
from doc_page_extractor import clip, Rectangle, Layout, LayoutClass, OCRFragment, ExtractedResult
from .types import OCRLevel, PDFPageExtractorProgressReport
from .document import DocumentExtractor, DocumentParams


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
class BasicBlock:
  rect: Rectangle
  texts: list[Text]
  font_size: float

@dataclass
class TextBlock(BasicBlock):
  kind: TextKind
  has_paragraph_indentation: bool = False
  last_line_touch_end: bool = False

class AssetKind(Enum):
  FIGURE = 0
  TABLE = 1
  FORMULA = 2

@dataclass
class AssetBlock(BasicBlock):
  image: Image
  kind: AssetKind

Block = TextBlock | AssetBlock

class PDFPageExtractor:
  def __init__(
      self,
      device: Literal["cpu", "cuda"],
      model_dir_path: str,
      ocr_level: OCRLevel = OCRLevel.Once,
      debug_dir_path: str | None = None,
    ):
    self._doc_extractor: DocumentExtractor = DocumentExtractor(
      device=device,
      ocr_level=ocr_level,
      model_dir_path=model_dir_path,
      debug_dir_path=debug_dir_path,
    )

  def extract(self, pdf: str | Document, report_progress: PDFPageExtractorProgressReport | None = None) -> Generator[Block, None, None]:
    for _, blocks, _ in self.extract_enumerated_blocks_and_image(
      pdf=pdf,
      report_progress=report_progress,
    ):
      yield from blocks

  def extract_enumerated_blocks_and_image(
      self,
      pdf: str | Document,
      page_indexes: Iterable[int] | None = None,
      report_progress: PDFPageExtractorProgressReport | None = None,

    ) -> Generator[tuple[int, list[Block], Image], None, None]:

    for page_index, result, layouts in self._doc_extractor.extract(DocumentParams(
      pdf=pdf,
      page_indexes=page_indexes,
      report_progress=report_progress,
    )):
      blocks = self._convert_to_blocks(result, layouts)
      page_range = self._texts_range(blocks)

      for block in blocks:
        if not isinstance(block, TextBlock) or \
           block.kind == TextKind.ABANDON:
          continue

        if len(block.texts) == 1:
          mean_line_height, x1, x2 = page_range
        else:
          mean_line_height, x1, x2 = self._texts_range((block,))

        first_text = block.texts[0]
        last_text = block.texts[-1]
        first_delta_x = (first_text.rect.lt[0] + first_text.rect.lb[0]) / 2 - x1
        last_delta_x = x2 - (last_text.rect.rt[0] + last_text.rect.rb[0]) / 2
        block.has_paragraph_indentation = first_delta_x > mean_line_height
        block.last_line_touch_end = last_delta_x < mean_line_height

      yield page_index, blocks, result.extracted_image

  def _convert_to_blocks(self, result: ExtractedResult, layouts: list[Layout]) -> list[Block]:
    store: list[tuple[Layout, Block]] = []
    def previous_block(cls: LayoutClass) -> Block | None:
      for i in range(len(store) - 1, -1, -1):
        layout, block = store[i]
        if cls == layout.cls:
          return block
        if cls != LayoutClass.ABANDON:
          return None
      return None

    for layout in layouts:
      cls = layout.cls
      if cls == LayoutClass.TITLE:
        store.append((layout, TextBlock(
          rect=layout.rect,
          kind=TextKind.TITLE,
          font_size=0.0,
          texts=self._convert_to_text(layout.fragments),
        )))
      elif cls == LayoutClass.PLAIN_TEXT:
        store.append((layout, TextBlock(
          rect=layout.rect,
          kind=TextKind.PLAIN_TEXT,
          font_size=0.0,
          texts=self._convert_to_text(layout.fragments),
        )))
      elif cls == LayoutClass.ABANDON:
        store.append((layout, TextBlock(
          rect=layout.rect,
          kind=TextKind.ABANDON,
          font_size=0.0,
          texts=self._convert_to_text(layout.fragments),
        )))
      elif cls == LayoutClass.FIGURE:
        store.append((layout, AssetBlock(
          rect=layout.rect,
          texts=[],
          kind=AssetKind.FIGURE,
          font_size=0.0,
          image=clip(result, layout),
        )))
      elif cls == LayoutClass.TABLE:
        store.append((layout, AssetBlock(
          rect=layout.rect,
          texts=[],
          kind=AssetKind.TABLE,
          font_size=0.0,
          image=clip(result, layout),
        )))
      elif cls == LayoutClass.ISOLATE_FORMULA:
        store.append((layout, AssetBlock(
          rect=layout.rect,
          texts=[],
          kind=AssetKind.FORMULA,
          font_size=0.0,
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

    self._fill_font_size_for_blocks(store)

    return [block for _, block in store]

  def _texts_range(self, blocks: Iterable[Block]) -> tuple[float, float, float]:
    sum_lines_height: float = 0.0
    texts_count: int = 0
    x1: float = float("inf")
    x2: float = float("-inf")

    for block in blocks:
      if not isinstance(block, TextBlock):
        continue
      if block.kind == TextKind.ABANDON:
        continue
      for text in block.texts:
        sum_lines_height += text.rect.size[1]
        texts_count += 1
        for x, _ in text.rect:
          x1 = min(x1, x)
          x2 = max(x2, x)

    if texts_count == 0:
      return 0.0, 0.0, 0.0
    return sum_lines_height / texts_count, x1, x2

  def _convert_to_text(self, fragments: list[OCRFragment]) -> list[Text]:
    return [
      Text(
        content=f.text,
        rank=f.rank,
        rect=f.rect,
      )
      for f in fragments
    ]

  def _fill_font_size_for_blocks(self, store: list[tuple[Layout, Block]]):
    font_sizes: list[float] = []

    for layout, _ in store:
      if len(layout.fragments) == 0:
        font_sizes.append(0.0)
      else:
        sum_height: float = 0.0
        for fragment in layout.fragments:
          sum_height += fragment.rect.size[1]

        # without considering vertical writing, the height of a line of text is proportional to the font size.
        font_size = sum_height / len(layout.fragments)
        font_sizes.append(font_size)

    max_font_size: float = 1.0
    min_font_size: float = 1.0

    if len(font_sizes) > 0:
      max_font_size = max(font_sizes)
      min_font_size = min(font_sizes)

    if max_font_size == min_font_size:
      for _, block in store:
        block.font_size = 0
    else:
      for font_size, (_, block) in zip(font_sizes, store):
        block.font_size = (font_size - min_font_size) / (max_font_size - min_font_size)
