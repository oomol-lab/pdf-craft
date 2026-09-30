"""Synchronous compatibility facade for pdf-craft."""

from .craft import PDFCraft
from ..transformer import SyncAnchoredContentTransformer

__all__ = ["PDFCraft", "SyncAnchoredContentTransformer"]
