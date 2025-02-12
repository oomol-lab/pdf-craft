import os
from pdf_craft.pdf import PDF

def main():
  model_dir_path = os.path.join(__file__, "..", "./models")
  model_dir_path = os.path.abspath(model_dir_path)

  PDF(model_dir_path).extract(
    "/Users/taozeyu/Downloads/中国古代练丹家的目的.pdf",
  )

if __name__ == "__main__":
  main()