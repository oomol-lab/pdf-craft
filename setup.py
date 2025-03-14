from setuptools import setup, find_packages

setup(
  name="pdf-craft",
  version="0.0.3",
  author="Tao Zeyu",
  author_email="i@taozeyu.com",
  url="https://github.com/oomol-lab/pdf-craft",
  description="PDF craft can convert PDF files into various other formats. This project will focus on processing PDF files of scanned books. The project has just started.",
  packages=find_packages(),
  long_description=open("./README.md", encoding="utf8").read(),
  long_description_content_type="text/markdown",
  install_requires=[
    "tqdm>=4.66.5,<5.0.0",
    "tiktoken>=0.9.0,<1.0.0",
    "jinja2>=3.1.5,<4.0.0",
    "pyMuPDF>=1.25.3,<2.0.0",
    "alphabet-detector>=0.0.7,<1.0.0",
    "shapely>=2.0.6,<3.0.0",
    "tiktoken>=0.9.0,<1.0.0",
    "doc-page-extractor==0.0.6",
    "langchain>=0.3.17,<0.4.0",
    "langchain_community>=0.3.16,<0.4.0",
    "langchain_core>=0.3.35,<0.4.0",
    "langchain_openai>=0.3.6,<0.4.0",
    "langchain_anthropic>=0.3.7,<0.4.0",
  ],
)