"""Persisted page-local furniture identities, including legacy PCEX input."""

from xml.etree.ElementTree import Element


def section_identity(section: Element) -> str:
    """New sections use an assigned ID; legacy sections retain their det key.

    Readers never renumber existing objects. Explicit IDs are positive decimal
    strings, so they cannot alias a legacy four-coordinate det string.
    """
    return section.get("id", section.get("det", ""))


def coverage_identity(entry: Element) -> str:
    """Read the same identity from a furniture coverage entry."""
    return entry.get("section_id", entry.get("det", ""))
