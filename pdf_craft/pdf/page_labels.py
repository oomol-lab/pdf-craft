"""Poppler's public GLib API supplies labels unavailable in pdfinfo's CLI."""
from __future__ import annotations

import asyncio
import ctypes
from collections.abc import Callable
import sys
from ctypes.util import find_library
from pathlib import Path


def read_page_labels(source: Path, count: int,
                     aborted: Callable[[], bool] = lambda: False) -> dict[int, str]:
    """Read labels only after preflight has confirmed an explicit /PageLabels tree.

    Poppler otherwise synthesizes decimal physical numbers. The presence bit
    is supplied by preflight's already-required PDF object traversal.
    """
    def load(name: str):
        aliases = {"poppler-glib": "libpoppler-glib-8", "glib-2.0": "libglib-2.0-0",
                   "gobject-2.0": "libgobject-2.0-0"}
        location = find_library(name) or find_library(aliases[name])
        if location is None and sys.platform == "darwin":
            location = next((str(path) for prefix in ("/opt/homebrew", "/usr/local", "/opt/local")
                             if (path := Path(prefix) / "lib" / f"lib{name}.dylib").exists()), None)
        if location is None:
            raise OSError(f"Poppler label library {name!r} not found; install libpoppler-glib8 (Linux) or Poppler (macOS)")
        return ctypes.CDLL(location)

    library = load("poppler-glib")
    glib = load("glib-2.0")
    gobject = load("gobject-2.0")
    pointer = ctypes.c_void_p
    library.poppler_document_new_from_file.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(pointer)]
    library.poppler_document_new_from_file.restype = pointer
    library.poppler_document_get_page.argtypes = [pointer, ctypes.c_int]
    library.poppler_document_get_page.restype = pointer
    library.poppler_page_get_label.argtypes = [pointer]
    library.poppler_page_get_label.restype = pointer
    gobject.g_object_unref.argtypes = [pointer]
    gobject.g_object_unref.restype = None
    glib.g_free.argtypes = [pointer]
    glib.g_free.restype = None
    glib.g_error_free.argtypes = [pointer]
    glib.g_error_free.restype = None
    error = pointer()
    document = library.poppler_document_new_from_file(source.resolve().as_uri().encode(), None, ctypes.byref(error))
    if not document:
        if error:
            glib.g_error_free(error)
        raise ValueError("Poppler could not open PDF to read publication page labels")
    labels = {}
    try:
        for index in range(count):
            if aborted():
                raise asyncio.CancelledError()
            page = library.poppler_document_get_page(document, index)
            if not page:
                raise ValueError(f"Poppler could not read label page {index + 1}")
            try:
                label = library.poppler_page_get_label(page)
                if label:
                    try:
                        value = ctypes.string_at(label).decode("utf-8").strip()
                        if value:
                            labels[index + 1] = value
                    finally:
                        glib.g_free(label)
            finally:
                gobject.g_object_unref(page)
    finally:
        gobject.g_object_unref(document)
    return labels
