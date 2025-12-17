from dataclasses import dataclass
from xml.etree.ElementTree import Element

from ...common import indent


@dataclass
class PageRef:
    page_index: int
    score: float
    matched_titles: list["MatchedTitle"]


@dataclass
class MatchedTitle:
    text: str
    score: float
    references: list["TitleReference"]


@dataclass
class TitleReference:
    page_index: int
    order: int


def encode(page_refs: list[PageRef]) -> Element:
    root = Element("toc-range")
    for page_ref in page_refs:
        page_ref_elem = Element("page-ref")
        page_ref_elem.set("page-index", str(page_ref.page_index))
        page_ref_elem.set("score", str(page_ref.score))

        for matched_title in page_ref.matched_titles:
            matched_title_elem = Element("matched-title")
            matched_title_elem.set("score", str(matched_title.score))

            text_elem = Element("text")
            text_elem.text = matched_title.text
            matched_title_elem.append(text_elem)

            for reference in matched_title.references:
                reference_elem = Element("reference")
                reference_elem.set("page-index", str(reference.page_index))
                reference_elem.set("order", str(reference.order))
                matched_title_elem.append(reference_elem)

            page_ref_elem.append(matched_title_elem)

        root.append(page_ref_elem)

    return indent(root)

def decode(element: Element) -> list[PageRef]:
    if element.tag != "toc-range":
        raise ValueError(f"Expected root element 'toc-range', got '{element.tag}'")

    page_refs = []
    for page_ref_elem in element.findall("page-ref"):
        page_index_str = page_ref_elem.get("page-index")
        score_str = page_ref_elem.get("score")
        if page_index_str is None or score_str is None:
            raise ValueError("Missing required attributes on page-ref element")

        page_index = int(page_index_str)
        score = float(score_str)

        matched_titles = []
        for matched_title_elem in page_ref_elem.findall("matched-title"):
            text_elem = matched_title_elem.find("text")
            title_score_str = matched_title_elem.get("score")
            if text_elem is None or text_elem.text is None or title_score_str is None:
                raise ValueError("Missing required elements/attributes on matched-title element")

            text = text_elem.text
            title_score = float(title_score_str)

            references = []
            for reference_elem in matched_title_elem.findall("reference"):
                ref_page_index_str = reference_elem.get("page-index")
                order_str = reference_elem.get("order")
                if ref_page_index_str is None or order_str is None:
                    raise ValueError("Missing required attributes on reference element")

                ref_page_index = int(ref_page_index_str)
                order = int(order_str)
                references.append(TitleReference(ref_page_index, order))

            matched_titles.append(MatchedTitle(text, title_score, references))

        page_refs.append(PageRef(page_index, score, matched_titles))

    return page_refs
