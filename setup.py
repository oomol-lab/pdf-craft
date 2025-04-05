from setuptools import setup, find_packages

setup(
  name="pdf-craft",
  version="0.0.16",
  author="Tao Zeyu",
  author_email="i@taozeyu.com",
  url="https://github.com/oomol-lab/pdf-craft",
  description="PDF craft can convert PDF files into various other formats. This project will focus on processing PDF files of scanned books. The project has just started.",
  packages=find_packages(),
  long_description=open("./README.md", encoding="utf8").read(),
  long_description_content_type="text/markdown",
  include_package_data=True,
  classifiers=[
      'Development Status :: 2 - Pre-Alpha',
      'Intended Audience :: Developers',
      'License :: OSI Approved :: GNU Affero General Public License v3',
      'Programming Language :: Python',
      'Programming Language :: Python :: 3.10',
  ],
  package_data={
    "pdf_craft": ["data/**/*.jinja"],
  },
  install_requires=[
    "jinja2>=3.1.5,<4.0.0",
    "pyMuPDF>=1.25.3,<2.0.0",
    "alphabet-detector>=0.0.7,<1.0.0",
    "shapely>=2.0.6,<3.0.0",
    "tiktoken>=0.9.0,<1.0.0",
    "doc-page-extractor==0.0.8",
    "resource-segmentation==0.0.1",
    "langchain[openai]>=0.3.21,<0.4.0",
  ],
)