"""Transformers operating on render-ready document packages."""

from collections.abc import Callable
from pathlib import Path
from shutil import copy2, copytree
from typing import Protocol
from xml.etree.ElementTree import Element

from pdf_craft.common.xml import read_xml, save_xml
from pdf_craft.document import DocumentPackage
from pdf_craft.sequence.chapter import decode, encode
from pdf_craft.transformer.protocol import ChapterTransformer
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind


class PackageTransformer(Protocol):
    """A format-neutral transformation from one package to another."""

    def transform(self, package: DocumentPackage, output_path: Path) -> DocumentPackage: ...


class ChapterPackageTransformer:
    """Copy a package and transform its chapter XML files independently."""

    def __init__(
        self,
        chapter_transformer: ChapterTransformer,
        *,
        mode: SubmitKind = SubmitKind.REPLACE,
        toc_transformer: Callable[[Element], Element] | None = None,
    ) -> None:
        if mode != SubmitKind.REPLACE and hasattr(chapter_transformer, "with_mode"):
            chapter_transformer = getattr(chapter_transformer, "with_mode")(mode)
        self.chapter_transformer = chapter_transformer
        self.mode = mode
        self.toc_transformer = toc_transformer

    def transform(self, package: DocumentPackage, output_path: Path) -> DocumentPackage:
        package.validate()
        if output_path.exists():
            raise FileExistsError(f"output package already exists: {output_path}")
        output_path.mkdir(parents=True)
        copytree(package.chapters_path, output_path / "chapters")
        copytree(package.assets_path, output_path / "assets")
        for source in (package.toc_path, package.cover_path, package.metadata_path):
            if source is not None and source.exists():
                copy2(source, output_path / source.name)

        for path in sorted((output_path / "chapters").glob("chapter*.xml")):
            chapter = decode(read_xml(path))
            transformed = self.chapter_transformer.transform(chapter)
            save_xml(encode(transformed), path)

        if self.toc_transformer is not None and package.toc_path is not None and package.toc_path.exists():
            save_xml(self.toc_transformer(read_xml(output_path / package.toc_path.name)), output_path / package.toc_path.name)
        return DocumentPackage.from_path(output_path).validate()
