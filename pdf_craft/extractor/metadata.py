"""Best-effort extraction of bibliographic metadata from raw OCR pages."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..document import DocumentAuthor, DocumentMetadata
from ..llm import LLM, Message, MessageRole, runtime_for
from ..llm.guaranteed import GuaranteedOptions, request_guaranteed_json
from ..pdf import PDFDocumentMetadata


_INITIAL_PAGE_COUNT = 3
_MAX_PAGE_COUNT = 12
_MIN_MORE_PAGES = 2
_MAX_MORE_PAGES = 4
_PAGE_FILE_PATTERN = re.compile(r"page_(\d+)\.xml$")


@dataclass(frozen=True)
class _OCRPage:
    index: int
    element: ElementTree.Element
    text: str


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str
    page_index: int = Field(ge=1)
    evidence: str

    @field_validator("value", "evidence")
    @classmethod
    def _require_nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class _AuthorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: _Evidence
    original_name: _Evidence | None = None
    nationality: _Evidence | None = None


class _MetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: _Evidence | None = None
    original_title: _Evidence | None = None
    description: _Evidence | None = None
    publisher: _Evidence | None = None
    isbn: _Evidence | None = None
    authors: list[_AuthorResponse] = Field(default_factory=list)
    editors: list[_Evidence] = Field(default_factory=list)
    translators: list[_Evidence] = Field(default_factory=list)
    publication_date: _Evidence | None = None
    edition: _Evidence | None = None
    subjects: list[_Evidence] = Field(default_factory=list)
    rights: _Evidence | None = None
    language: _Evidence | None = None


class _TurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["read_more", "complete"]
    page_count: int | None = None
    metadata: _MetadataResponse | None = None

    @model_validator(mode="after")
    def _validate_action(self) -> "_TurnResponse":
        if self.action == "read_more":
            if self.metadata is not None:
                raise ValueError("read_more must not include metadata")
            if self.page_count is None:
                raise ValueError("read_more must include page_count")
            if not _MIN_MORE_PAGES <= self.page_count <= _MAX_MORE_PAGES:
                raise ValueError(
                    f"page_count must be between {_MIN_MORE_PAGES} and {_MAX_MORE_PAGES}"
                )
        elif self.page_count is not None or self.metadata is None:
            raise ValueError("complete must include metadata and no page_count")
        return self


def extract_book_metadata_from_ocr(pages_path: Path, metadata_llm: LLM) -> DocumentMetadata:
    """Read up to twelve front OCR pages through a bounded, repairable dialogue."""
    pages = _read_front_pages(pages_path)
    if not pages:
        return DocumentMetadata()

    loaded = pages[:_INITIAL_PAGE_COUNT]
    cursor = len(loaded)
    messages = [
        Message(MessageRole.SYSTEM, _SYSTEM_PROMPT),
        Message(MessageRole.USER, _render_pages_message(loaded, remaining=len(pages) - cursor)),
    ]
    runtime = runtime_for(metadata_llm, protocol_version="book-metadata-json-v1") \
        if isinstance(metadata_llm, LLM) else None

    while True:
        turn = _request_turn(
            metadata_llm,
            runtime,
            messages,
            loaded,
            available_page_count=len(pages),
        )
        messages.append(Message(
            MessageRole.ASSISTANT,
            turn.model_dump_json(exclude_none=True, ensure_ascii=False),
        ))
        if turn.action == "complete":
            assert turn.metadata is not None
            return _to_document_metadata(turn.metadata)

        assert turn.page_count is not None
        remaining = len(pages) - cursor
        assert remaining > 0  # _request_turn rejects read_more after the last physical OCR page.
        requested = min(turn.page_count, remaining)
        next_pages = pages[cursor:cursor + requested]
        loaded.extend(next_pages)
        cursor += len(next_pages)
        messages.append(Message(
            MessageRole.USER,
            _render_pages_message(
                next_pages,
                remaining=min(_MAX_PAGE_COUNT - len(loaded), len(pages) - cursor),
            ),
        ))


def merge_ocr_and_pdf_metadata(
    ocr_metadata: DocumentMetadata | None,
    pdf_metadata: PDFDocumentMetadata | None,
) -> DocumentMetadata:
    """Use native PDF fields only to fill fields missing from verified OCR."""
    ocr = ocr_metadata or DocumentMetadata()
    if pdf_metadata is None:
        return ocr
    return DocumentMetadata(
        title=ocr.title or _text_or_none(pdf_metadata.title),
        original_title=ocr.original_title,
        description=ocr.description or _text_or_none(pdf_metadata.description),
        publisher=ocr.publisher or _text_or_none(pdf_metadata.publisher),
        isbn=ocr.isbn or _text_or_none(pdf_metadata.isbn),
        authors=ocr.authors or tuple(
            DocumentAuthor(name=name) for name in _unique_nonempty(pdf_metadata.authors)
        ),
        editors=ocr.editors or tuple(_unique_nonempty(pdf_metadata.editors)),
        translators=ocr.translators or tuple(_unique_nonempty(pdf_metadata.translators)),
        publication_date=ocr.publication_date,
        edition=ocr.edition,
        subjects=ocr.subjects,
        rights=ocr.rights,
        language=ocr.language,
    )


def _request_turn(
    metadata_llm: LLM,
    runtime: Any,
    messages: list[Message],
    loaded_pages: list[_OCRPage],
    *,
    available_page_count: int,
) -> _TurnResponse:
    page_text = {page.index: page.text for page in loaded_pages}

    def parse(data: _TurnResponse, _index: int, _maximum: int) -> _TurnResponse:
        if data.action == "read_more":
            if len(loaded_pages) >= available_page_count:
                raise ValueError("all available OCR pages are already loaded; return action=complete")
            return data
        assert data.metadata is not None
        _validate_metadata_evidence(data.metadata, page_text)
        return data

    return request_guaranteed_json(GuaranteedOptions(
        messages=messages,
        request=lambda current, index, maximum: _request_llm(
            metadata_llm, runtime, current, index, maximum,
        ),
        schema=_TurnResponse,
        parse=parse,
        max_retries=3,
    ))


def _request_llm(metadata_llm: LLM, runtime: Any, messages, index: int, maximum: int) -> str:
    if runtime is not None:
        return runtime.request(messages, retry_index=index, retry_max=maximum, use_cache=False)
    return cast(Any, metadata_llm).request(input=messages)


def _validate_metadata_evidence(metadata: _MetadataResponse, page_text: dict[int, str]) -> None:
    for evidence in _iter_evidence(metadata):
        source = page_text.get(evidence.page_index)
        if source is None:
            raise ValueError(f"evidence references unloaded page {evidence.page_index}")
        printed_source = _printed_form(source)
        printed_evidence = _printed_form(evidence.evidence)
        printed_value = _printed_form(evidence.value)
        if printed_evidence not in printed_source:
            raise ValueError(f"evidence for {evidence.value!r} is not present on page {evidence.page_index}")
        if printed_value not in printed_evidence:
            raise ValueError(f"value {evidence.value!r} is not supported by its evidence")

    author_names = [_printed_form(author.name.value) for author in metadata.authors]
    if len(author_names) != len(set(author_names)):
        raise ValueError("authors must not repeat")
    for field_name, values in (
        ("editors", metadata.editors),
        ("translators", metadata.translators),
        ("subjects", metadata.subjects),
    ):
        printed = [_printed_form(value.value) for value in values]
        if len(printed) != len(set(printed)):
            raise ValueError(f"{field_name} must not repeat")
    if metadata.isbn is not None and not _looks_like_isbn(metadata.isbn.value):
        raise ValueError("isbn must be a valid ISBN-10 or ISBN-13 checksum")


def _iter_evidence(metadata: _MetadataResponse) -> Iterable[_Evidence]:
    for value in (
        metadata.title, metadata.original_title, metadata.description, metadata.publisher,
        metadata.isbn, metadata.publication_date, metadata.edition, metadata.rights,
        metadata.language,
    ):
        if value is not None:
            yield value
    for author in metadata.authors:
        yield author.name
        if author.original_name is not None:
            yield author.original_name
        if author.nationality is not None:
            yield author.nationality
    yield from metadata.editors
    yield from metadata.translators
    yield from metadata.subjects


def _to_document_metadata(metadata: _MetadataResponse) -> DocumentMetadata:
    return DocumentMetadata(
        title=_value(metadata.title),
        original_title=_value(metadata.original_title),
        description=_value(metadata.description),
        publisher=_value(metadata.publisher),
        isbn=_value(metadata.isbn),
        authors=tuple(DocumentAuthor(
            name=author.name.value,
            original_name=_value(author.original_name),
            nationality=_value(author.nationality),
        ) for author in metadata.authors),
        editors=tuple(value.value for value in metadata.editors),
        translators=tuple(value.value for value in metadata.translators),
        publication_date=_value(metadata.publication_date),
        edition=_value(metadata.edition),
        subjects=tuple(value.value for value in metadata.subjects),
        rights=_value(metadata.rights),
        language=_value(metadata.language),
    )


def _read_front_pages(pages_path: Path) -> list[_OCRPage]:
    numbered_paths: list[tuple[int, Path]] = []
    for path in pages_path.glob("page_*.xml"):
        match = _PAGE_FILE_PATTERN.fullmatch(path.name)
        if match is not None:
            numbered_paths.append((int(match.group(1)), path))
    pages: list[_OCRPage] = []
    for index, path in sorted(numbered_paths)[:_MAX_PAGE_COUNT]:
        element = ElementTree.parse(path).getroot()
        text = "\n".join(
            layout.text.strip() for layout in element.findall(".//layout")
            if layout.text and layout.text.strip()
        )
        pages.append(_OCRPage(index=index, element=element, text=text))
    return pages


def _render_pages_message(pages: list[_OCRPage], *, remaining: int) -> str:
    root = ElementTree.Element("ocr-pages")
    for page in pages:
        root.append(page.element)
    pages_xml = ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)
    return (
        "These are raw OCR page XML records from the front of one book. "
        f"At most {remaining} more page(s) may be supplied after this message.\n"
        f"{pages_xml}"
    )


def _printed_form(value: str) -> str:
    """Allow only Unicode canonical equivalence, never editorial rewriting."""
    return unicodedata.normalize("NFC", value)


def _normalize_native_text(value: str) -> str:
    """Deduplicate untrusted PDF Info fallback fields without affecting OCR proof."""
    return re.sub(r"\s+", "", value).casefold()


def _looks_like_isbn(value: str) -> bool:
    """Validate a printed ISBN after removing only conventional separators."""
    compact = re.sub(r"[\s-]+", "", value)
    if re.fullmatch(r"\d{13}", compact):
        weighted_sum = sum(
            int(character) * (1 if index % 2 == 0 else 3)
            for index, character in enumerate(compact)
        )
        return weighted_sum % 10 == 0
    if re.fullmatch(r"\d{9}[\dXx]", compact):
        weighted_sum = sum(
            (10 - index) * (10 if character in "Xx" else int(character))
            for index, character in enumerate(compact)
        )
        return weighted_sum % 11 == 0
    return False


def _value(value: _Evidence | None) -> str | None:
    return value.value if value is not None else None


def _text_or_none(value: str | None) -> str | None:
    value = value.strip() if value else ""
    return value or None


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        normalized = _normalize_native_text(value)
        if value and normalized not in seen:
            result.append(value)
            seen.add(normalized)
    return result


_SYSTEM_PROMPT = """You extract bibliographic facts from raw OCR pages of a book.

Return complete JSON only. Your response must be either:
{"action":"read_more","page_count":2}
or:
{"action":"complete","metadata":{...}}

`read_more.page_count` must be an integer from 2 through 4. You may request more front pages
conservatively, without explaining why, until no more pages are available or the twelve-page
limit is reached.

For `complete`, metadata may contain only these fields: title, original_title, description,
publisher, isbn, authors, editors, translators, publication_date, edition, subjects, rights,
and language. A scalar field is either null or {"value", "page_index", "evidence"}. A list
field is a list of those evidence objects. Each author is
{"name": evidence, "original_name": evidence-or-null, "nationality": evidence-or-null}.

Use only facts explicitly present in the supplied OCR pages. Do not infer, translate, correct
from world knowledge, or invent any field. Names, titles, publishers, and ISBNs must preserve
the printed form. `description` is allowed only for an explicitly printed blurb or description;
do not summarize the book. Set `language` only when a literal language identifier such as `en` or
`zh` is printed; do not turn a printed language name such as `English` into a code. Every non-null
value must cite the page containing it and a literal evidence string that contains the value.
Transcribe capitalization and internal whitespace exactly as printed. Use null or [] when no
evidence exists."""
