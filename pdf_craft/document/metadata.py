"""Standard book metadata snapshots shared by extraction and translation."""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

from .package import DocumentAuthor, DocumentMetadata
from ..runtime import invoke_callback


@dataclass(frozen=True)
class MetadataContent:
    """One finalized book view; original is present only for translation."""
    metadata: DocumentMetadata
    original: DocumentMetadata | None = None


MetadataCallback = Callable[[MetadataContent], object]


def metadata_from_mapping(value: Mapping[str, Any]) -> DocumentMetadata:
    """Detach manifest/overlay values, including legacy string author lists."""
    data = {field.name: value[field.name] for field in fields(DocumentMetadata)
            if field.name in value}
    data['authors'] = tuple(
        DocumentAuthor(author) if isinstance(author, str) else DocumentAuthor(**author)
        for author in value.get('authors', ())
    )
    for key in ('editors', 'translators', 'subjects'):
        data[key] = tuple(value.get(key, ()))
    modified = data.get('modified')
    if isinstance(modified, str):
        try:
            data['modified'] = datetime.fromisoformat(modified)
        except ValueError:
            data['modified'] = None
    return DocumentMetadata(**data)


async def notify_metadata_translation(source, overlay, callback: MetadataCallback | None) -> None:
    """Notify a complete replacement view, never a bilingual or partial overlay."""
    if callback is not None:
        await invoke_callback(callback, MetadataContent(
            metadata_from_mapping({**source, **overlay}), metadata_from_mapping(source),
        ))
