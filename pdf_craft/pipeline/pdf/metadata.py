"""Map normalized PCEX document metadata onto PDF document information."""

from collections.abc import Mapping
import json
from typing import Any


_PCEX_SNAPSHOT_KEY = "/PDFCraftDocumentMetadata"


def pdf_document_metadata(document: Mapping[str, Any]) -> dict[str, str]:
    """Return PDF document-information values for populated PCEX metadata.

    PDF's standard Info dictionary cannot represent every PCEX field.  The
    complete normalized document object is therefore retained as UTF-8 JSON in
    one stable private key, while the ordinary reader-visible title, author,
    subject, and keywords fields retain their standard meanings.
    """
    if not any(_has_value(value) for value in document.values()):
        return {}

    result = {
        _PCEX_SNAPSHOT_KEY: json.dumps(
            dict(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ),
    }
    _set_string(result, "/Title", document.get("title"))
    _set_string(result, "/Subject", document.get("description"))
    _set_strings(result, "/Author", document.get("authors"), item_key="name")
    _set_strings(result, "/Keywords", document.get("subjects"))
    return result


def _has_value(value: Any) -> bool:
    return value not in (None, "", (), [], {})


def _set_string(result: dict[str, str], key: str, value: Any) -> None:
    if isinstance(value, str) and value:
        result[key] = value


def _set_strings(
    result: dict[str, str], key: str, value: Any, *, item_key: str | None = None,
) -> None:
    if not isinstance(value, list | tuple):
        return
    if item_key is None:
        values = [item for item in value if isinstance(item, str) and item]
    else:
        values = [
            item[item_key]
            for item in value
            if isinstance(item, dict) and isinstance(item.get(item_key), str) and item[item_key]
        ]
    if values:
        result[key] = "; ".join(values)
