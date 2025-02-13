import pdfplumber

from doc_page_extractor import DocExtractor, LayoutClass


class PDF:
  def __init__(self, model_dir_path: str):
    self._model_dir_path: str = model_dir_path

  def extract(self, pdf_path: str):
    extractor = DocExtractor(self._model_dir_path, order_by_layoutreader=False)
    lines: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
      for page in pdf.pages:
        image = page.to_image().annotated
        result = extractor.extract(image, "ch")
        for layout in result.layouts:
          if layout.cls == LayoutClass.TITLE:
            lines.append("# " + "".join([fragment.text for fragment in layout.fragments]))
          elif layout.cls == LayoutClass.PLAIN_TEXT:
            lines.append("".join([fragment.text for fragment in layout.fragments]))

    for line in lines:
      print(line)