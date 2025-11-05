from pathlib import Path

from ..xml import save_xml

from ..pdf import PagesReader
from .jointer import TITLE_TAGS, Jointer
from .chapter import encode, Chapter, ParagraphLayout


def generate_chapter_files(pages_path: Path, chapters_path: Path):
    chapters_path.mkdir(parents=True, exist_ok=True)
    for chapter_file in chapters_path.glob("chapter_*.xml"):
        chapter_file.unlink()

    for i, chapter in enumerate(_generate_chapters(pages_path)):
        chapter_file = chapters_path / f"chapter_{i}.xml"
        chapter_element = encode(chapter)
        save_xml(chapter_element, chapter_file)

def _generate_chapters(pages_path: Path):
    pages_reader = PagesReader(pages_path)
    chapter: Chapter | None = None

    for layout in Jointer().execute(
        layouts_in_page=((p.index, p.body_layouts) for p in pages_reader.read())
    ):
        if isinstance(layout, ParagraphLayout) and layout.ref in TITLE_TAGS:
            if chapter:
                yield chapter
            chapter = Chapter(title=layout, layouts=[])
        else:
            if chapter is None:
                chapter = Chapter(title=None, layouts=[])
            chapter.layouts.append(layout)

    if chapter:
        yield chapter