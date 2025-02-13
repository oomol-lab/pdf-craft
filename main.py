import os

from pdf_craft.pdf import stream_pdf, Text, TextKind
from doc_page_extractor import DocExtractor

def main():
  pdf_file = "/Users/taozeyu/Downloads/中国古代练丹家的目的.pdf"
  model_dir_path = os.path.join(__file__, "..", "./models")
  model_dir_path = os.path.abspath(model_dir_path)
  extractor = DocExtractor(model_dir_path, order_by_layoutreader=False)
  lines: list[str] = []

  for item in stream_pdf(extractor, pdf_file):
    if not isinstance(item, Text):
      continue
    if item.kind == TextKind.TITLE:
      lines.append("# " + item.text)
    elif item.kind == TextKind.PLAIN_TEXT:
      lines.append(item.text)

  for line in lines:
    print(line)
    print("")

if __name__ == "__main__":
  main()