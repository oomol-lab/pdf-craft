# pylint: disable=protected-access

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import cast

from pdf_craft.common import read_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, DisplayFormula, SourceAsset, SourceTextFragment, StandaloneAsset,
    TextFlowItem,
)
from pdf_craft.extractor.chapter.chapter import InlineExpression, Reference
from pdf_craft.extractor.chapter.reader import create_chapters_reader
from pdf_craft.extractor.chapter.text_projection import (
    iter_continuous_content,
    iter_continuous_fragments,
)
from pdf_craft.markdown.paragraph import HTMLTag, flatten
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.error import IgnoreFillErrorsChecker
from pdf_craft.pipeline.pdf.models import PDFInlineFormula, PDFReplacement, PDFReplacementRegion
from pdf_craft.pipeline.pdf.patcher import PDFPatcher
from pdf_craft.transformer.translation_coverage import paragraph_identity, read_coverage


_INLINE_FORMULA_MARKER = "\ufffc"


class PDFTranslationPipeline:
    """Patch explicitly translated PCEX content back into its source PDF."""

    def __init__(self, pdf_handler: PDFHandler | None = None, patcher: PDFPatcher | None = None, dpi: int = 300) -> None:
        self.patcher = patcher or PDFPatcher(pdf_handler=pdf_handler, dpi=dpi)

    def patch(
        self,
        pdf_path: Path,
        target_path: Path,
        extraction: PDFCraftExtraction | Path,
        *,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        """Write text already present in ``extraction`` back to ``pdf_path``.

        This is deliberately separate from :meth:`translate`: the extraction is
        already the source of the replacement text, so no OCR or LLM
        transformer is involved.
        """
        extraction = _ensure_extraction(extraction)
        extraction.validate()
        pages = extraction.page_pixel_sizes()
        render_dpi = extraction.render_dpi()
        with extraction._materialize() as paths:
            chapters = tuple(create_chapters_reader(paths.chapters)())
            coverage = read_coverage(paths.translation)

            def replacements() -> Iterator[PDFReplacement]:
                yield from self._iter_covered_replacements(
                    chapters, paths.furnitures, coverage, pages, render_dpi,
                    ignore_errors=ignore_errors,
                )

            self._patch_replacements(pdf_path, target_path, replacements(), ignore_errors)

    def _iter_covered_replacements(
        self,
        chapters: tuple[Chapter, ...],
        furnitures_path: Path,
        coverage,
        pages,
        render_dpi: int,
        *,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> Iterator[PDFReplacement]:
        """Create a patch plan solely from explicit translation coverage.

        A pcex is intentionally valid without a sidecar; in that case every
        Narrative/Furniture unit remains untouched.  On a partially changed
        page those untouched rectangles become layout obstacles so translated
        text cannot drift into them.
        """
        obstacles: list[PDFReplacementRegion] = []
        translated_narrative: list[tuple[TextFlowItem, tuple[PDFReplacementRegion, ...]]] = []
        for chapter in chapters:
            obstacles.extend(_chapter_obstacle_regions(
                chapter, pages, render_dpi, ignore_errors=ignore_errors,
            ))
            for layout in chapter.flow_items:
                if not isinstance(layout, TextFlowItem) or layout.role not in {"body", "heading"}:
                    continue
                identity = paragraph_identity(chapter, layout)
                if identity is None:
                    continue
                fragments = list(iter_continuous_fragments(layout))
                regions = _regions_for_fragments(fragments, pages, render_dpi, ignore_errors)
                if not regions:
                    continue
                if coverage.narrative.get(identity) == "translated":
                    translated_narrative.append((layout, regions))
                else:
                    obstacles.extend(regions)

        translated_furniture: list[tuple[str, tuple[PDFReplacementRegion, ...]]] = []
        if furnitures_path.exists():
            furniture = read_xml(furnitures_path)
            positions = {
                (pattern.get("id", ""), position.get("id", "")): position
                for pattern in furniture.findall("patterns/pattern")
                for position in pattern.findall("position")
            }
            for page in furniture.findall("pages/page"):
                page_index = int(page.get("index", "0"))
                for section in page.findall("section"):
                    det = section.get("det", "")
                    associations = section.findall("association")
                    if associations:
                        content = _translated_association_content(
                            associations, positions, coverage.positions, page_index,
                        )
                        state = "translated" if content is not None else "preserved"
                    else:
                        state = coverage.sections.get((page_index, det))
                        content = section.text or ""
                    region = _region_for_box(page_index, _parse_det(det), pages, render_dpi, 0, ignore_errors)
                    if region is None:
                        continue
                    if state == "translated" and content is not None and content.strip():
                        translated_furniture.append((content.strip(), (region,)))
                    else:
                        obstacles.append(region)

        shared_obstacles = _unique_regions(obstacles)
        replacements: list[PDFReplacement] = []
        for layout, regions in translated_narrative:
            patch_text, inline_formulas = _to_pdf_patch_content(
                iter_continuous_content(layout)
            )
            patch_text = patch_text.strip()
            if not patch_text:
                continue
            first = regions[0]
            replacements.append(PDFReplacement(
                first.page_index, first.bbox, patch_text, first.page_pixel_size, first.dpi,
                reading_order=first.reading_order, regions=regions,
                layout_ref=layout.role, layout_level=layout.level,
                inline_formulas=inline_formulas, obstacle_regions=shared_obstacles,
            ))
        for content, regions in translated_furniture:
            first = regions[0]
            replacements.append(PDFReplacement(
                first.page_index, first.bbox, content, first.page_pixel_size, first.dpi,
                reading_order=first.reading_order, regions=regions,
                layout_ref="furniture", layout_level=0,
                obstacle_regions=shared_obstacles,
            ))

        # The window planner consumes one unified source-page stream.  The
        # translated pcex keeps NarrativeFlow and PageFurniture in separate
        # channels, so concatenating their plans would put early-page
        # furniture after late-page narrative and invalidate that stream.
        yield from sorted(replacements, key=_replacement_source_order)

    def _patch_replacements(
        self,
        pdf_path: Path,
        target_path: Path,
        replacements: Iterator[PDFReplacement],
        ignore_errors: IgnoreFillErrorsChecker,
    ) -> None:
        """Keep existing custom patchers compatible until they opt into recovery."""
        if _ignore_errors_enabled(ignore_errors):
            self.patcher.patch(
                pdf_path, target_path, replacements, ignore_errors=ignore_errors,
            )
            return
        self.patcher.patch(pdf_path, target_path, replacements)

def _regions_for_fragments(fragments: Iterable[SourceTextFragment], pages, render_dpi: int, ignore_errors) -> tuple[PDFReplacementRegion, ...]:
    regions: list[PDFReplacementRegion] = []
    for fragment in fragments:
        region = _region_for_box(
            fragment.page_index, fragment.bbox, pages, render_dpi, fragment.source_order, ignore_errors,
        )
        if region is not None:
            regions.append(region)
    return tuple(regions)


def _region_for_box(
    page_index: int,
    det: tuple[int, int, int, int],
    pages,
    render_dpi: int,
    reading_order: int,
    ignore_errors,
) -> PDFReplacementRegion | None:
    if page_index not in pages:
        error = ValueError(f"PDFCraftExtraction pages.xml is missing page {page_index}")
        if not _check_ignore_error(ignore_errors, error):
            raise error
        return None
    return PDFReplacementRegion(page_index, det, pages[page_index], render_dpi, reading_order)


def _parse_det(raw: str) -> tuple[int, int, int, int]:
    try:
        values = tuple(int(value) for value in raw.split(","))
    except ValueError as error:
        raise ValueError(f"invalid furniture section bbox: {raw}") from error
    if len(values) != 4:
        raise ValueError(f"invalid furniture section bbox: {raw}")
    return cast(tuple[int, int, int, int], values)


def _unique_regions(regions: list[PDFReplacementRegion]) -> tuple[PDFReplacementRegion, ...]:
    seen: set[tuple[int, tuple[int, int, int, int], int]] = set()
    result: list[PDFReplacementRegion] = []
    for region in regions:
        key = (region.page_index, region.bbox, region.reading_order)
        if key not in seen:
            seen.add(key)
            result.append(region)
    return tuple(result)


def _replacement_source_order(replacement: PDFReplacement) -> tuple[int, int, int, int]:
    """Order a unified replacement plan by its first source rectangle."""
    first = replacement.source_regions()[0]
    return first.page_index, first.reading_order, first.bbox[1], first.bbox[0]


def _translated_association_content(
    associations, positions, coverage, page_index: int,
) -> str | None:
    """Resolve a physical Section from every translated Pattern Position.

    Universal and SameSide patterns can both link to one physical section.
    The section has only one drawable rectangle, so equal translated content is
    safely deduplicated.  Differing translated values have no source-level
    precedence; preserving the original section is safer than choosing one
    pattern arbitrarily.
    """
    contents = {
        content
        for association in associations
        if coverage.get((association.get("pattern_id", ""), association.get("position_id", ""))) == "translated"
        for content in (_association_content(
            positions.get((association.get("pattern_id", ""), association.get("position_id", ""))),
            page_index,
        ),)
        if content is not None and content.strip()
    }
    contents.discard("")
    return contents.pop() if len(contents) == 1 else None


def _association_content(position, page_index: int) -> str | None:
    if position is None:
        return None
    style = position.get("folio_style")
    if style is None:
        return position.text or ""
    try:
        offset = int(position.get("folio_offset", ""))
    except ValueError:
        return None
    value = _format_folio(style, page_index + offset)
    if value is None:
        return None
    return position.get("folio_prefix", "") + value + position.get("folio_suffix", "")


def _format_folio(style: str, value: int) -> str | None:
    if value <= 0:
        return None
    if style == "D":
        return str(value)
    if style in {"R", "r"}:
        result = _roman_folio(value)
        return result if style == "R" else result.lower()
    if style in {"A", "a"}:
        result = _alphabetic_folio(value)
        return result if style == "A" else result.lower()
    return None


def _roman_folio(value: int) -> str:
    units = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    parts: list[str] = []
    for unit, text in units:
        count, value = divmod(value, unit)
        parts.append(text * count)
    return "".join(parts)


def _alphabetic_folio(value: int) -> str:
    result: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        result.append(chr(ord("A") + remainder))
    return "".join(reversed(result))


def _check_ignore_error(checker: IgnoreFillErrorsChecker, error: Exception) -> bool:
    """Match PDF patcher's bool-or-callable page-fill error policy."""
    return checker(error) if callable(checker) else checker


def _ignore_errors_enabled(checker: IgnoreFillErrorsChecker) -> bool:
    """A callable policy needs an error instance before it can opt in."""
    return checker is True or callable(checker)


def _to_pdf_patch_content(items) -> tuple[str, tuple[PDFInlineFormula, ...]]:
    """Return visible text with structural markers for embedded formulas.

    XML translation intentionally keeps :class:`InlineExpression` nodes in
    the translated Chapter.  Do not serialize them back to delimiter-wrapped
    LaTeX here: the PDF filler can then render a vector atom when local TeX is
    available, or use its plain-text fallback when it is not.
    """
    parts: list[str] = []
    formulas: list[PDFInlineFormula] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, InlineExpression):
            parts.append(_INLINE_FORMULA_MARKER)
            formulas.append(PDFInlineFormula(item.content.strip()))
        elif isinstance(item, Reference):
            parts.append(str(item.mark))
        elif isinstance(item, HTMLTag):
            children, child_formulas = _to_pdf_patch_content(item.children)
            parts.append(children)
            formulas.extend(child_formulas)
        else:
            raise TypeError(f"unsupported chapter content for PDF patching: {type(item).__name__}")
    return "".join(parts), tuple(formulas)


def _chapter_obstacle_regions(
    chapter: Chapter,
    pages,
    render_dpi: int,
    *,
    ignore_errors: IgnoreFillErrorsChecker = False,
) -> tuple[PDFReplacementRegion, ...]:
    """Collect non-flow geometry that may stop text from extending downward.

    Top-level text fragments are already represented by the replacement
    regions passed to the window planner. Standalone assets, display formulas,
    and reference layouts are not: references (notably footnotes) live beneath
    ``Reference.flow_items`` rather than in ``Chapter.flow_items``. Their block
    and asset rectangles are therefore explicit obstacles even when the
    reference itself is not being translated. Image/table assets nested in a
    TextFlowItem are deliberately excluded: they are reading-order anchors for
    reflow renderers, not PDF text-layout boundaries.
    """
    regions: list[PDFReplacementRegion] = []
    seen_regions: set[tuple[int, tuple[int, int, int, int]]] = set()
    seen_references: set[tuple[int, int]] = set()

    def add_region(page_index: int, det: tuple[int, int, int, int]) -> None:
        if page_index not in pages:
            error = ValueError(f"PDFCraftExtraction pages.xml is missing page {page_index}")
            if not _check_ignore_error(ignore_errors, error):
                raise error
            return
        key = (page_index, det)
        if key not in seen_regions:
            seen_regions.add(key)
            regions.append(PDFReplacementRegion(page_index, det, pages[page_index], render_dpi))

    def visit_asset(asset: SourceAsset) -> None:
        add_region(asset.page_index, asset.bbox)
        visit_references(asset.title)
        visit_references(asset.content)
        visit_references(asset.caption)

    def visit_text(text: TextFlowItem, *, obstacle: bool = False) -> None:
        for child in text.children:
            if isinstance(child, SourceAsset):
                # A source asset nested in a TextFlowItem is an EPUB/Markdown
                # reading-order anchor, not a PDF paragraph-layout boundary.
                # The PDF fitter consumes every text fragment in this flow as
                # one continuous bbox chain.  Its source rectangles already
                # describe the writable area around the original visual asset;
                # adding the asset itself as a lower obstacle would make its
                # aim/forbidden-line fitting artificially shrink or split the
                # paragraph.  References in an asset's extracted text still
                # own independent physical geometry and must remain protected.
                visit_references(child.title)
                visit_references(child.content)
                visit_references(child.caption)
            else:
                if obstacle:
                    add_region(child.page_index, child.bbox)
                visit_references(child.content)

    def visit_flow_item(item: TextFlowItem | DisplayFormula | StandaloneAsset, *, text_obstacle: bool = False) -> None:
        if isinstance(item, TextFlowItem):
            visit_text(item, obstacle=text_obstacle)
        else:
            visit_asset(item.asset)

    def visit_reference(reference: Reference) -> None:
        if reference.id in seen_references:
            return
        seen_references.add(reference.id)
        for item in reference.flow_items:
            visit_flow_item(item, text_obstacle=True)

    def visit_references(items) -> None:
        for item in flatten(items):
            if isinstance(item, Reference):
                visit_reference(item)

    for item in chapter.flow_items:
        visit_flow_item(item)

    return tuple(regions)


def _ensure_extraction(value: PDFCraftExtraction | Path) -> PDFCraftExtraction:
    if isinstance(value, PDFCraftExtraction):
        return value
    return PDFCraftExtraction.open(value)
