"""Bound serialized PDF resources before they enter the final document."""
# pylint: disable=protected-access
from itertools import groupby

from pypdf.generic import DictionaryObject, EncodedStreamObject


def compress_streams(writer):
    """Compress unfiltered streams, including ICC resources, without parsing them.

    pypdf has no public writer-wide stream replacement API. Preserve each
    indirect reference so page resources and annotation appearances keep working.
    Existing encoded image/font streams must retain their filters unchanged.
    """
    from pypdf.generic import StreamObject

    for obj in writer._objects:
        if isinstance(obj, StreamObject) and '/Filter' not in obj:
            # ContentStream may still store merge results as lazy operations;
            # flate_encode consumes its byte buffer, not those operations.
            obj.get_data()
            writer._replace_object(obj.indirect_reference, obj.flate_encode())


class _EncodedResource(EncodedStreamObject):
    """Deduplicate encoded bytes and filter parameters without decoding images."""

    def hash_value_data(self) -> bytes:
        return DictionaryObject.hash_value_data(self) + self._data


def deduplicate_objects(writer):
    """Keep pypdf deduplication from decoding JBIG2 and large image resources.

    Equality is deliberately conservative: identical compressed bytes and stream
    dictionaries deduplicate; differently encoded images remain distinct.
    """
    for obj in writer._objects:
        if isinstance(obj, EncodedStreamObject):
            resource = _EncodedResource()
            resource.update(obj)
            resource._data = obj._data
            writer._replace_object(obj.indirect_reference, resource)
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)


def append_windows(writer, composed, source_reader):
    """Read each contiguous window once and release it after cloning its pages."""
    from pypdf import PdfReader

    def window_path(index):
        entry = composed.get(index)
        return entry[0] if entry else None

    for path, indexes in groupby(range(1, len(source_reader.pages) + 1), window_path):
        if path is None:
            for index in indexes:
                writer.add_page(source_reader.pages[index - 1], excluded_keys=("/Annots",))
            continue
        with path.open('rb') as stream, PdfReader(stream) as reader:
            for index in indexes:
                writer.add_page(reader.pages[composed[index][1]])
            # All references now belong to writer. Its clone map otherwise
            # holds the source reader alive until the whole book is written.
            writer.reset_translation(reader)
